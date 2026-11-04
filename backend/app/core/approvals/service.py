"""Approval flows as data: starting a run, deciding a step, withdrawing a run and reading its state.

Other modules use this through the public interface in `__init__.py`. The service records facts
only. It does not write audit rows itself, because the module that owns the subject records its own
change in the same transaction, and the caller commits.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Depends

from app.core.approvals.models import Decision, Run, RunStep
from app.core.approvals.repository import ApprovalRepository, get_approval_repository
from app.core.approvals.workflow import (
    Approver,
    Decided,
    Outcome,
    RunState,
    Step,
    current_step,
    refusal_for,
    state_of,
    steps_for,
)
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.shared.ids import new_id


@dataclass(frozen=True)
class DecisionView:
    position: int
    outcome: Outcome
    decided_by: uuid.UUID
    decided_by_name: str
    decided_at: datetime
    reason: str | None


@dataclass(frozen=True)
class RunView:
    id: uuid.UUID
    subject_type: str
    subject_id: uuid.UUID
    subject_department_id: uuid.UUID
    flow_code: str
    amount: Decimal
    started_by: uuid.UUID
    started_by_name: str
    started_at: datetime
    cancelled_at: datetime | None
    cancel_reason: str | None
    state: RunState
    steps: tuple[Step, ...]
    decisions: tuple[DecisionView, ...]

    @property
    def current(self) -> Step | None:
        return current_step(self.steps, self._decided())

    @property
    def deciders(self) -> frozenset[uuid.UUID]:
        return frozenset(decision.decided_by for decision in self.decisions)

    def _decided(self) -> tuple[Decided, ...]:
        return tuple(Decided(decision.position, decision.outcome) for decision in self.decisions)


class ApprovalService:
    def __init__(self, repo: ApprovalRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    async def start(
        self,
        *,
        flow_code: str,
        subject_type: str,
        subject_id: uuid.UUID,
        subject_department_id: uuid.UUID,
        amount: Decimal,
        started_by: uuid.UUID,
        started_by_name: str,
    ) -> uuid.UUID:
        """Start a run for the subject. Its steps are copied from the flow for this amount."""
        if await self._repo.flow(flow_code) is None:
            raise NotFound(f"No approval flow named {flow_code} is set up.")
        rows = await self._repo.flow_steps(flow_code)
        steps = steps_for(
            (Step(row.position, row.role, row.department_scoped, row.above) for row in rows),
            amount,
        )
        if not steps:
            raise Unprocessable("No approval step applies to this amount.")
        run = Run(
            id=new_id(),
            flow_code=flow_code,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_department_id=subject_department_id,
            amount=amount,
            started_by=started_by,
            started_by_name=started_by_name,
            started_at=self._clock.now(),
            cancelled_at=None,
            cancelled_by=None,
            cancel_reason=None,
        )
        self._repo.add(run)
        for step in steps:
            self._repo.add(
                RunStep(
                    id=new_id(),
                    run_id=run.id,
                    position=step.position,
                    role=step.role,
                    department_scoped=step.department_scoped,
                    above=step.above,
                )
            )
        await self._repo.flush()
        return run.id

    async def decide(
        self,
        *,
        run_id: uuid.UUID,
        approver: Approver,
        approver_name: str,
        outcome: Outcome,
        reason: str | None,
    ) -> RunView:
        """Record the decision on the run's current step, after the separation rules pass."""
        view = await self._view_of_id(run_id)
        if view.state is not RunState.PENDING or view.current is None:
            raise Conflict("This request is no longer waiting for a decision.")
        step = view.current
        refusal = refusal_for(
            step,
            approver,
            started_by=view.started_by,
            subject_department_id=view.subject_department_id,
            earlier_deciders=view.deciders,
        )
        if refusal is not None:
            raise Forbidden(refusal)
        if outcome is Outcome.REJECTED and not (reason and reason.strip()):
            raise Unprocessable("A rejection needs a reason.")
        self._repo.add(
            Decision(
                id=new_id(),
                run_id=run_id,
                position=step.position,
                outcome=outcome.value,
                decided_by=approver.id,
                decided_by_name=approver_name,
                decided_at=self._clock.now(),
                reason=reason.strip() if reason else None,
            )
        )
        await self._repo.flush()
        return await self._view_of_id(run_id)

    async def cancel(
        self, *, run_id: uuid.UUID, actor_id: uuid.UUID, reason: str | None
    ) -> RunView:
        """Withdraw a run that is still waiting. Only the person who started it may withdraw it."""
        view = await self._view_of_id(run_id)
        if view.started_by != actor_id:
            raise Forbidden("Only the person who started this request can withdraw it.")
        if view.state is not RunState.PENDING:
            raise Conflict("This request has already been decided or withdrawn.")
        run = await self._repo.run(run_id)
        if run is None:
            raise NotFound("No request has that identifier.")
        run.cancelled_at = self._clock.now()
        run.cancelled_by = actor_id
        run.cancel_reason = reason
        await self._repo.flush()
        return await self._view_of_id(run_id)

    async def latest(
        self, subject_type: str, subject_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, RunView]:
        """The newest run for each subject that has one."""
        latest: dict[uuid.UUID, RunView] = {}
        for view in await self._views(await self._repo.runs(subject_type, subject_ids)):
            _ = latest.setdefault(view.subject_id, view)
        return latest

    async def history(self, subject_type: str, subject_id: uuid.UUID) -> list[RunView]:
        """Every run of one subject, newest first. Earlier decisions stay visible here."""
        return await self._views(await self._repo.runs(subject_type, [subject_id]))

    async def pending_for(self, approver: Approver, subject_type: str) -> list[RunView]:
        """The pending runs whose current step this person may decide right now."""
        waiting: list[RunView] = []
        for view in await self._views(await self._repo.runs(subject_type, None)):
            step = view.current
            if view.state is not RunState.PENDING or step is None:
                continue
            if refusal_for(
                step,
                approver,
                started_by=view.started_by,
                subject_department_id=view.subject_department_id,
                earlier_deciders=view.deciders,
            ):
                continue
            waiting.append(view)
        return waiting

    async def _view_of_id(self, run_id: uuid.UUID) -> RunView:
        run = await self._repo.run(run_id)
        if run is None:
            raise NotFound("No request has that identifier.")
        return (await self._views([run]))[0]

    async def _views(self, rows: Sequence[Run]) -> list[RunView]:
        """Build views for many runs with two queries, not one per run."""
        ids = [run.id for run in rows]
        steps_by_run: dict[uuid.UUID, list[Step]] = {}
        for step_row in await self._repo.run_steps(ids):
            steps_by_run.setdefault(step_row.run_id, []).append(
                Step(step_row.position, step_row.role, step_row.department_scoped, step_row.above)
            )
        decisions_by_run: dict[uuid.UUID, list[DecisionView]] = {}
        for decision in await self._repo.decisions(ids):
            decisions_by_run.setdefault(decision.run_id, []).append(
                DecisionView(
                    position=decision.position,
                    outcome=Outcome(decision.outcome),
                    decided_by=decision.decided_by,
                    decided_by_name=decision.decided_by_name,
                    decided_at=decision.decided_at,
                    reason=decision.reason,
                )
            )
        return [
            self._view(run, steps_by_run.get(run.id, []), decisions_by_run.get(run.id, []))
            for run in rows
        ]

    def _view(self, run: Run, steps: list[Step], decisions: list[DecisionView]) -> RunView:
        decided = [Decided(decision.position, decision.outcome) for decision in decisions]
        ordered_steps = tuple(sorted(steps, key=lambda step: step.position))
        return RunView(
            id=run.id,
            subject_type=run.subject_type,
            subject_id=run.subject_id,
            subject_department_id=run.subject_department_id,
            flow_code=run.flow_code,
            amount=run.amount,
            started_by=run.started_by,
            started_by_name=run.started_by_name,
            started_at=run.started_at,
            cancelled_at=run.cancelled_at,
            cancel_reason=run.cancel_reason,
            state=state_of(ordered_steps, decided, cancelled=run.cancelled_at is not None),
            steps=ordered_steps,
            decisions=tuple(decisions),
        )


def get_approval_service(
    repo: Annotated[ApprovalRepository, Depends(get_approval_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ApprovalService:
    return ApprovalService(repo, clock)
