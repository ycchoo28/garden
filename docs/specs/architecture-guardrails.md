# Garden architecture guardrails

Status: Specified; implementation pending.
Date: 2026-11-10.
Architecture policy: [ADR 0026](../adr/0026-business-ownership-and-reporting-reads.md).
Follows: [business ownership and reporting-read refactor](../refactors/business-ownership-alignment.md), which deferred this work.

## Problem Statement

Garden's ownership rules are written down, and the business-ownership refactor moved the code into the agreed shape.
Most of that shape is held only by prose and by the care of whoever makes the next change.
A developer or coding agent can break a rule without any check failing.

The checks that exist have gaps.
The backend public-interface check sees only one import form, so a plain or relative import into another module's internals passes.
The list of private feature repositories is kept by hand and already misses the Variance reporting repository.
Nothing detects a dependency cycle between business modules.
The frontend application layer can import any file inside a module instead of its public entry point.
Nothing ties a documented reporting read contract to the SQL that uses it.
Shared can hold business policy as long as it imports nothing, which is how expense categories ended up there before the refactor.
An Operation is non-critical unless someone remembers to mark it.
The agent skills name Operations by hand with no check against the Contract.
The Contract check rewrites generated files in the working tree, which broke a browser run that started at the same time.

The documentation also claims more than the checks deliver.
The code patterns say an import contract keeps every feature repository private, while the contract omits one.
Some rules cannot be decided by any check, such as who owns a business outcome, and today nothing states which rules those are or how they are reviewed.

## Solution

Give every architecture rule one stated means of enforcement, and make that means as strong as the rule allows.
The order of preference is: impossible by construction, a static check, a behavior test, a review checklist, and prose only as a last resort.

Checks discover Modules, features and subpackages themselves, so a new one inherits every rule without anyone editing a list.
The only hand-kept lists are exceptions, so each exception is a visible, reviewable change.
The guardrails are themselves proven: the Scaffold job plants known violations in a generated Module and confirms that each check rejects them.
Static checks run in the fast check tier, so a boundary violation is reported in seconds and without Postgres.
Each failure names the rule, the offending file, the permitted alternative and the document that explains it.
The documentation names the check behind each rule, or says the rule is held by review.

## User Stories

1. As the repository owner, I want every architecture rule to name how it is enforced, so that I know which rules are guaranteed and which depend on review.
2. As the repository owner, I want new Modules to inherit every rule automatically, so that protection does not depend on someone updating a list.
3. As the repository owner, I want every exception recorded in a small number of known lists, so that weakening a boundary is always a visible change.
4. As the repository owner, I want proof that each guardrail rejects a real violation, so that a green check means something.
5. As a coding agent, I want boundary violations reported by the fast checks, so that I can correct them before running the database tests.
6. As a coding agent, I want each failure to state the rule and the permitted alternative, so that I can fix the cause instead of guessing.
7. As a coding agent, I want the working guide to match what the checks enforce, so that I do not rely on a rule that nothing holds.
8. As a backend maintainer, I want every import form into another Module's internals rejected, so that the package interface is the only way in.
9. As a backend maintainer, I want every feature repository private to its feature without being listed, so that a new reporting repository is protected on the day it is added.
10. As a backend maintainer, I want dependency cycles between business modules rejected, so that an ownership move cannot quietly create one.
11. As a backend maintainer, I want database access limited to repositories and models, so that a new kind of file cannot bypass the layer rule.
12. As a Claims maintainer, I want any reporting query that reads Claims tables to appear in a declared read contract, so that I can see who depends on my storage.
13. As a Claims maintainer, I want other modules prevented from writing Claims rows through raw SQL, so that reporting-read permission never becomes write access.
14. As a Budget maintainer, I want the Variance read contract checked against the migrated database, so that a stale contract is caught when a column changes.
15. As a person reading Variance, I want the report's approval logic proven equal to the Approvals rules, so that a resubmitted or cancelled Claim is never counted wrongly.
16. As a department Manager, I want a test for each role's Data scope on Variance, so that a query change cannot widen what I or others may see.
17. As a platform maintainer, I want every Operation to state whether it is a Critical operation, so that a new destructive route cannot default to unconfirmed.
18. As a platform maintainer, I want Shared rejected when it names business concepts, so that business policy cannot hide in plumbing again.
19. As a platform maintainer, I want Shared limited to types and helpers, so that permissions, routes, tables and pages always have an owning Module.
20. As a frontend developer, I want the application layer to import a Module only through its public entry point, so that Module internals can change freely.
21. As a frontend developer, I want business pages rejected outside their owning Module, so that the application layer does not become a second home for business screens.
22. As a frontend developer, I want the pages allowed in the application layer listed explicitly, so that adding a global page is a deliberate decision.
23. As a frontend developer, I want hand-written server URLs rejected, so that every call goes through the generated client and follows the Contract.
24. As a Garden developer, I want each frontend module name to match a backend module, so that I can find both halves of a capability by one name.
25. As a Garden developer, I want no check that demands matching file trees, so that alignment does not create empty folders.
26. As an agent package maintainer, I want every subpackage placed in the layer order, so that a new subpackage cannot sit outside the dependency rules.
27. As an Agent acting for a person, I want every Operation named in a skill to exist in the Contract, so that the skill never tells me to run a command that is gone.
28. As a developer running checks in parallel, I want the Contract check to leave my working tree untouched, so that it cannot break a build or a browser run that is in progress.
29. As a reviewer, I want a fixed set of ownership questions for each change, so that the rules no check can decide are still examined.
30. As a reviewer, I want single-consumer Shared helpers reported, so that I can ask whether they belong to that consumer.
31. As a documentation reader, I want the no-em-dash rule checked, so that the authoring rule does not depend on memory.
32. As the repository owner, I want existing tests rewritten where they conflict with a new rule, so that no rule needs a test exemption.

## Implementation Decisions

### Principles

1. Each rule has exactly one primary means of enforcement, chosen from this order: by construction, static check, behavior test, review checklist, prose.
2. Checks discover the units they govern from the source tree and the composition root.
   A check never lists today's Modules, features, repositories or subpackages.
3. A hand-kept list is allowed only for exceptions, and each entry carries a short reason.
   The existing Public route and Critical operation lists follow this pattern already.
4. Record the enforcement model in one new ADR that refines ADR 0026, and link it from the working guide and the code patterns.

### Backend imports

5. Replace the hand-written public-interface check and the enumerated repository-privacy contract with one check built on the real import graph, using the import-graph library the import contracts already depend on.
   It must treat absolute, plain, relative and function-level imports alike.
6. That check enforces four rules for every discovered Module and feature.
   A Module is imported by other Modules only through its package interface.
   A feature repository is imported only from inside its own feature.
   Business modules form no dependency cycle.
   Only repositories, models and the Shared database plumbing may import the database library or session.
7. Keep the existing contracts that Core never imports business modules and that Shared imports neither Core nor business modules.
   They already cover new Modules.
8. Keep the existing exemptions: tests inside a Module may import that Module's internals and other Modules' parts, and the composition root may import everything.
9. Inverting the database-access rule may expose files that touch the database today outside a repository, most likely in Knowledge batch processing.
   Inspect these first.
   Move the access into a repository where that is a small change, and otherwise record a named exception with its reason.
10. Keep the existing rule that a feature service imports only the model classes its own repository imports.

### Reporting read contracts

11. The read contract table in a consuming Module's documentation becomes the declaration the check reads, in a fixed column layout: owner, table, columns and meaning.
    The prose around the table stays free-form.
12. A static check extracts every schema-qualified table reference from each repository's SQL.
    A reference to a table outside the Module's own Schema must appear in that Module's declared read contract.
13. The same check rejects any data-changing SQL statement that targets a Schema the Module does not own.
14. A database-backed check confirms that every declared table and column exists after migrations.
15. These checks do not judge business meaning.
    Changes to what a consumed column means remain covered by behavior tests and review.

### Critical operations

16. Remove the default from the Operation declaration's confirmation setting, so every route states whether it is a Critical operation.
    State the current value explicitly on every existing route and in the Scaffold templates.
    The published Contract must not change as a result.
17. Keep the existing explicit list of Critical operations in the architecture tests as the reviewable record.

### Shared stays domain-neutral

18. Apply the same three checks to backend Shared and frontend Shared, from one script with one word list.
19. Imports: Shared imports nothing above it.
    Remove the frontend allowance for Shared to import the generated client, which Shared does not use.
20. Vocabulary: Shared file names and code may not use business words.
    Build the word list from the business module names, their feature names and the Glossary sections named after business modules.
    Match whole words in singular and plural forms, in identifiers, strings and comments.
21. Kind of thing: Shared holds reusable tools and never business facts.
    Generic UI components such as a data table, permission and role types, the permission check, navigation types, database helpers and error classes all belong in Shared.
    Backend Shared declares no specific permission or role values, routers, database table definitions or SQL that names a Schema.
    Frontend Shared holds no routed pages, route definitions, specific permission strings or server calls.
    A reusable component that knows a business concept belongs to the Module that owns the concept and is exposed through that Module's public entry point.
22. The vocabulary check has no exemption for tests.
    Rewrite the Shared navigation unit test to use invented module names, and reword the one Shared comment that uses a business module as its example.
23. Glossary terms that are also generic, such as Year-month, go in an explicit exception list with a reason.
24. Report Shared helpers that only one Module imports as information in the check output.
    This never fails the check, because the earlier refactor chose to keep generic money and month helpers in Shared.

### Frontend placement and imports

25. Add an entry-point rule to the existing boundaries lint: code outside a Module or Core package imports it only through its public entry point.
26. Add a structure check: routed pages live only under a business module, a Core package or the application layer, and there is no global pages root.
27. The pages allowed in the application layer are an explicit list, starting with the home page and the signed-out page.
28. Add a lint rule that rejects hand-written server URLs and direct network calls outside the generated client.
29. Add a name check: every frontend business module and Core package name exists as a backend package of the same group.
    Do not check for matching files, folders or features.
30. Keep the existing rule that a frontend module imports only the generated client, Shared and itself.
    If a real need for sibling imports appears later, relax it to public entry points only and add a cycle check at that time.

### Agent package

31. Make the layer contract exhaustive, so a subpackage missing from the layer order fails the check.
32. Rewrite the agent public-interface check to discover subpackages instead of listing them.
33. Add a test that every Operation id named in an agent skill exists in the published Contract.

### Contract and documentation

34. Make the Contract check non-mutating: generate into a temporary location, compare with the working tree, and report the stale files.
    The existing regenerate command remains the only way to update the generated files.
35. Add a check that no tracked text file contains the em dash character, next to the existing deny-list check.
36. Update the working guide and the code patterns so each rule names its check or says it is held by review.
    Remove the hand-written inventory of feature folders from the code patterns, because it duplicates what the tree shows and will drift.

### Feedback and messages

37. Run every static architecture check in the fast check tier, with no need for Postgres.
    Database-backed checks stay with the backend test run.
38. Leave the commit hook as it is: fast checks on staged files only.
39. Every guardrail failure prints the rule's name, the offending file and line, the permitted alternative and a link to the governing document.

### Proving the guardrails

40. Extend the existing CI Scaffold job.
    After it generates a throwaway Module, it plants one violation per guardrail in turn and asserts that the matching check fails and names the rule.
41. Each guardrail also has a permitted example beside each prohibited one, so a check that rejects everything cannot pass as correct.

### Review tier

42. Add the eight ownership review questions from the architecture discussion to the repository documentation as a checklist.
43. The working guide requires an agent to answer the checklist at handover when a change adds a file to Shared or the application layer, adds a Module, widens a public entry point or adds a reporting read contract.
    This covers business ownership, Shared neutrality beyond vocabulary, and the quality of public entry points, which no check can decide.

### Delivery order

44. Deliver in reviewable slices in this order.
    First the backend import-graph check, the move of static checks into the fast tier and the planted-violation proof.
    Second the Shared neutrality checks on both sides.
    Third the frontend placement, entry-point, URL and name checks.
    Fourth the reporting read contract checks and the Variance behavior tests.
    Fifth the Critical operation default, the agent package checks and the Contract check.
    Last the documentation updates, the ADR and the review checklist.
45. Any existing code that a new check rejects is fixed in the same slice as the check, or recorded as a named exception.
    No slice lands with a check disabled.

## Testing Decisions

- The single seam for guardrail tests is the check commands themselves, run against a tree that contains a planted violation.
  A good guardrail test asserts the outcome a contributor sees: the check fails, names the rule and points at the file.
  It does not assert how the checker walks the tree.
- Each guardrail has at least one prohibited and one permitted example.
  Import rules cover each import form: absolute, plain, relative and inside a function.
- Prior art is the CI Scaffold job, which already generates a Module and runs every check on it, and the existing architecture tests that walk every installed route.
- Rewrite existing tests where a new rule requires it instead of exempting them.
  The backend public-interface test is replaced by the import-graph check.
  The agent public-interface test is rewritten to discover subpackages.
  The Shared navigation unit test is rewritten with invented module names.
- Variance approval parity is a behavior test through the existing HTTP seam against real Postgres.
  It builds Claims in each approval situation and asserts that Variance counts exactly the Claims the Approvals rules call approved.
  The situations are: no run, pending, approved, rejected, cancelled, resubmitted after rejection, and several runs for one Claim.
- Data scope is a behavior test through the same seam, with one case each for a Manager, Finance, an Admin and a person with no Budget permission.
- Check first whether the tests added by the ownership refactor already cover a situation, and add only what is missing.
- Prior art for behavior tests is the existing Budget line, Claims lifecycle and Variance tests, which use the shared signed-in client fixture.
- The full backend suite continues to run on every pull request.
  This spec adds no selective test routing.

## Out of Scope

- Branch protection or required status checks on the default branch.
  That is a repository setting for the owner, not a code change, although without it a failing check does not block a merge.
- A vocabulary rule for Core.
  It is a natural partner to the Shared rule, but Core has not been scanned and Approvals stores a business subject type as data.
- Moving the money and month helpers out of Shared.
- Per-Module database roles or grants to enforce write ownership inside Postgres.
- Selective test routing, or new isolation machinery for concurrent runs beyond the non-mutating Contract check.
- Agent harness hooks, such as blocking a command inside one coding tool.
  The repository checks are the backstop for every tool.
- Any check on matching frontend and backend file trees, one module per page, or pages that show several Modules' data.
  The architecture discussion rejected each of these.
- Dynamic imports, which a static import graph cannot see.
- New features, changed business behavior or changes to the published Contract.

## Further Notes

Three decisions in this spec are the author's recommended defaults and were not explicitly confirmed by the owner: the planted-violation proof in the Scaffold job, making the review checklist required instead of advisory, and removing test exemptions from the vocabulary rule.
Confirm or change them before implementation starts.

The vocabulary rule would have caught the original misplacement of expense categories in Shared, which no import rule could see.
It cannot catch business policy hidden behind a neutral name, so the review question remains: would this file survive unchanged if the business modules were replaced by different ones?

A quick scan found the money and month helpers imported only by Budgets, and found no importer of the Shared random source inside the application code.
Neither finding was verified in depth.
Both are examples of what the single-consumer report would surface for review.

The facts in the Problem Statement were checked against the merged default branch on 2026-11-10.
