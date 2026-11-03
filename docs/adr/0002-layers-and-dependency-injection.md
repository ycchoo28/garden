# 0002. Layers and dependency injection

Status: Accepted, 2026-11-03.

## Context

Route handlers that run queries directly are hard to test, and they let business rules leak into HTTP code.
Agents change code quickly, so the boundaries must be enforced by tools and not by memory.

## Decision

Each module has four layers: api, service, repository and models.
A request goes through them in that order and no other.
Each layer's dependencies come from FastAPI dependency functions, such as `get_audit_service`.
There are no service locators and no global sessions.

## Consequences

- Import contracts enforce the layers: api and services never touch the ORM or the session.
- Tests replace a dependency with `app.dependency_overrides`, so no test patches a module global.
- Each module writes a few more lines than a direct query would need.
