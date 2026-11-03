# 0003. Schema per module

Status: Accepted, 2026-11-03.

## Context

Modules share one database, so their tables could collide or depend on each other without anyone noticing.

## Decision

Each module owns one Postgres schema, named after the module, and one Alembic branch with the same label.
Each migration creates its schema.
No foreign key may cross schemas, and modules refer to each other by ID only.

## Consequences

- An architecture test fails when a table references another module's schema.
- The users, audit, files and confirmations schemas are created by their own migrations.
- A module that needs another module's data asks that module's public interface for it.
