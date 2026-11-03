# 0004. Facts over flags

Status: Accepted, 2026-11-03.

## Context

A mutable status flag such as `approved = true` says what is true now, but not when it happened or who made it.
Flags also drift out of step with the facts that should decide them.

## Decision

Record facts as columns named `*_at`, `*_by` and `*_reason`.
Compute a status from those facts when it is read, wherever that is practical.
A confirmation, for example, has a `decided_at` and an `approved` value, and its status is computed from those and the clock.

## Consequences

- History is kept without an extra table.
- Reads do a little more work, and a status must be derived the same way everywhere.
