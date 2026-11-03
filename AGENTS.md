# AGENTS.md

This file is the working guide for coding agents in this repository.
Read it before you change anything, then read the folder you work in.

## Commands

- `just setup` installs dependencies and creates `.env` from `.env.example`.
- `just up` starts Postgres and the fake identity provider in Docker Compose.
- `just migrate`, `just seed` and `just dev` prepare and run the local stack.
- `just quick` runs before you hand work over, and `just check` runs everything.
- `just api` regenerates the OpenAPI contract and the frontend client.
  Never hand-edit files under `contracts/` or `frontend/src/api/generated/`.

## Layout

- `backend/app/shared` holds plumbing that every module uses: errors, operations, permissions, database helpers and the contract headers.
- `backend/app/core` holds the foundation modules: users, audit, files and confirmations.
  Core never imports business modules.
- `backend/app/modules` holds business modules, one folder each, created with `just new-module`.
- `backend/app/composition.py` lists the installed modules.
- `backend/migrations` holds one Alembic branch per module, and each branch creates its own schema.
- `frontend/src/modules` holds frontend modules. A module imports only from `api`, `shared` and itself.
- `frontend/src/shared` holds the UI shell, theme tokens and shared helpers.

## Rules that the checks enforce

- Every route declares an operation with `op(...)` and requires a permission through `require(...)`, or it is listed as public in the architecture tests.
- Every permission declares whether an agent may hold it. There is no default.
- Services and access checks never touch the database. Only repositories and models do.
- There are no foreign keys across module schemas.
- Errors are RFC 9457 problem documents, raised as `ProblemError` subclasses.
- Business changes write an audit row in the same transaction.

## Rules for the work itself

- Do not commit, push or add a remote unless the person asks you to.
- Do not use `rm`. Move files you no longer need aside, or use `trash` when it is installed.
- Never write the em dash character (U+2014). Use a plain "-".
- Do not edit generated files. Change the source and regenerate.
- Do not touch `.scratch/` or `docs/specs/`. They hold the brief for the work and the product spec.
- Keep changes surgical. Every changed line should trace to the task.
- Use the loopback ports in `justfile`. Check with `ss -ltn` before you start a server.
- The fake identity provider and the seeded people are for development only. Never ship them.

## Where to look

- [README.md](README.md) for the local setup and the ports.
- [GLOSSARY.md](GLOSSARY.md) for the domain words. Use these terms and avoid the ones it lists as terms to avoid.
- [docs/code-patterns.md](docs/code-patterns.md) for how to write a route, a service, a repository and a test.
- [docs/ui.md](docs/ui.md) for the frontend shell and colour rules.
- [docs/adr/README.md](docs/adr/README.md) for the decisions and why they were made.
