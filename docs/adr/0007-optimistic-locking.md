# 0007. Optimistic locking

Status: Accepted, 2026-11-03.

## Context

Two people or two agents can change the same record at the same time, and the second save would silently undo the first.

## Decision

A record that people or agents edit has a `version` column.
An update names the version it read, and a stale version returns 409 Conflict.

## Consequences

- No foundation module edits versioned records yet, so the first business module that does adds the column and the check.
- Clients must re-read a record after a 409 before they try again.
