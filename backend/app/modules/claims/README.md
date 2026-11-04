# Claims (module)

## What it owns

- Expense claims made by an employee, their lines and the receipt for each line.
- The claim's status, computed from its approval run, and its lines' void history.
- It does not own approval flows (the approvals core does), receipts as files (the files core does), or closed months (the spend module will own them).

Schema: `claims`. Tables: `claim`, `line`.

## Interface

Import from `app.modules.claims` only: `router`, `ROLES` and `PERMISSIONS`.
The approved spend per department, month and category is exposed through `ClaimsService.approved_spend`, which the budgets module reads for the variance report.

## Key decisions

- Approval flows are data in the approvals core, and the claim asks it for a run (see ADR 0016).
- Nobody approves their own claim, and nobody decides two steps of one run (see ADR 0017).
- Certify, submit, approve and reject are person-only, and withdraw is the one agent-allowed action that waits for a person's confirmation.
- Closed months are checked through the `ClosedMonths` port until the spend module can answer.
