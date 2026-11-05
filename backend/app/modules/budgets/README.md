# Budgets (module)

## What it owns

- Budget lines: one amount per department, month and category, with every replaced version kept.
- Month close: the closing of one year-month with a reason.
  A closed month never reopens.
- Variance: the budget minus the actual spend for a range of months.
- Schema: `budgets`. Tables: `line` (owned by `lines`) and `month_close` (owned by `months`). `variance` owns no table.
- It reads approved claim lines from `claims` through that module's public interface, and it owns neither those lines nor the claims.

## Features

- `lines/`: budget lines, their history and the category list. Repository: `lines/repository.py`.
- `months/`: month close and the closed-months port that claims uses. Repository: `months/repository.py`.
- `variance/`: the variance report. It reads lines through the lines service and approved spend through `ClaimsService`.
- `api.py` at the module root mounts the three feature routers under `/api/budgets`.

## Interface

Import from `app.modules.budgets` only: `router`, `ROLES` and `PERMISSIONS`.
Its month routes are `/api/budgets/months` and `/api/budgets/months/{year_month}/close`, and the close needs the `budgets.month.close` permission.
Claims reads whether a month is closed through a port that this module provides (`months/ports.py`).

## Key decisions

- A replacement is a new version with a reason, and the old version stays in history (ADR 0005).
- Each replacement carries the version the caller read, and a stale version returns 409 (ADR 0007).
- Creates and replacements take the `budgets.lines` advisory lock, so two writers never race on one budget key.
- Managers read only their own department's lines; a request for another department is refused with 403.
- Month close is recorded with a reason and never reverts (ADR 0019).
  Claims refuses to add, void or submit a claim line dated in a closed month.
- Variance is the budget minus the actual spend, and the actual spend is approved claim lines only.
- The categories and the `YYYY-MM` month format are shared with claims.
