"""Business rules of card statements and closed months. Endpoints call this, never the database."""

import hashlib
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated

from fastapi import Depends

from app.core.audit import AuditService, get_audit_service
from app.core.users import UserService, get_user_service
from app.modules.spend.models import MonthClose, Statement, StatementRow
from app.modules.spend.repository import SpendRepository, get_repository
from app.modules.spend.statement_csv import Check, FileRejected, candidate_refs, check
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, FieldError, NotFound, Unprocessable
from app.shared.ids import new_id
from app.shared.money import format_money

DEFAULT_SOURCE_NAME = "statement.csv"


def _field_errors(checked: Check) -> list[FieldError]:
    """Every row error and duplicate, as problem+json field errors such as rows[3].amount_usd."""
    issues = sorted((*checked.errors, *checked.duplicates), key=lambda issue: issue.row)
    return [
        FieldError(field=f"rows[{issue.row}].{issue.field}", message=issue.message)
        for issue in issues
    ]


class SpendService:
    def __init__(
        self,
        repo: SpendRepository,
        audit: AuditService,
        clock: Clock,
        users: UserService,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._clock = clock
        self._users = users

    async def _check(self, data: bytes) -> Check:
        try:
            existing = await self._repo.existing_refs(candidate_refs(data))
            departments = {view.code: view.id for view in await self._users.departments()}
            closed = await self._repo.closed_months()
            return check(data, departments=departments, closed_months=closed, existing=existing)
        except FileRejected as error:
            raise Unprocessable(error.message) from error

    async def preview(self, data: bytes) -> Check:
        """The dry run. It reads the file and the saved references, and writes nothing."""
        return await self._check(data)

    async def import_statement(
        self, *, data: bytes, source_name: str, actor_id: uuid.UUID
    ) -> Statement:
        """Save every row of the statement, or none of them when a row is an error or duplicate."""
        await self._repo.lock_ledger()
        checked = await self._check(data)
        if not checked.can_apply:
            raise Unprocessable(
                "The statement was not saved. Fix every row listed here and upload it again.",
                errors=_field_errors(checked),
            )
        name = source_name.strip()[:255] or DEFAULT_SOURCE_NAME
        statement = Statement(
            id=new_id(),
            source_name=name,
            source_sha256=hashlib.sha256(data).hexdigest(),
            row_count=len(checked.rows),
            total_amount=checked.total_amount,
            imported_at=self._clock.now(),
            imported_by=actor_id,
            voided_at=None,
            voided_by=None,
            void_reason=None,
        )
        self._repo.add(statement)
        self._repo.add_rows(
            [
                StatementRow(
                    id=new_id(),
                    statement_id=statement.id,
                    line_no=row.row,
                    transaction_ref=row.transaction_ref,
                    posted_on=row.posted_on,
                    year_month=row.year_month,
                    department_id=row.department_id,
                    category=row.category,
                    merchant=row.merchant,
                    amount=row.amount,
                )
                for row in checked.rows
            ]
        )
        self._audit.record(
            actor_id=actor_id,
            operation="spend.statement.import",
            target=f"statement:{statement.id}",
            summary=(
                f"Imported {name}: {len(checked.rows)} rows totalling "
                f"{format_money(checked.total_amount)} USD."
            ),
        )
        await self._repo.commit()
        return statement

    async def statements(self) -> Sequence[Statement]:
        return await self._repo.statements()

    async def void_statement(
        self, statement_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID
    ) -> Statement:
        """Stop a statement counting as spend. Its rows stay, with the reason and who voided it."""
        await self._repo.lock_ledger()
        statement = await self._repo.statement(statement_id, for_update=True)
        if statement is None:
            raise NotFound("No card statement has that identifier.")
        if statement.voided_at is not None:
            raise Conflict("This statement is already voided.")
        touched = await self._repo.statement_months(statement_id)
        already_closed = await self._repo.closed_months()
        closed = sorted(touched & already_closed)
        if closed:
            raise Conflict(
                f"This statement touches closed month {', '.join(closed)}, so it cannot be voided."
            )
        statement.voided_at = self._clock.now()
        statement.voided_by = actor_id
        statement.void_reason = reason
        self._audit.record(
            actor_id=actor_id,
            operation="spend.statement.void",
            target=f"statement:{statement.id}",
            summary=f"Voided {statement.source_name}: {reason}",
        )
        await self._repo.commit()
        return statement

    async def closed_months(self) -> Sequence[MonthClose]:
        return await self._repo.month_closes()

    async def is_month_closed(self, year_month: str) -> bool:
        return await self._repo.month_close(year_month) is not None

    async def close_month(self, year_month: str, *, reason: str, actor_id: uuid.UUID) -> MonthClose:
        """Close a month. No spend for it can be imported or voided afterwards."""
        await self._repo.lock_ledger()
        if await self._repo.month_close(year_month) is not None:
            raise Conflict(f"{year_month} is already closed.")
        close = MonthClose(
            year_month=year_month,
            closed_at=self._clock.now(),
            closed_by=actor_id,
            reason=reason,
        )
        self._repo.add(close)
        self._audit.record(
            actor_id=actor_id,
            operation="spend.month.close",
            target=f"month:{year_month}",
            summary=f"Closed {year_month}: {reason}",
        )
        await self._repo.commit()
        return close

    async def actual_spend(
        self,
        *,
        first_month: str,
        last_month: str,
        department_ids: frozenset[uuid.UUID] | None,
    ) -> dict[tuple[uuid.UUID, str, str], Decimal]:
        """Actual spend per (department id, month, category) from statements that are not voided."""
        return await self._repo.actual_spend(
            first_month=first_month,
            last_month=last_month,
            department_ids=department_ids,
        )


def get_spend_service(
    repo: Annotated[SpendRepository, Depends(get_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    clock: Annotated[Clock, Depends(get_clock)],
    users: Annotated[UserService, Depends(get_user_service)],
) -> SpendService:
    return SpendService(repo, audit, clock, users)
