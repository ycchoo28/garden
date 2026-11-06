# 0026. Business ownership and reporting reads

Status: Accepted, 2026-11-06.
Refines the data-access consequence of [ADR 0003](0003-schema-per-module.md).

## Context

Garden's business modules own user outcomes, not individual tables or lists of dependencies.
Budget Variance remains a Budgets capability when it combines Budget amounts with approved Claims spending.
The earlier service-only read rule protects storage details, but can make a shared-database report depend on command services and merge unneeded data in application code.

## Decision

Organize business pages and their related components, queries, rules and tests under the owning business module.
Frontend and backend should align by business responsibility, without requiring identical source trees or placeholder modules.
Global home, auth/error screens, layouts and navigation composition belong to the application layer.
Shared code stays domain-neutral; business vocabulary such as expense categories needs an explicit business owner even when several modules consume it.

A module-owned reporting repository may directly read tables or views owned by other modules in the shared database.
It need not call another service solely because the report reads that service's tables.
Using an existing public service read remains valid when it is the simpler, appropriate contract.
SQL stays in repositories; routes and services do not gain database access.

Each cross-module database read must document the consumed tables or views, relevant columns and semantics, the consuming feature, authorization scope, and tests affected by schema changes.
Keep this read contract with the owning and consuming module documentation.
Approved database references do not grant access to private Python models, repositories or other implementation files.
Use documented SQL references or an intentionally exported read interface rather than importing foreign private ORM classes.

Apply caller scope and reporting filters in the query where practical, and preserve backend authorization regardless of the chosen read path.
For Variance, approved spending must respect the latest approval run, its applicable steps and decisions, cancellations, voided claim lines and the reporting date range.
Prevent duplicate amounts when joining approval history or replaced budget lines.
If semantics become complex or several consumers need them, prefer a maintained owner-provided query or SQL view rather than duplicating business rules.

Only the owning domain's authorized operations may change its records.
Cross-domain writes continue through domain-owned operations with appropriate transaction handling, audit and invariants.
Reporting-read permission does not allow a consumer to change Claim status, Budget amounts or Approval decisions directly.
Private repository boundaries, code dependency direction and the prohibition on cross-schema foreign keys remain in force.

## Consequences

- Direct reads exchange service coupling for explicit schema and semantic coupling.
  Changes to consumed storage or business meanings must run the consumer's affected tests.
- Knowledge Search's reads across its features are an example of the general reporting-read policy, rather than the only permitted exception.
- Module entry points must expose deliberate contracts; re-exporting arbitrary internals does not establish a useful boundary.
- Existing Variance service calls remain compliant.
  A focused report query is a refactor opportunity that requires behavior parity and measurement before any performance claim.
- The current expense categories in Shared and global frontend pages/layouts outside the application layer are pending alignment work.
  This decision records the target policy and does not claim those code changes are implemented.
  The [refactor spec](../refactors/business-ownership-alignment.md) describes the proposed code changes separately from the deferred lint, checks and test redesign.
