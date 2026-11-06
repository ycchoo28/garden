# Garden business ownership and reporting-read refactor

Status: Specified; implementation pending.
Date: 2026-11-06.
Architecture policy: [ADR 0026](../adr/0026-business-ownership-and-reporting-reads.md).

## Problem Statement

Garden already groups its main business screens and backend capabilities under Budgets, Claims and Knowledge.
However, some global application concerns and business vocabulary have misleading owners, and the original service-only read policy is stricter than the agreed shared-database architecture.

Expense categories currently live in generic Shared plumbing and are repeated as a selectable list in the Claims frontend.
Global home and signed-out pages belong to Users, while application layouts belong to Shared.
These placements make it harder for a developer or coding agent to decide where future changes belong.

Budget Variance is already correctly owned by Budgets.
It currently depends on business services to collect Budget amounts, approved Claims spending and departments, with spending aggregation performed in application code before department filtering.
This is a valid implementation, but it is a candidate for a smaller, scoped reporting read now that direct database reads are explicitly permitted.

The refactor should make ownership clearer while preserving the behavior people rely on.
It should not become a redesign of every lint rule, quality check or testing convention.

## Solution

Keep the existing business modules and feature names.
Give expense categories one explicit business owner, move global frontend composition into the application layer, and keep business pages with their owning modules.
Allow a focused Budget-owned reporting query to consume documented database read contracts while domain-owned operations retain control of writes.

Deliver these as separate, reviewable slices so an ownership move is distinguishable from a reporting implementation change.
The documentation policy is updated first; this spec describes code changes that remain to be implemented.

## User Stories

1. As a Garden developer, I want each feature to have one business owner, so that I can place a change without counting its data sources.
2. As a Budget maintainer, I want Variance to stay under Budgets, so that reporting responsibility stays clear when it reads Claims data.
3. As a Claims maintainer, I want Claims operations to retain control of Claim mutations, so that reporting work cannot bypass Claim invariants.
4. As a platform maintainer, I want reusable Approval behavior to remain in the foundation, so that Claims and Knowledge can share it without duplicating their workflows.
5. As a Garden developer, I want expense categories to have an explicit business owner, so that shared vocabulary is not confused with generic infrastructure.
6. As a person adding a Claim line, I want the selectable categories to come from the authoritative category contract, so that the screen offers the values the backend accepts.
7. As a person who can create Claims, I want to discover categories without receiving Budget financial access, so that reference data does not broaden my permissions.
8. As a Finance person, I want Budget lines and Claim lines to use the same category vocabulary, so that actual spend reconciles with planned amounts.
9. As an agent acting for a person, I want category discovery to respect declared permissions, so that it follows the same access model as other operations.
10. As a frontend developer, I want home, signed-out pages and layouts to have an application owner, so that global composition is easy to locate.
11. As a Users maintainer, I want identity and session operations to stay with Users, so that moving global pages does not scatter authentication behavior.
12. As a business-module developer, I want my module to keep its pages and domain-specific components, so that changes remain colocated.
13. As a frontend developer, I want generic UI primitives and theme tokens to remain reusable, so that the application move does not pull business modules into global composition.
14. As a person using Garden, I want existing links, sign-in redirects and sign-out behavior to keep working, so that directory changes do not disrupt my workflow.
15. As a department Manager, I want Variance to show only my permitted departments, so that reporting reads preserve my data scope.
16. As a Finance person or Admin, I want Variance to retain its broader department view, so that I can reconcile spending across the company.
17. As a person reading Variance, I want only approved, non-voided Claim spending to count, so that drafts, pending Claims and rejected or withdrawn Claims do not inflate actual spend.
18. As a person reading Variance, I want the latest Approval run to determine Claim eligibility, so that earlier decisions do not count a resubmitted Claim incorrectly.
19. As a person reading Variance, I want amounts grouped by department, year-month and category, so that the report remains comparable to Budget lines.
20. As a person reading Variance, I want missing Budget amounts to remain distinguishable from an explicit zero Budget, so that unbudgeted spending stays visible.
21. As a backend maintainer, I want cross-module reporting reads to name their database dependencies and meanings, so that storage changes reveal the reports they can affect.
22. As a backend maintainer, I want a reporting query to avoid importing foreign private repositories or ORM implementations, so that read permission does not dissolve code boundaries.
23. As a maintainer, I want reporting reads to preserve authoritative Approval semantics, so that moving aggregation into SQL does not create a second inconsistent workflow engine.
24. As a maintainer, I want a narrow read refactor to avoid unnecessary command-service dependencies, so that a report is easier to understand and maintain.
25. As a coding agent, I want the documentation to distinguish current code from pending refactors, so that I do not treat a proposed layout as already delivered.
26. As a repository owner, I want the broad lint, checks and testing redesign handled separately, so that this refactor remains focused and reviewable.

## Implementation Decisions

1. Business responsibility determines ownership.
   Budgets continues to own Budget lines, Month close and Variance; Claims owns Claim drafting and review; Knowledge retains its existing capabilities.
   Integrated data does not justify a new generic cross-module business module.
2. Core continues to hold reusable foundation capabilities: Users, Audit, Files, Approvals and Agent.
   Shared holds domain-neutral infrastructure and primitives.
   Reuse alone does not make expense policy domain-neutral.
3. Budgets will own the fixed expense-category taxonomy in this refactor.
   This follows the existing Budget category operation and the glossary's Budget category vocabulary.
   Expose a narrow public category contract for backend consumers instead of importing the definition from generic Shared plumbing.
   Do not introduce a new Finance or Categories module solely to hold the fixed list.
4. Preserve the existing Budget category operation and its category values and ordering.
   Give category discovery its own read permission, granted to people who can create Claims, without granting permission to read Budget amounts.
   Declare explicitly whether agents may hold that permission; category discovery should be agent-allowed.
   Keep the general Budget view permission for financial records.
5. The Claims frontend consumes the authoritative category operation through the generated API client.
   Remove its duplicated selectable category list.
   Keep presentation labels and Claim-specific formatting in Claims.
   Loading or failure must not silently substitute a separate hard-coded policy list.
6. Treat a backend dependency on the category vocabulary as a public business contract.
   Review the resulting code dependency direction together with the Variance read change, because retaining a broad Budgets-to-Claims service dependency while introducing Claims-to-Budgets category imports would create a cycle.
   Prefer the scoped database read described below to remove that opposing code dependency.
7. Move global frontend composition, home and signed-out screens, layouts and navigation assembly under the application layer.
   Keep identity/session behavior in Users, Agent-owned pages in Agent, business pages under their modules, and generic UI/theme primitives in Shared.
   Module navigation types may stay Shared when they are generic data contracts consumed by modules.
8. Preserve public routes, route names, layout selection, sign-in destinations, permission filtering, theme behavior and sign-out behavior during the frontend ownership move.
   Update only imports and path classifications needed to keep the current build and lint working.
9. Keep Budget Variance's existing public operation and response contract.
   A focused Budgets-owned reporting repository should aggregate current Budget amounts and eligible actual spend at department, year-month and category grain, with caller scope and date filters applied in SQL.
   Validation and scope derivation remain in the service; routes and services do not gain SQL or database-session access.
10. Declare the direct database read contract in the consuming and owning module documentation before introducing the query.
    Identify consumed tables or views, relevant columns, latest-run Approval meanings, voiding and replacement rules, caller scope and the consumer behavior affected by changes.
    Referencing those documented database objects does not authorize imports from foreign private model or repository packages.
11. Preserve the computed nature of Approval state.
    Eligibility must account for the latest run, applicable steps, decisions, rejection and cancellation rather than treating a Claim as having a simple stored approved flag.
    Avoid multiplying Claim lines by joining several Approval runs, steps or decisions.
    Prefer a narrow owner-maintained read query or SQL view if expressing these meanings independently would duplicate a complex state machine.
12. Preserve money precision, category values, response ordering, reporting-period boundaries and zero actual spend for Budget-only rows.
    Preserve `null` Budget and `null` Variance for actual-spend rows with no matching Budget line.
    Do not change these product meanings as part of a structural refactor.
13. Direct reads exchange service coupling for schema and semantic coupling.
    Record that tradeoff explicitly, and coordinate affected consumer verification when consumed storage or business meanings change.
    Do not claim a performance improvement without measurement.
14. Cross-domain mutations continue through authorized domain-owned operations with existing audit and transaction behavior.
    Keep the closed-months integration used during Claim changes.
    Keep the prohibition on cross-schema foreign keys; read-only joins do not require new foreign keys.
15. Source the generated OpenAPI and frontend client from their normal generation process when the category permission or contract changes.
    Do not edit generated outputs manually.
16. Implement the category owner and the Variance dependency change as one coherent backend slice or in an order that never leaves a circular import between Budgets and Claims.
    The frontend application move can be delivered independently.
    Any choice of a Claims-owned narrow reporting read instead of direct SQL must retain an acyclic code dependency graph before the category owner is moved.
17. Restrict quality-tooling edits to compatibility changes required by moved files or changed contracts.
    The comprehensive design of future directory enforcement, lint, checks and tests is a follow-up discussion.

## Testing Decisions

- The detailed test strategy and broader lint/check architecture are deferred at the owner's request.
  This spec does not introduce a new testing framework or prescribe a comprehensive enforcement redesign.
- Existing checks remain the baseline during implementation.
  Prefer externally visible behavior over tests that mirror helper methods or file layouts.
- Existing HTTP behavior for category permissions, Claim validation, Variance eligibility, totals and department scope provides prior art.
  Existing browser journeys for sign-in, layouts, Claims and Variance provide prior art for user-facing compatibility.
- Preserve those behaviors while refactoring, and record the checks actually run with each delivered slice.
  Database validation must use the existing isolated test-database configuration.
  No future check is claimed to exist merely because this spec describes a required behavior.

## Out of Scope

- A repository-wide redesign of lint rules, checks, architecture enforcement, test placement or testing strategy.
- General public-interface checker hardening beyond compatibility work needed for this refactor.
- Moving Variance out of Core: its business logic has always belonged to Budgets in the inspected history.
- Moving all business pages into a global page directory.
- New Reporting, Finance or hybrid modules without an independent business responsibility.
- New category values, editable category administration, currency changes or revised financial formulas.
- Changes to Approval policy, Claim lifecycle, Month close semantics or domain-owned write authorization.
- Replacing every existing service read with SQL.
- Removing generic money/date helpers from Shared solely because business modules use them.
- Broad page decomposition, worker-wiring cleanup, CLI/Agent refactoring or changes to existing staged work.
- Microservice extraction, new deployment infrastructure or schema rewrites solely for frontend/backend symmetry.

## Further Notes

The policy refinement is recorded in ADR 0026.
The former ADR required service-interface access for foreign data; the new policy permits controlled reporting reads while preserving private code and mutation boundaries.
This is a deliberate tradeoff between service coupling and schema coupling, not permission to bypass business ownership.

The dedicated Variance service folder was introduced by the November 5 feature-package refactor.
The Variance method already belonged to the Budget service when the Budget capability first appeared on November 4.
Therefore the refactor addresses dependency shape and query placement, not a historical Core ownership violation.

Specification completion and documentation edits are not implementation completion.
Category ownership, application file moves, the reporting query and their verification remain pending until implemented and read back.
