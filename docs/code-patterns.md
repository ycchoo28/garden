# Code patterns

This document shows the shape of the code in garden.
Copy these patterns when you add to a module, and read the matching ADR when you want the reason.

## Backend layers

Each module has four layers, and a request passes through them in this order: api, service, repository and models.

- **api** (`api.py`) declares routes. It names each route with `op(...)`, requires a permission with `require(...)`, and calls the service.
- **service** (`service.py`) holds the business rules. It never imports the ORM or the database session.
- **repository** (`repository.py`) is the only code that runs queries.
- **models** (`models.py`) declares the tables in the module's schema.

The import contracts in `backend/pyproject.toml` enforce these rules, and `just backend-check` runs them.

## A route

```python
@router.get(
    "/api/audit/events",
    response_model=list[AuditEventOut],
    **op("audit.event.list", "List the most recent saved changes, newest first."),
)
async def list_events(
    _person: Annotated[CurrentPerson, require("audit.view")],
    service: Annotated[AuditService, Depends(get_audit_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AuditEventOut]:
    ...
```

Every route needs both `op(...)` and a permission check, except the public routes that the architecture test lists.
The operation id follows `module.resource.verb`.

## A permission

Permissions and roles live in the module's `permissions.py`.
Every permission states `agent_allowed`, and there is no default.

```python
PERMISSIONS = (Permission("budgets.view", agent_allowed=True),)
ROLES = (Role(FINANCE, frozenset({"budgets.view"})),)
```

A business module grants its own permissions to the company's roles in its `ROLES`.
Grants from different modules to the same role combine at startup.
The role names come from the users core, and the app refuses to start when a module names any other role.
The app also refuses to start when a role names a permission that no module declares.

## Errors

Raise a `ProblemError` subclass such as `NotFound`, `Forbidden` or `Conflict`.
The app turns it into an RFC 9457 problem document with the right status code.

## Business changes and the audit trail

A service that changes data calls `AuditService.record(...)` in the same transaction as the change.
The caller commits, so the change and its audit row land together or not at all.

## Dependencies

Each module provides its service and repository through FastAPI dependency functions, such as `get_audit_service`.
Tests override these functions, and the app never uses a global session or a service locator.

## Tests

Backend tests run against a real Postgres database whose name ends in `_test`.
The shared `client` fixture signs people in through the fake identity provider.
Architecture tests walk every installed route and check the rules above.

## Schemas and migrations

Each module owns one Postgres schema and one Alembic branch, named after the module.
Migrations create their schema, and no foreign key may reference a table in another schema.
Primary keys are UUIDv7 from `app.shared.ids.new_id`.
Schema ownership controls writes; it does not prohibit documented read-only joins across schemas.
See [ADR 0026](adr/0026-business-ownership-and-reporting-reads.md) for the read boundary.

## Feature folders

A business module with more than one area of work splits into feature folders, one per area, such as `budgets/lines/` and `knowledge/search/`.
Each feature folder holds its own `api.py`, `service.py`, `repository.py` and `schemas.py`, as far as that feature needs them.
The module root keeps what the features share: `api.py` mounts the feature routers, `permissions.py` and `__init__.py` export the public interface, and `models.py` holds the tables when several features share them.
A module's tests stay in its own `tests/` folder, and they may import the module's internals.
Garden has these feature folders: `budgets/` has `lines`, `months` and `variance`, `claims/` has `drafting` and `review`, and `knowledge/` has `collections`, `batches`, `search` and `reading`.
Not every feature has every file.
`reading/` holds pure helpers only, and `claims/review/` has no repository, because it reads and versions claims through the drafting service.

The rules are these.

- A feature repository is private to its feature.
  The import-linter contract "Feature repositories stay private to their feature" forbids other module code from importing it.
  A consumer may use an owner's public service or port, or a documented database read contract for reporting.
- Read-only helpers with no database access go at the module root, such as `views.py` and `rules.py` in claims.
- The module's `__init__.py` is the only import point for other modules.
  The architecture test in `backend/tests/architecture/test_module_interfaces.py` fails when another module imports anything deeper.
- Each table has one owning feature repository, and only that feature writes it.
  Reporting repositories may read other features' or modules' tables under the read rules below.
- The search repository in knowledge applies access rules by reading collection rules in SQL.
  It is an existing example of controlled cross-feature reads, and writes only the search log it owns.
- Service interactions return deliberate public views rather than another feature's repository or ORM rows.

## Reporting reads

A report belongs to the business module responsible for its outcome, even when it reads several domains.
Budget Variance belongs to Budgets and may aggregate Budget and Claims data directly in a Budgets-owned reporting repository.
Its current service-based implementation is also allowed.

- Document consumed tables/views, relevant columns and business meanings, the consumer, caller scope and affected migration tests in the module READMEs before adding direct cross-module SQL.
- Keep SQL in repositories, with department and reporting-period filters applied there where practical.
  Do not depend on frontend filtering for authorization.
- Keep foreign private models and repositories private.
  A database read contract permits specified SQL references, not arbitrary code imports or foreign writes.
- Preserve authoritative meanings such as latest-run approval, voiding, dates and category allocation.
  Use an owner-provided read query or view when copying those meanings would duplicate complex rules.
- Aggregate at the intended grain without multiplying amounts through joins, and include missing Budget or spending sides correctly.
- Run report tests when consumed schema or business meanings change.
  Check backend authorization, money precision and response compatibility as well as totals.
- Keep writes, audit and transaction handling under the owning domain's operations.
  The closed-months port used during Claim mutations remains an explicit domain integration.

## Business and application ownership

Business modules own their pages, domain-specific UI, client logic and tests.
Multiple data sources do not create a new module or move a business page to the application layer.
Frontend/backend naming should align by responsibility, without forcing matching files or empty modules.

The application layer owns routing, home, global auth/error pages, layouts and navigation composition.
Generic UI, theme tokens, transport and utilities stay in Shared.
Domain-specific vocabulary and policies, including expense categories, require a business owner and a public contract.
Reuse by several modules is not enough reason to put business policy in Shared.

The current Users home/signed-out pages, Shared layouts and Shared category definition are pending refactor work under ADR 0026.
Their current locations should not be copied as the target ownership pattern.

The frontend mirrors the same split.
`frontend/src/modules/<module>/<feature>/` holds the pages of one feature, and the module's `index.ts` exports the routes.

## Frontend modules

Each frontend module lives in `frontend/src/modules/<name>` and exports its pages and routes from `index.ts`.
It may import from `api` and `shared`, and from itself, but never from another module or from `app`.
Boundaries lint enforces this, and a module's feature folders count as part of that module.
Server calls use the generated query options, such as `usersSelfReadOptions()`, and never a hand-written URL.

## Adding a module

Run `just new-module <name>`.
The scaffold creates the backend package, the migration, the tests and the frontend folder, registers the module in `composition.py`, and regenerates the contract and client.
Then fill the module in and run `just check`.
