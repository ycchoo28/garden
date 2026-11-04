# 0018. Statement import is a dry run, then all or nothing

Status: Accepted, 2026-11-03.

## Context

Finance uploads company card statements as CSV files.
A file can hold a bad amount, an unknown department, a date that does not exist, or a charge that was already imported.
Saving part of a file leaves the books half-updated, and a second upload of the same file counts every charge twice.

## Decision

An upload is first checked by a dry run, which reads the file and writes nothing.
The dry run lists every row error and every duplicate, with its row number.
A duplicate is a transaction reference that appears twice in the file, or a reference that a statement which is not voided already holds.
The import saves every row or none.
A file with any row error or duplicate cannot be applied, and the apply control stays off until a corrected file passes the check.
The import takes the ledger lock, then checks the file again, so the check and the save see the same saved references.
The statement keeps the SHA-256 of the file, the row count and the total, so a later reader can tell what was saved.

## Consequences

- Finance sees every problem in one pass, before anything changes.
- A voided statement stops counting, and its transaction references may be imported again.
- Approved claims are not yet counted as actual spend, because the claims module is outside this ticket.
- The file is parsed again when it is applied, so the upload is read twice; files are capped at 1 MB and 5,000 rows.
