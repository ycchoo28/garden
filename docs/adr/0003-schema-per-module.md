# 0003. Schema per module

Status: Accepted, 2026-11-03.
The data-access consequence was refined by [ADR 0026](0026-business-ownership-and-reporting-reads.md) on 2026-11-06.

## Context

Modules share one database, so their tables could collide or depend on each other without anyone noticing.

## Decision

Each module owns one Postgres schema, named after the module, and one Alembic branch with the same label.
Each migration creates its schema.
No foreign key may cross schemas, and modules refer to each other by ID only.

## Consequences

- An architecture test fails when a table references another module's schema.
- The users, audit, files and agent schemas are created by their own migrations.
- Code and service interactions use the owning module's public interface.
- Read-only reporting may query another module's tables through a documented database read contract, as described in ADR 0026.
  This does not transfer table ownership or permit foreign writes.
