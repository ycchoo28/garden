# 0019. Closing a month freezes its spend

Status: Accepted, 2026-11-03.

## Context

Finance reports on a month once the books for it are final.
If a late import or a void can still change that month, a report that was already sent becomes wrong without anyone noticing.

## Decision

A month is closed with a reason, and the close records who closed it and when.
A closed month is a fact that never reverts, so there is no reopen action.
Once a month is closed, an import that has a row in that month is refused, and so is a void of any statement that has rows in it.
Both refusals are problem documents: an import returns 422 with the row, and a void returns 409.
The spend interface tells other modules whether a month is closed, so a budget check can read it.
Closing a month takes the same ledger lock as imports and voids, so a close never overlaps a change to that month.

## Consequences

- A correction to a closed month needs a new month decision, which is deliberate friction.
- Closing a month that has no spend is allowed, so the close can be recorded before a late file arrives.
- An agent's close or void waits for the person's confirmation in the browser, as set out in the confirmation gate.
