"""Business rules of budgets. Endpoints call this, never the database."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Depends

from app.core.audit import AuditService, get_audit_service
from app.core.users import CurrentPerson, DepartmentView, UserService, get_user_service
from app.modules.budgets.models import BudgetLine
from app.modules.budgets.repository import BudgetRepository, get_repository
from app.modules.claims import ClaimsService, get_claims_service
from app.modules.spend import CATEGORIES, SpendService, get_spend_service, is_year_month
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, FieldError, Forbidden, NotFound, Unprocessable
from app.shared.ids import new_id
from app.shared.money import format_money, parse_money


@dataclass(frozen=True)
class LineView:
    line_id: uuid.UUID
    version: int
    department_id: uuid.UUID
    department_code: str
    department_name: str
    year_month: str
    category: str
    amount: Decimal
    reason: str | None
    created_at: datetime
    created_by: uuid.UUID
    current: bool


@dataclass(frozen=True)
class VarianceView:
    department_id: uuid.UUID
    department_code: str
    department_name: str
    year_month: str
    category: str
    budget: Decimal | None
    actual: Decimal
    variance: Decimal | None


def _amount(text: str, errors: list[FieldError]) -> Decimal:
    """Read a money amount. A bad or negative amount is recorded in `errors` and never saved."""
    try:
        value = parse_money(text)
    except ValueError as error:
        errors.append(FieldError(field="amount", message=str(error)))
        return Decimal("0.00")
    if value < 0:
        errors.append(FieldError(field="amount", message="A budget cannot be negative."))
    return value


def _department_code(departments: dict[uuid.UUID, DepartmentView], department_id: uuid.UUID) -> str:
    department = departments.get(department_id)
    return department.code if department is not None else "unknown"


class BudgetService:
    def __init__(
        self,
        repo: BudgetRepository,
        audit: AuditService,
        clock: Clock,
        users: UserService,
        spend: SpendService,
        claims: ClaimsService,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._clock = clock
        self._users = users
        self._spend = spend
        self._claims = claims

    async def _departments(self) -> dict[uuid.UUID, DepartmentView]:
        return {view.id: view for view in await self._users.departments()}

    @staticmethod
    def _view(line: BudgetLine, departments: dict[uuid.UUID, DepartmentView]) -> LineView:
        department = departments.get(line.department_id)
        return LineView(
            line_id=line.line_id,
            version=line.version,
            department_id=line.department_id,
            department_code=_department_code(departments, line.department_id),
            department_name=department.name if department is not None else "Unknown",
            year_month=line.year_month,
            category=line.category,
            amount=line.amount,
            reason=line.reason,
            created_at=line.created_at,
            created_by=line.created_by,
            current=line.superseded_at is None,
        )

    @staticmethod
    def _visible(
        caller: CurrentPerson, department_id: uuid.UUID | None
    ) -> frozenset[uuid.UUID] | None:
        """The departments the caller may see. None means every department."""
        if department_id is not None:
            return frozenset({department_id})
        if caller.scope.all_departments:
            return None
        return caller.scope.department_ids

    async def list_lines(
        self, caller: CurrentPerson, *, year_month: str, department_id: uuid.UUID | None
    ) -> list[LineView]:
        if department_id is not None and not caller.scope.covers(department_id):
            raise Forbidden("You can read only your own department's budget lines.")
        lines = await self._repo.current_lines(
            department_ids=self._visible(caller, department_id),
            first_month=year_month,
            last_month=year_month,
        )
        departments = await self._departments()
        return [self._view(line, departments) for line in lines]

    async def create_line(
        self,
        caller: CurrentPerson,
        *,
        department_id: uuid.UUID,
        year_month: str,
        category: str,
        amount: str,
    ) -> LineView:
        departments = await self._departments()
        errors: list[FieldError] = []
        if department_id not in departments:
            errors.append(FieldError(field="department_id", message="Unknown department."))
        if not is_year_month(year_month):
            errors.append(FieldError(field="year_month", message="Use a month such as 2026-11."))
        if category not in CATEGORIES:
            errors.append(
                FieldError(field="category", message=f"Use one of: {', '.join(CATEGORIES)}.")
            )
        value = _amount(amount, errors)
        if errors:
            raise Unprocessable("The budget line was not saved.", errors=errors)
        await self._repo.lock_lines()
        if await self._repo.current_key_exists(department_id, year_month, category):
            raise Conflict(
                "A budget line already exists for this department, month and category. "
                "Replace it with a reason instead."
            )
        line = BudgetLine(
            id=new_id(),
            line_id=new_id(),
            version=1,
            department_id=department_id,
            year_month=year_month,
            category=category,
            amount=value,
            reason=None,
            created_at=self._clock.now(),
            created_by=caller.id,
            superseded_at=None,
            superseded_by=None,
        )
        self._repo.add(line)
        self._audit.record(
            actor_id=caller.id,
            operation="budgets.line.create",
            target=f"budget_line:{line.line_id}",
            summary=(
                f"Set the {category} budget for {_department_code(departments, department_id)} "
                f"in {year_month} to {format_money(value)} USD."
            ),
        )
        await self._repo.commit()
        return self._view(line, departments)

    async def replace_line(
        self,
        caller: CurrentPerson,
        line_id: uuid.UUID,
        *,
        version: int,
        amount: str,
        reason: str,
    ) -> LineView:
        errors: list[FieldError] = []
        value = _amount(amount, errors)
        if errors:
            raise Unprocessable("The budget line was not replaced.", errors=errors)
        await self._repo.lock_lines()
        current = await self._repo.current_for_update(line_id)
        if current is None:
            raise NotFound("No current budget line has that identifier.")
        if current.version != version:
            raise Conflict(
                f"This budget line is now at version {current.version}. "
                "Read it again before you replace it."
            )
        now = self._clock.now()
        old_amount = current.amount
        old_version = current.version
        current.superseded_at = now
        current.superseded_by = caller.id
        # Write the superseded row before the replacement, so the current-key index never sees two.
        await self._repo.flush()
        replacement = BudgetLine(
            id=new_id(),
            line_id=line_id,
            version=old_version + 1,
            department_id=current.department_id,
            year_month=current.year_month,
            category=current.category,
            amount=value,
            reason=reason,
            created_at=now,
            created_by=caller.id,
            superseded_at=None,
            superseded_by=None,
        )
        self._repo.add(replacement)
        departments = await self._departments()
        self._audit.record(
            actor_id=caller.id,
            operation="budgets.line.replace",
            target=f"budget_line:{line_id}",
            summary=(
                f"Changed the {replacement.category} budget for "
                f"{_department_code(departments, replacement.department_id)} in "
                f"{replacement.year_month} from {format_money(old_amount)} to "
                f"{format_money(value)} USD: {reason}"
            ),
        )
        await self._repo.commit()
        return self._view(replacement, departments)

    async def history(self, caller: CurrentPerson, line_id: uuid.UUID) -> list[LineView]:
        rows = await self._repo.history(line_id)
        # A line outside the caller's departments reads as missing, so its existence stays hidden.
        if not rows or not caller.scope.covers(rows[0].department_id):
            raise NotFound("No budget line has that identifier.")
        departments = await self._departments()
        return [self._view(row, departments) for row in rows]

    async def _actual(
        self,
        *,
        first_month: str,
        last_month: str,
        scope: frozenset[uuid.UUID] | None,
    ) -> dict[tuple[uuid.UUID, str, str], Decimal]:
        """Card statement rows plus approved claim lines, per department, month and category."""
        actuals = dict(
            await self._spend.actual_spend(
                first_month=first_month, last_month=last_month, department_ids=scope
            )
        )
        for row in await self._claims.approved_spend(from_month=first_month, to_month=last_month):
            if scope is not None and row.department_id not in scope:
                continue
            key = (row.department_id, row.month, row.category)
            actuals[key] = actuals.get(key, Decimal("0.00")) + row.amount
        return actuals

    async def variance(
        self, caller: CurrentPerson, *, first_month: str, last_month: str
    ) -> list[VarianceView]:
        errors: list[FieldError] = []
        if not is_year_month(first_month):
            errors.append(FieldError(field="from", message="Use a month such as 2026-01."))
        if not is_year_month(last_month):
            errors.append(FieldError(field="to", message="Use a month such as 2026-12."))
        if not errors and first_month > last_month:
            errors.append(FieldError(field="from", message="From must not be after to."))
        if errors:
            raise Unprocessable("The month range is not valid.", errors=errors)
        scope = self._visible(caller, None)
        lines = await self._repo.current_lines(
            department_ids=scope, first_month=first_month, last_month=last_month
        )
        budgets = {
            (line.department_id, line.year_month, line.category): line.amount for line in lines
        }
        actuals = await self._actual(first_month=first_month, last_month=last_month, scope=scope)
        departments = await self._departments()
        keys = sorted(
            set(budgets) | set(actuals),
            key=lambda key: (_department_code(departments, key[0]), key[1], key[2]),
        )
        views: list[VarianceView] = []
        for department_id, year_month, category in keys:
            key = (department_id, year_month, category)
            budget = budgets.get(key)
            actual = actuals.get(key, Decimal("0.00"))
            department = departments.get(department_id)
            views.append(
                VarianceView(
                    department_id=department_id,
                    department_code=_department_code(departments, department_id),
                    department_name=department.name if department is not None else "Unknown",
                    year_month=year_month,
                    category=category,
                    budget=budget,
                    actual=actual,
                    variance=None if budget is None else budget - actual,
                )
            )
        return views


def get_budget_service(
    repo: Annotated[BudgetRepository, Depends(get_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    clock: Annotated[Clock, Depends(get_clock)],
    users: Annotated[UserService, Depends(get_user_service)],
    spend: Annotated[SpendService, Depends(get_spend_service)],
    claims: Annotated[ClaimsService, Depends(get_claims_service)],
) -> BudgetService:
    return BudgetService(repo, audit, clock, users, spend, claims)
