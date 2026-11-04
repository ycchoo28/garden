"""Claim business rules: drafting, lines, submission, withdrawal, decisions and visibility.

Endpoints call this, never the database. Every change records an audit row in the same transaction
and moves the claim's version on, so a stale writer gets a 409 instead of overwriting.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Depends

from app.core.approvals import (
    ApprovalService,
    Approver,
    DecisionView,
    Outcome,
    RunState,
    RunView,
    get_approval_service,
)
from app.core.audit import AuditService, get_audit_service
from app.core.files import FileService, get_file_service
from app.core.users import CurrentPerson
from app.modules.claims.flows import CLAIM_FLOW_CODE, SUBJECT_TYPE
from app.modules.claims.models import Claim, Line
from app.modules.claims.ports import ClosedMonths, get_closed_months
from app.modules.claims.repository import ClaimRepository, get_claim_repository
from app.modules.claims.rules import (
    CATEGORIES,
    EDITABLE,
    SUBMITTABLE,
    ClaimStatus,
    month_of,
    status_of,
)
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.shared.ids import new_id


@dataclass(frozen=True)
class ClaimSummary:
    id: uuid.UUID
    title: str
    claimant_id: uuid.UUID
    claimant_name: str
    department_id: uuid.UUID
    department_code: str
    status: ClaimStatus
    total: Decimal
    version: int
    created_at: datetime


@dataclass(frozen=True)
class LineView:
    id: uuid.UUID
    category: str
    spent_on: date
    amount: Decimal
    description: str
    receipt_sha256: str


@dataclass(frozen=True)
class StepRecord:
    position: int
    role: str
    decision: DecisionView | None


@dataclass(frozen=True)
class RunRecord:
    """One submission of the claim, with the decision on each of its steps."""

    id: uuid.UUID
    state: RunState
    started_at: datetime
    started_by_name: str
    cancelled_at: datetime | None
    cancel_reason: str | None
    steps: tuple[StepRecord, ...]


@dataclass(frozen=True)
class ClaimDetail:
    summary: ClaimSummary
    lines: tuple[LineView, ...]
    history: tuple[RunRecord, ...]
    can_edit: bool
    can_submit: bool
    can_withdraw: bool
    can_decide: bool


@dataclass(frozen=True)
class SpendRow:
    """Approved spend for one department, month and category."""

    department_id: uuid.UUID
    month: str
    category: str
    amount: Decimal


class ClaimsService:
    def __init__(
        self,
        repo: ClaimRepository,
        approvals: ApprovalService,
        audit: AuditService,
        files: FileService,
        closed: ClosedMonths,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._approvals = approvals
        self._audit = audit
        self._files = files
        self._closed = closed
        self._clock = clock

    # Reads

    async def list_visible(self, caller: CurrentPerson) -> list[ClaimSummary]:
        departments = None if caller.scope.all_departments else caller.scope.department_ids
        claims = await self._repo.visible_claims(caller.id, departments)
        return await self._summaries(claims)

    async def queue(self, caller: CurrentPerson) -> list[ClaimSummary]:
        """Claims waiting for a decision that this person may make now."""
        approver = _approver(caller)
        waiting = await self._approvals.pending_for(approver, SUBJECT_TYPE)
        claims = await self._repo.claims_by_ids([view.subject_id for view in waiting])
        return await self._summaries(claims)

    async def detail(self, caller: CurrentPerson, claim_id: uuid.UUID) -> ClaimDetail:
        claim = await self._visible(caller, claim_id)
        return await self._detail(caller, claim)

    async def approved_spend(self, *, from_month: str, to_month: str) -> list[SpendRow]:
        """Actual spend from approved claims, by department, month and category.

        This is the claims module's public read for the budgets module. Months are YYYY-MM and both
        ends are included.
        """
        claims = await self._repo.all_claims()
        latest = await self._approvals.latest(SUBJECT_TYPE, [claim.id for claim in claims])
        approved = {
            claim.id: claim
            for claim in claims
            if (run := latest.get(claim.id)) is not None and run.state is RunState.APPROVED
        }
        lines = await self._repo.lines_for(list(approved))
        totals: dict[tuple[uuid.UUID, str, str], Decimal] = defaultdict(lambda: Decimal("0.00"))
        for line in lines:
            if line.voided_at is not None:
                continue
            month = month_of(line.spent_on)
            if not from_month <= month <= to_month:
                continue
            department_id = approved[line.claim_id].department_id
            totals[(department_id, month, line.category)] += line.amount
        return [
            SpendRow(department_id=key[0], month=key[1], category=key[2], amount=amount)
            for key, amount in sorted(totals.items())
        ]

    # Changes

    async def create(self, caller: CurrentPerson, title: str) -> ClaimDetail:
        clean = title.strip()
        if not clean:
            raise Unprocessable("Give the claim a title.")
        claim = Claim(
            id=new_id(),
            claimant_id=caller.id,
            claimant_name=caller.name,
            department_id=caller.department_id,
            department_code=caller.department_code,
            title=clean,
            created_at=self._clock.now(),
            version=1,
        )
        self._repo.add(claim)
        self._audit.record(
            actor_id=caller.id,
            operation="claims.claim.create",
            target=f"claim:{claim.id}",
            summary=f'Created claim "{clean}".',
        )
        await self._repo.commit()
        return await self._detail(caller, claim)

    async def add_line(
        self,
        caller: CurrentPerson,
        claim_id: uuid.UUID,
        *,
        expected_version: int,
        category: str,
        spent_on: date,
        amount: Decimal,
        description: str,
        receipt_sha256: str,
    ) -> ClaimDetail:
        if category not in CATEGORIES:
            raise Unprocessable("Choose a category from the list.")
        claim = await self._changeable(caller, claim_id, expected_version)
        await self._require_status(claim, EDITABLE, "This claim can no longer change.")
        await self._require_open_months({month_of(spent_on)})
        if not await self._files.exists(receipt_sha256):
            raise Unprocessable("Upload the receipt first, then add the line.")
        description_clean = description.strip()
        if not description_clean:
            raise Unprocessable("Describe what the expense was for.")
        line = Line(
            id=new_id(),
            claim_id=claim.id,
            category=category,
            spent_on=spent_on,
            amount=amount,
            description=description_clean,
            receipt_sha256=receipt_sha256,
            added_by=caller.id,
            added_at=self._clock.now(),
            voided_by=None,
            voided_at=None,
            void_reason=None,
        )
        self._repo.add(line)
        self._audit.record(
            actor_id=caller.id,
            operation="claims.line.add",
            target=f"claim:{claim.id}",
            summary=f"Added {category} line of {amount} on {spent_on.isoformat()}.",
        )
        await self._repo.commit()
        return await self._detail(caller, claim)

    async def void_line(
        self,
        caller: CurrentPerson,
        claim_id: uuid.UUID,
        line_id: uuid.UUID,
        *,
        expected_version: int,
        reason: str | None,
    ) -> ClaimDetail:
        claim = await self._changeable(caller, claim_id, expected_version)
        await self._require_status(claim, EDITABLE, "This claim can no longer change.")
        line = await self._repo.line(line_id)
        if line is None or line.claim_id != claim.id or line.voided_at is not None:
            raise NotFound("No line on this claim has that identifier.")
        await self._require_open_months({month_of(line.spent_on)})
        line.voided_at = self._clock.now()
        line.voided_by = caller.id
        line.void_reason = reason.strip() if reason and reason.strip() else None
        self._audit.record(
            actor_id=caller.id,
            operation="claims.line.void",
            target=f"claim:{claim.id}",
            summary=f"Voided {line.category} line of {line.amount}.",
        )
        await self._repo.commit()
        return await self._detail(caller, claim)

    async def submit(
        self, caller: CurrentPerson, claim_id: uuid.UUID, *, expected_version: int
    ) -> ClaimDetail:
        claim = await self._changeable(caller, claim_id, expected_version)
        await self._require_status(claim, SUBMITTABLE, "This claim is already submitted.")
        lines = await self._active_lines(claim.id)
        if not lines:
            raise Unprocessable("Add at least one line before you submit.")
        await self._require_open_months({month_of(line.spent_on) for line in lines})
        total = sum((line.amount for line in lines), Decimal("0.00"))
        _ = await self._approvals.start(
            flow_code=CLAIM_FLOW_CODE,
            subject_type=SUBJECT_TYPE,
            subject_id=claim.id,
            subject_department_id=claim.department_id,
            amount=total,
            started_by=caller.id,
            started_by_name=caller.name,
        )
        self._audit.record(
            actor_id=caller.id,
            operation="claims.claim.submit",
            target=f"claim:{claim.id}",
            summary=f"Certified and submitted for {total}.",
        )
        await self._repo.commit()
        return await self._detail(caller, claim)

    async def withdraw(
        self, caller: CurrentPerson, claim_id: uuid.UUID, *, expected_version: int
    ) -> ClaimDetail:
        claim = await self._changeable(caller, claim_id, expected_version)
        latest = (await self._approvals.latest(SUBJECT_TYPE, [claim.id])).get(claim.id)
        if latest is None or latest.state is not RunState.PENDING:
            raise Conflict("Only a submitted claim that nobody has decided can be withdrawn.")
        _ = await self._approvals.cancel(run_id=latest.id, actor_id=caller.id, reason=None)
        self._audit.record(
            actor_id=caller.id,
            operation="claims.claim.withdraw",
            target=f"claim:{claim.id}",
            summary="Withdrew the submitted claim.",
        )
        await self._repo.commit()
        return await self._detail(caller, claim)

    async def decide(
        self,
        caller: CurrentPerson,
        claim_id: uuid.UUID,
        *,
        expected_version: int,
        outcome: Outcome,
        reason: str | None,
    ) -> ClaimDetail:
        claim = await self._visible(caller, claim_id)
        if claim.claimant_id == caller.id:
            raise Forbidden("Nobody can approve or reject their own claim.")
        if not await self._repo.bump(claim.id, expected_version):
            raise Conflict("This claim changed since you loaded it. Reload it and try again.")
        claim = await self._reload(claim.id)
        latest = (await self._approvals.latest(SUBJECT_TYPE, [claim.id])).get(claim.id)
        if latest is None or latest.current is None or latest.state is not RunState.PENDING:
            raise Conflict("This claim is not waiting for a decision.")
        step_role = latest.current.role
        _ = await self._approvals.decide(
            run_id=latest.id,
            approver=_approver(caller),
            approver_name=caller.name,
            outcome=outcome,
            reason=reason,
        )
        self._audit.record(
            actor_id=caller.id,
            operation="claims.claim.decide",
            target=f"claim:{claim.id}",
            summary=f"{outcome.value.capitalize()} the claim as {step_role}.",
        )
        await self._repo.commit()
        return await self._detail(caller, claim)

    # Helpers

    async def _reload(self, claim_id: uuid.UUID) -> Claim:
        claim = await self._repo.claim(claim_id)
        if claim is None:
            raise NotFound("No claim has that identifier.")
        return claim

    async def _visible(self, caller: CurrentPerson, claim_id: uuid.UUID) -> Claim:
        """The claim, when the caller may see it. Otherwise it looks like it does not exist."""
        claim = await self._repo.claim(claim_id)
        if claim is None:
            raise NotFound("No claim has that identifier.")
        if claim.claimant_id != caller.id and not caller.scope.covers(claim.department_id):
            raise NotFound("No claim has that identifier.")
        return claim

    async def _changeable(
        self, caller: CurrentPerson, claim_id: uuid.UUID, expected_version: int
    ) -> Claim:
        """Only the claimant changes a claim. The version check takes the row lock, then reloads."""
        claim = await self._visible(caller, claim_id)
        if claim.claimant_id != caller.id:
            raise Forbidden("Only the claimant can change this claim.")
        if not await self._repo.bump(claim.id, expected_version):
            raise Conflict("This claim changed since you loaded it. Reload it and try again.")
        return await self._reload(claim.id)

    async def _status(self, claim: Claim) -> ClaimStatus:
        latest = (await self._approvals.latest(SUBJECT_TYPE, [claim.id])).get(claim.id)
        return status_of(None if latest is None else latest.state)

    async def _require_status(
        self, claim: Claim, allowed: frozenset[ClaimStatus], message: str
    ) -> None:
        if await self._status(claim) not in allowed:
            raise Conflict(message)

    async def _require_open_months(self, months: set[str]) -> None:
        closed = await self._closed.closed_among(frozenset(months))
        if closed:
            names = ", ".join(sorted(closed))
            raise Unprocessable(f"Spend in {names} is closed, so this claim cannot change it.")

    async def _active_lines(self, claim_id: uuid.UUID) -> list[Line]:
        rows = await self._repo.lines_for([claim_id])
        return [row for row in rows if row.voided_at is None]

    async def _summaries(self, claims: Sequence[Claim]) -> list[ClaimSummary]:
        latest = await self._approvals.latest(SUBJECT_TYPE, [claim.id for claim in claims])
        lines = await self._repo.lines_for([claim.id for claim in claims])
        return [_summary(claim, latest.get(claim.id), _total(lines, claim.id)) for claim in claims]

    async def _detail(self, caller: CurrentPerson, claim: Claim) -> ClaimDetail:
        all_lines = await self._repo.lines_for([claim.id])
        lines = [row for row in all_lines if row.voided_at is None]
        history = await self._approvals.history(SUBJECT_TYPE, claim.id)
        latest = history[0] if history else None
        status = status_of(None if latest is None else latest.state)
        is_claimant = claim.claimant_id == caller.id
        pending_here = False
        if latest is not None and latest.state is RunState.PENDING:
            waiting = await self._approvals.pending_for(_approver(caller), SUBJECT_TYPE)
            pending_here = any(view.id == latest.id for view in waiting)
        return ClaimDetail(
            summary=_summary(claim, latest, _total(all_lines, claim.id)),
            lines=tuple(
                LineView(
                    id=line.id,
                    category=line.category,
                    spent_on=line.spent_on,
                    amount=line.amount,
                    description=line.description,
                    receipt_sha256=line.receipt_sha256,
                )
                for line in lines
            ),
            history=tuple(_record(view) for view in history),
            can_edit=is_claimant and status in EDITABLE,
            can_submit=is_claimant and status in SUBMITTABLE and bool(lines),
            can_withdraw=is_claimant and latest is not None and latest.state is RunState.PENDING,
            can_decide=pending_here,
        )


def _approver(caller: CurrentPerson) -> Approver:
    return Approver(id=caller.id, roles=caller.roles, department_id=caller.department_id)


def _total(lines: Sequence[Line], claim_id: uuid.UUID) -> Decimal:
    return sum(
        (row.amount for row in lines if row.claim_id == claim_id and row.voided_at is None),
        Decimal("0.00"),
    )


def _summary(claim: Claim, latest: RunView | None, total: Decimal) -> ClaimSummary:
    return ClaimSummary(
        id=claim.id,
        title=claim.title,
        claimant_id=claim.claimant_id,
        claimant_name=claim.claimant_name,
        department_id=claim.department_id,
        department_code=claim.department_code,
        status=status_of(None if latest is None else latest.state),
        total=total,
        version=claim.version,
        created_at=claim.created_at,
    )


def _record(view: RunView) -> RunRecord:
    decisions = {decision.position: decision for decision in view.decisions}
    return RunRecord(
        id=view.id,
        state=view.state,
        started_at=view.started_at,
        started_by_name=view.started_by_name,
        cancelled_at=view.cancelled_at,
        cancel_reason=view.cancel_reason,
        steps=tuple(
            StepRecord(
                position=step.position, role=step.role, decision=decisions.get(step.position)
            )
            for step in view.steps
        ),
    )


def get_claims_service(
    repo: Annotated[ClaimRepository, Depends(get_claim_repository)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    files: Annotated[FileService, Depends(get_file_service)],
    closed: Annotated[ClosedMonths, Depends(get_closed_months)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ClaimsService:
    return ClaimsService(repo, approvals, audit, files, closed, clock)
