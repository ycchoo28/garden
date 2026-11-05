# Approvals (core)

## What it owns

- Approval flows as data: a flow is a named list of steps, and each step applies above an amount.
- Runs: one run for one request to approve one subject.
  A resubmitted subject gets a new run.
- Decisions: one decision per step, so a step is decided once.
- Separation of duties: nobody decides their own request, and nobody decides two steps of one run.
- Schema: `approvals`. Tables: `flow`, `flow_step`, `run`, `run_step` and `decision`.

## Interface

Import from `app.core.approvals` only: `ApprovalService` and `get_approval_service`, which start a run, decide a step, withdraw a run and read its state.
It also exports the flow and workflow types (`Flow`, `FlowStep`, `Step`, `Outcome`, `RunState`, `Approver`) and the view types `RunView` and `DecisionView`.
The core has no routes: `router`, `ROLES` and `PERMISSIONS` are empty, and owning modules expose the decisions they need.

## Key decisions

- The workflow rules live in `workflow.py`, which has no database and no HTTP, so each rule is unit-tested alone.
- Steps are copied from the flow into the run when it starts, so a later change to a flow does not change a run in progress.
- The core tracks state only.
  The owning module owns its subject and decides what an approval means for it.
