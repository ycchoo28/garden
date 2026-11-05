# 0019. Closing a month freezes its actual spend

Status: Accepted, 2026-11-03.

## Context

Finance reports on a month once the books for it are final.
If a late claim line, or a void of one, can still change that month, a report that was already sent becomes wrong without anyone noticing.

## Decision

The budgets module closes a month with a reason, and the close records who closed it and when.
A closed month is a fact that never reverts, so there is no reopen action.
Claims refuses to add, void or submit a claim line dated in a closed month.
Claims asks budgets whether a month is closed through a port, so the check reads the budgets interface and not its tables.
Closing a month takes the same ledger lock as the claim line changes, so a close never overlaps a change to that month.
The refusal is a problem document.

## Consequences

- A correction to a closed month needs a new month decision, which is deliberate friction.
- Closing a month that has no claim lines is allowed, so the close can be recorded before a late claim arrives.
- An agent's close waits for the person's confirmation in the browser, as set out in the confirmation gate.
