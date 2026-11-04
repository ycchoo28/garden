# 0016. Approval flows as data

Status: Accepted, 2026-11-04.

## Context

Claims need approval now, and expenses, leave and purchases will need it later.
Each module that needs a decision would otherwise write its own steps, its own thresholds and its own decision history.

## Decision

The approvals core holds flows as rows: a flow has ordered steps, and each step names a role, an amount above which it applies, and whether it is scoped to the subject's department.
A module starts a run for its subject, which copies the applicable steps into the run, and then reads the run's state.
Steps and decisions are append-only: a decision is recorded with who, when and a reason, and a cancellation is recorded as a timestamp.
The run's state is computed from its steps and decisions, and nothing stores a status flag.

## Consequences

- A threshold or a new step is a data change in the seeded flow, not a code change.
- Each run keeps the steps it started with, so changing a flow later does not rewrite the history of runs already in progress.
- The owning module decides what an approval means for its subject, and writes its own audit rows.
- The approvals core is a shared public interface, so a second module can use it without importing the claims module.
