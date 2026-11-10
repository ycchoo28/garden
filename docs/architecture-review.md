# Architecture enforcement and ownership review

## Enforcement map

Each row names the primary enforcement for that rule.
Static architecture commands run through `just architecture-check`, included in `just quick`, without Postgres.

| Rule | Primary enforcement |
| --- | --- |
| Core never imports business; Shared imports neither | Backend Import Linter contracts |
| Cross-module public imports, private feature repositories, business cycles, database layer | `scripts/check_backend_architecture.py` using Grimp |
| Feature services import only models their repository imports | `backend/tests/architecture/test_feature_boundaries.py` in backend-check |
| Shared direction, vocabulary and permitted kinds | `scripts/check_shared_architecture.py` |
| Frontend dependency direction and public entry points | Frontend ESLint boundaries |
| Frontend placement, application pages, backend package names and network calls | `scripts/check_frontend_architecture.py` and frontend ESLint |
| Foreign SQL reads declared; foreign SQL writes refused | `scripts/check_reporting_contracts.py` |
| Declared reporting tables and columns exist | Database architecture test `test_reporting_contracts.py` |
| Variance approval parity and role scope | Budget HTTP behavior tests in `test_budget_lines.py` |
| Explicit Critical operation choice | Required `confirm` argument to `op` |
| Reviewed Critical operations, public routes, permissions and no cross-schema foreign keys | Backend architecture tests |
| Agent exhaustive layers and package interfaces | Agent Import Linter and agent architecture checks |
| Agent skills reference published Operations | Agent architecture skill test |
| Generated Contract and client match source | Non-mutating `just api-check` |
| No em dash | `scripts/check_authoring.py` |
| Valid documentation links and prohibited terminology | `just docs-check` and `just deny-check` |
| Business ownership, public-interface quality and semantics of consumed columns | Required ownership review below |
| Single-consumer Shared helpers | Advisory output from Shared check, reviewed below |
| Audit transaction semantics and backend authorization | Module HTTP behavior tests |

## Required ownership checklist

Answer all eight questions at handover when adding a file to Shared or the application layer, adding a Module, widening a public entry point or adding a reporting read contract.
Use a short answer with evidence or state why a question does not apply.

1. Which business outcome does this capability serve, and does its owner match that outcome?
2. Does each business page, rule and domain-specific component live with that owner?
3. Would each Shared addition survive unchanged if all business modules were replaced?
4. Is each application-layer addition global composition, layout or an explicitly allowed global page?
5. Does each public entry point expose a deliberate capability or view rather than private storage details?
6. Are cross-module interactions through public owner operations, with writes, authorization, transactions and audit kept with the owner?
7. For reporting reads, are consumed tables, columns, meanings, scope and affected tests declared and proven against authoritative behavior?
8. Do single-consumer Shared helpers belong with their consumer, or is their domain-neutral placement justified?

## Exceptions and limits

Backend database exceptions are the reasoned `DB_EXCEPTIONS` map in the backend checker.
Worker composition may own one session spanning repositories; development seed and migration entry points compose database work.
Public and Critical route exception lists remain in the backend architecture tests.
Shared vocabulary exceptions are generic glossary terms, with reasons in the Shared checker.
The application page allowlist lives in the frontend structure checker.
Static imports cannot detect dynamic imports, and vocabulary cannot establish business neutrality by itself.
Reporting declarations establish storage dependencies; behavior tests and review establish meaning.
No per-module database grants or repository hosting settings are changed.
