# 0008. Audit in the same transaction

Status: Accepted, 2026-11-03.

## Context

An audit row written after the change commits can be lost, and a row written in a separate transaction can record a change that never happened.

## Decision

`AuditService.record` adds the audit row to the caller's session.
The caller commits once, so the change and its audit row land together or not at all.
Each row records the actor, whether an agent made the change, the operation, the target and a summary.

## Consequences

- A service that changes data must call `record` before it commits.
- The request's login decides the acting-as-agent flag, so modules do not set it themselves.
