# Budgets (module)

## What it owns

- Budget lines: one amount per department, month and category, with every replaced version kept.
- Month close: the closing of one year-month with a reason.
  A closed month never reopens.
- Variance: the budget minus the actual spend for a range of months.
- Schema: `budgets`. Tables: `line` (owned by `lines`) and `month_close` (owned by `months`). `variance` owns no table.
- Variance reads the documented Claims and Approvals tables below, and does not own or write them.

## Features

- `lines/`: budget lines, their history and the category list. Repository: `lines/repository.py`.
- `months/`: month close and the closed-months port that claims uses. Repository: `months/repository.py`.
- `variance/`: the variance report. Its repository reads current Budget lines and scoped approved Claim spend in one query.
- `api.py` at the module root mounts the three feature routers under `/api/budgets`.

## Interface

Import from `app.modules.budgets` only: `router`, `ROLES`, `PERMISSIONS`, `CATEGORIES` and `get_closed_months_for_claims`.
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
- Budgets owns the fixed `CATEGORIES` contract used to validate Budget and Claim lines.
  `budgets.category.read` exposes the category list to employees and agents without granting `budgets.view`.

## Reporting read boundary

Variance uses `variance/repository.py` for its scoped reporting query.
This query is a read contract, not permission to import foreign private models/repositories or write foreign rows.

| Owner | Table | Columns read | Meaning used by Variance |
| --- | --- | --- | --- |
| Budgets `lines` | `budgets.line` | `department_id`, `year_month`, `category`, `amount`, `superseded_at` | Current Budget is the row with no `superseded_at`, at department/month/category grain. |
| Claims `drafting` | `claims.claim` | `id`, `department_id` | Claim's copied department is the spend department. |
| Claims `drafting` | `claims.line` | `claim_id`, `spent_on`, `category`, `amount`, `voided_at` | Nonvoided line amount groups into its calendar month and category. |
| Approvals core | `approvals.run` | `id`, `subject_type`, `subject_id`, `started_at`, `cancelled_at` | The latest claim-subject run is ordered by `started_at DESC, id DESC`, matching `ApprovalService.latest`. |
| Approvals core | `approvals.run_step` | `run_id`, `position` | A nonempty run whose steps are all decided may be approved. |
| Approvals core | `approvals.decision` | `run_id`, `position`, `outcome` | Any rejection makes a noncancelled run rejected; all run steps decided with no rejection makes it approved. |
| Users core | `users.department` | `id`, `code`, `name` | Supplies report labels and deterministic department sort order. |

The Budgets `variance` feature is the consumer; its `budgets.view` authorization derives department scope from `CurrentPerson` and applies that scope to both Budget and spend aggregates in SQL.
The requested inclusive `YYYY-MM` range filters both sides.
Run state precedence matches Approvals `state_of`: cancellation, then any rejection, then nonempty all-decided, otherwise pending.
An absent Budget remains `null`, including its Variance; a Budget with no spend has actual `0.00`, and actual-only rows have `null` Budget and Variance.
The query preserves Decimal amounts, current Budget versions, nonvoided Claim lines and `(department code, month, category)` ordering.

When any consumed columns or meanings change, run `budgets/tests/test_budget_lines.py` and the Claims lifecycle tests in `claims/tests/test_claims.py`, plus the backend architecture/import checks.
Claims mutations and Approval decisions remain with their owners.
