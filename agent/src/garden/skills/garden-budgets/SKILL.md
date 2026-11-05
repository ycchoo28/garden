---
name: garden-budgets
description: Read and change budget lines, compare budget with actual spend, and close months in Garden with the garden command. Use when asked about a department's budget, to set or replace a budget amount, to see variance, or to close a month.
---

# Garden budgets

A budget line is the budget for one department, one month (`YYYY-MM`) and one category.
Each replacement keeps the old version with its reason, so nothing is lost.
Variance is the budget minus the actual spend, where actual spend is approved claim lines only.
A negative variance means spend above budget.

Use `garden` (see the garden-basics skill).
Each command below is the operation id as words, and the MCP tool name is the same id with underscores.

## Find things

- Department ids: `garden users departments --json` (`users.department.list`).
  Budgets and claims use these ids, not names.
- Categories: `garden budgets category list --json` (`budgets.category.list`).
- Lines for one month: `garden budgets line list --month <YYYY-MM> --json` (`budgets.line.list`).
  Add `--department-id <id>` to narrow it.
  A manager sees only their own department, and a request for another department is refused with 403.
- The history of one line, oldest first, with the reason for each change: `garden budgets line history --line-id <id> --json` (`budgets.line.history`).

## Change a budget

- Create a budget for a department, month and category, for finance and admin:
  `garden budgets line create --department-id <id> --year-month <YYYY-MM> --category <category> --amount <amount> --json` (`budgets.line.create`).
- Change an amount by replacing it, not by creating a second line:
  `garden budgets line replace --line-id <id> --version <version> --amount <amount> --reason "<why>" --json` (`budgets.line.replace`).
  Use the `version` from the line you read.
  A stale `version` returns 409, so read the line again, and check the change still makes sense before you retry.
- Show the person each change and its reason before you run it.
  Ordinary budget saves need no approval.

## Variance

- `garden budgets variance read --from <YYYY-MM> --to <YYYY-MM> --json` (`budgets.variance.read`).
  It compares budget with actual spend by department, month and category for the range.
  Managers see only their own department.

## Close a month (critical)

- Closed months: `garden budgets month list --json` (`budgets.month.list`).
  This needs `budgets.month.close`, which only finance and admin hold.
- Close: `garden budgets month close --year-month <YYYY-MM> --reason "<why>" --json` (`budgets.month.close`).
  This is a critical action.
  Tell the person the month, the reason and the effect, then wait for them to confirm in the browser.
  Do not call it again while it waits.

A closed month never reopens, and no claim line dated in it can be added, voided or submitted afterwards.
Closing a month with no claim lines is allowed.
There is no reopen action, so a correction needs a new decision by the person.

## Permissions

- `budgets.view`: every manager, and finance and admin (`budgets.line.list`, `budgets.line.history`, `budgets.variance.read`).
- `budgets.manage`: finance and admin (`budgets.line.create`, `budgets.line.replace`).
  Agents may hold it.
- `budgets.month.close`: finance and admin (`budgets.month.list`, `budgets.month.close`).

## Pitfalls

- Amounts are money strings with two decimals, such as `"1500.00"`.
  Do not send floats.
- Months are `YYYY-MM`, such as `2026-10`.
  The same format is used by claims.
- Budget lines cannot be deleted.
  Replace the amount instead, and the history keeps the old version with its reason.
- The categories are a fixed list.
  Ask the person before you invent one.
