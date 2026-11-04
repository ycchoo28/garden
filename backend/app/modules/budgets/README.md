# Budgets (module)

## What it owns

- Budget lines: one amount per department, month and category, with every replaced version kept.
- Schema: `budgets`. Tables: `line`.
- It reads actual spend from `spend` through that module's interface, and does not own spend.

## Interface

Import from `app.modules.budgets` only: `router`, `ROLES` and `PERMISSIONS`.

## Key decisions

- A replacement is a new version with a reason, and the old version stays in history (ADR 0005).
- Each replacement carries the version the caller read, and a stale version returns 409 (ADR 0007).
- Creates and replacements take the `budgets.lines` advisory lock, so two writers never race on one budget key.
- Managers read only their own department's lines; a request for another department is refused with 403.
