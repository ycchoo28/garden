---
name: garden-claims
description: Draft, correct and check expense claims in Garden with the garden command. Use when asked to start a claim, add or void its lines, check a claim's status, or withdraw a submitted claim.
---

# Garden claims

A claim is one person's request for money spent for the company.
It has a title and lines, and each line has a category, the date spent, an amount, a description and a receipt.
Its status (`draft`, `submitted`, `approved`, `rejected` or `withdrawn`) comes from its approval run, not from a flag you can set.

Use `garden` (see the garden-basics skill).
Each command below is the operation id as words, and the MCP tool name is the same id with underscores.

## Find things

- Your permissions: `garden users me --json` (`users.self.read`).
- Claims you may see: `garden claims claim list --json` (`claims.claim.list`).
  Finance and admin see every claim, and everyone else sees their own, plus their department's if they manage it.
- One claim, with its lines, receipts, `version` and decision history: `garden claims claim read --claim-id <id> --json` (`claims.claim.read`).
- Allowed categories: `garden budgets category list --json` (`budgets.category.list`).
  They are `equipment`, `marketing`, `meals`, `office`, `other`, `services`, `software` and `travel`.

## Draft a claim

1. Create the draft: `garden claims claim create --body '{"title": "<title>"}' --json` (`claims.claim.create`).
   Keep the returned id and `version`.
2. Get the receipt's SHA-256.
   If the receipt is a file on this computer, upload it: `garden files file upload --path <receipt> --json` (`files.file.upload`), and use the `sha256` it returns.
   To check a receipt that is already stored, pass its SHA-256 to `files.file.read` (`garden files file read --sha256 <hash>`).
   A receipt that is not stored cannot be attached.
3. Add each line: `garden claims line add --claim-id <id> --expected-version <version> --category <category> --spent-on <YYYY-MM-DD> --amount <amount> --description "<text>" --receipt-sha256 <hash> --json` (`claims.line.add`).
4. Read the claim again after each change, because every change moves its `version` on.

Certify and submit are person-only.
Tell the person the claim is ready, and that they must certify and submit it in the portal.

## Correct a claim

- To remove a line, void it: `garden claims line void --claim-id <id> --line-id <id> --expected-version <version> --reason "<text>" --json` (`claims.line.void`).
  The line stays in the history.
- To cancel a submitted claim before anyone decides it, withdraw it: `garden claims claim withdraw --claim-id <id> --expected-version <version> --json` (`claims.claim.withdraw`).
  This is a critical action, so tell the person first, and wait for them to confirm in the browser.
- Lines and claims on a draft or rejected claim can be changed.
  A submitted, approved or withdrawn claim cannot.

## Approval (person only)

- Certifying, submitting, approving and rejecting are person-only (`claims.claim.submit`, `claims.claim.decide`).
  An agent login never holds them, and the approval queue (`claims.decision.queue`) is not a tool either.
- A claim above the finance threshold (USD 500.00 in the seeded flow) goes to finance after the manager.
  Nobody approves their own claim, and nobody decides two steps of one run.
- When a claim is rejected, the claimant may correct it, and submit it again.

## Permissions

- `employee` holds `claims.view`, `claims.create`, `claims.submit` and `claims.withdraw`.
- `manager` and `finance` hold `claims.decide`, which an agent never holds.
- A 403 means the person lacks the permission, so say so and do not retry.

## Pitfalls

- `expected_version` must be the claim's current version.
  A stale version returns 409, so read the claim again and retry.
- A line dated in a closed month is refused, and so is a void or submit in that month.
  Tell the person the month is closed, and do not try to date the line elsewhere to get round it.
- Amounts are money strings with two decimals, such as `"42.50"`.
  Do not send floats.
- Upload a receipt with `--path`, never inside `--body`.
- A 403 on the upload means the person lacks `files.upload`.
  Say so, and ask the person to attach the receipt in the portal, then give you its SHA-256.
