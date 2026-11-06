# Claims (module)

## What it owns

- Expense claims made by an employee, their lines and the receipt for each line.
- The claim's status, computed from its approval run, and its lines' void history.
- It does not own approval flows (the approvals core does), receipts as files (the files core does), or closed months (the budgets module does).

Schema: `claims`. Tables: `claim` and `line`, both owned by the `drafting` feature.

## Features

- `drafting/` creates a claim, adds and voids its lines, submits and withdraws it, and reads the claims. Its repository is `drafting/repository.py`, and no other feature imports it.
- `review/` lists the approval queue and decides a claim. It reads and versions the claim through `ClaimsService`, which returns a plain `ClaimView`, not through the repository.
- `views.py`, `rules.py`, `models.py`, `ports.py`, `permissions.py` and `flows.py` stay at the module root, because both features use them.
  The status rules and the read models are shared, so they do not belong to one feature.
- `api.py` at the module root mounts the two feature routers under `/api/claims`.
  The review routes are mounted first, so `/queue` is not read as a claim id.

## Interface

Import from `app.modules.claims` only: `router`, `ROLES` and `PERMISSIONS`.
The approved spend per department, month and category is exposed through `ClaimsService.approved_spend`, which the budgets module reads for the variance report.
Variance counts approved, nonvoided claim lines only, through the documented Budgets reporting read in [the Budgets module contract](../budgets/README.md).
A line uses the public Budgets `CATEGORIES` contract, the same list Budget lines use.
The category operation is available to claim creators without granting them Budget financial access.

## Reporting read boundary

`ClaimsService.approved_spend` remains a public read for module consumers.
Budget Variance uses its documented scoped SQL read contract, without importing the private Claims repository or model classes.
Claims remains the sole writer for its claim and line tables.

## Key decisions

- Approval flows are data in the approvals core, and the claim asks it for a run (see ADR 0016).
- Nobody approves their own claim, and nobody decides two steps of one run (see ADR 0017).
- Certify, submit, approve and reject are person-only, and withdraw is the one agent-allowed action that waits for a person's confirmation.
- Closed months are checked through the `ClosedMonths` port, which asks the budgets module. A line dated in a closed month cannot be added, voided or submitted.
