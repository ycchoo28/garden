# Audit (core)

## What it owns

- The business audit trail: who did what to which record, and the change.
  It is stored in Postgres and is separate from logs.
- Schema: `audit`, table `event`. Rows are only inserted.

## Interface

Import from `app.core.audit` only: `AuditService` and `get_audit_service`, which a caller uses inside its own request transaction.
It also exports `router` (the list route at `/api/audit/events`), `ROLES`, `PERMISSIONS` and `AuditRepository`.

## Key decisions

- `record` only adds the row to the caller's session.
  The calling module commits, so an audited change and its audit row succeed or fail together.
- No update or delete path exists.
- The actor is a plain id, not a foreign key, because schemas may not foreign-key each other.
- `acting_as_agent` is set from the request's login, so a CLI action is marked as one.
- `confirmation_id` and `confirmed_at` are set from the request's login when a critical change ran under an approval the person gave in the browser (ADR 0012).
