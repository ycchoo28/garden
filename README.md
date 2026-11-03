# Garden

Garden is a small internal portal for team spend at a fictional company, Fernhill Studio.
It is built to show how a modular Python and Vue codebase stays easy for coding agents to change safely: the rules, checks, templates and contracts do most of the guarding.

## What it does

- Department budgets, compared against actual spend (planned).
- Company card statement import (planned).
- Expense claims with receipts and approval (planned).
- A policy knowledge base, filtered by what each person may read (planned).

The foundation is in place now: people and departments, role-based permissions, browser sign-in through a fake identity provider, an audit trail, content-addressed file storage and a server-side confirmation gate.

## Run it on your machine

You need Docker, uv, Bun, just and gitleaks.
Then run these from the repository root:

```sh
just setup    # create .env and install dependencies
just up       # start Postgres 17 with pgvector and the fake identity provider
just migrate  # apply the database migrations and the job queue schema
just seed     # load Fernhill Studio and the seeded people
just dev      # start the API, the background worker and the web app
```

Open http://localhost:55173 and choose a seeded person.
The fake sign-in page takes the person's email as the subject, for example `dev.admin@fernhill.example`.

Every port is bound to 127.0.0.1.
The API runs on 55000, the web app on 55173, the fake identity provider on 55081 and Postgres on 55432.

## Check it

- `just quick` runs the fast checks: backend format, lint, strict types and import contracts; frontend types, lint, unit tests and colour rules; the API contract; secrets; doc links; and the deny-list.
- `just check` runs everything: the quick checks, the backend tests against Postgres, the browser tests and the dependency audits.
- `just new-module <name>` scaffolds a business module.

## Where to read next

- [AGENTS.md](AGENTS.md) is the working guide for coding agents and people who change the code.
- [GLOSSARY.md](GLOSSARY.md) defines the domain words, and the terms to avoid.
- [docs/code-patterns.md](docs/code-patterns.md) shows how backend and frontend code is laid out.
- [docs/ui.md](docs/ui.md) covers the frontend shell, theme tokens and colour rules.
- [docs/adr/README.md](docs/adr/README.md) lists the architecture decisions.
