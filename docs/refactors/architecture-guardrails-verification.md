# Architecture guardrails verification

Implementation follows the [guardrails spec](../specs/architecture-guardrails.md) and [ADR 0027](../adr/0027-architecture-enforcement.md).
No commits, pushes or repository hosting settings are part of this change.
The spec was moved into `docs/specs/`; that directory remains excluded by the checkout's existing `.git/info/exclude` rule.

## Repository findings

The new checks identified business vocabulary in `frontend/src/shared/ui/navigation.test.ts` and a business-specific example in `frontend/src/shared/ui/navigation.ts`.
The test now uses invented module names and the comment describes a generic module section.
No product behavior changed.
The discovered backend import graph found no current private-interface import, feature repository leak or business dependency cycle.
Reporting SQL has declared foreign reads and no detected foreign writes.

The database layer has named composition exceptions in `scripts/check_backend_architecture.py`.
`app.modules.knowledge.batches.jobs` creates one worker session around several repositories and services.
`app.shared.jobs` configures the Postgres queue connection.
`app.seed`, `app.demo_seed` and `app.migrate` compose development or migration database work.
These are explicit exceptions with reasons, rather than undisclosed passing violations.

The Shared single-consumer report is advisory.
Money and month helpers remain Shared by the spec's explicit decision.
Shared queue and request-login plumbing also currently have one business consumer, Knowledge.
Their generic infrastructure responsibility supports retaining them in Shared.
The frontend redirect helper has one Core package consumer, Users, alongside application-layer uses; its navigation responsibility remains generic.

## Ownership review answers

1. This change serves repository architecture enforcement; it introduces no new business capability or Module.
2. Business pages and rules keep their current owners; Variance tests stay with Budgets.
3. Shared edits remove business-specific examples; the added tooling lives in `scripts/`, outside Shared.
4. No application-layer file or global page is added; the explicit page allowlist starts with Home and Signed out.
5. No public capability is added or widened; existing package interfaces are now checked.
6. Existing owner operations and transaction behavior are unchanged; the database checker exposes the composition exceptions above.
7. Reporting declarations are consumed from module READMEs and checked against migrated storage; Variance HTTP tests check approval and scope semantics.
8. Generic money/month helpers remain Shared as explicitly required; the advisory report does not force moves based only on consumer counts.

## Verification

Final `just quick` passes on 2026-11-10.

- Backend and agent format, lint, type and import checks pass.
- Frontend type, lint, unit tests (9) and colour-token checks pass.
- Every static architecture command passes on the current repository.
- The command-level proof suite passes 40 tests locally; 4 cases are reserved for a generated Scaffold module.
- The isolated generated-module copy passes all 17 backend guardrail proofs, including those 4 Scaffold cases.
- All 13 Budget/Variance HTTP tests pass against the dedicated `garden_guardrails_test` database.
- The reporting declaration test passes against that migrated database.
- The full agent suite passes 106 tests, and the focused backend architecture suite passes 14 tests.
- The API comparison reports the published Contract and generated client current, without rewriting them.
- Secret scanning, documentation links, the deny-list and whitespace checks pass.

The full backend suite and browser suite were not run locally for this tooling change.
The existing full CI backend and browser jobs remain unchanged, with the command-level guardrail proof added to the Scaffold job.
