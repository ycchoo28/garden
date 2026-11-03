# 0005. Replace or void

Status: Accepted, 2026-11-03.

## Context

Editing a business record in place loses what it said before, and a finance record that disappears is a worse problem than one that was wrong.

## Decision

A business record is never edited in place.
A correction replaces the record with a new one, or voids it with a reason, and the old row stays.

## Consequences

- Every business module that stores money or decisions follows this rule.
- No foundation module needs it yet, so it applies from the first business module on.
- Queries must say whether they want voided rows.
