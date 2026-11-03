# 0006. Money and IDs

Status: Accepted, 2026-11-03.

## Context

Floating-point money loses cents, and sequential IDs leak how many records exist and in what order they were made.

## Decision

Money is stored as `NUMERIC(14,2)`, handled as `Decimal` in Python, and sent over the API as a string.
Primary keys are UUIDv7, made by `app.shared.ids.new_id`.

## Consequences

- Totals reconcile to the cent.
- UUIDv7 keys sort by creation time, so indexes stay tidy.
- The frontend parses money strings itself and never does arithmetic on floats.
