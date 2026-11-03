# 0001. Modular monolith

Status: Accepted, 2026-11-03.

## Context

Garden has a few business areas that share people, permissions and audit, and one team builds them.
Separate services would add network calls, deployment work and contracts between teams that do not exist yet.

## Decision

Garden is one FastAPI app with one Postgres database.
Code is split into `shared` plumbing, `core` foundation modules and business `modules`.
`backend/app/composition.py` is the one place that lists the installed modules.

## Consequences

- A change that spans modules is one commit and one deploy.
- Modules still keep their own schema, router and permissions, so they can be split later if that ever matters.
- Import contracts stop core from importing business modules, and shared from importing either.
