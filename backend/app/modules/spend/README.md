# Spend (module)

## What it owns

- Company card statements: each import, its rows, and whether it has been voided.
- Closed months: a month that is closed can no longer change.
- Schema: `spend`. Tables: `statement`, `statement_row`, `month_close`.
- It does not own budgets, which are in `budgets`, or claims, which are not built yet.

## Interface

Import from `app.modules.spend` only: `router`, `ROLES`, `PERMISSIONS`, `CATEGORIES`, `YEAR_MONTH_PATTERN`, `is_year_month`, `SpendService` and `get_spend_service`.
`SpendService.is_month_closed(year_month)` says whether a month is closed.
`SpendService.actual_spend(...)` returns actual spend per department, month and category from statements that are not voided.

## Key decisions

- Statement import is a dry run, then all or nothing (ADR 0018).
- Closing a month freezes its spend (ADR 0019).
- Voids keep the rows and record who, when and why (ADR 0005).
- Imports and voids take the `spend.ledger` advisory lock, so they never race each other or a month close.
