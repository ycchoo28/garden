# Glossary

These are the words the code, the API, the screens and the docs use.
Each word means one thing.
The terms are grouped by area, and the last section lists the words to avoid.

## People and access

- **Person**: someone who can sign in, and who belongs to one department.
- **Department**: one of Design or Operations, which sets what a person may see.
- **Role**: a named bundle of permissions, such as `manager` or `finance`.
  The roles are `employee`, `manager`, `finance` and `admin`, and each module grants its permissions to them.
  `admin` holds every permission that any other role holds, and a test checks it.
- **Role grant**: one row that gives a person one role.
- **Employee**: the base role.
  Everyone who signs in holds it, so it grants the permissions every person has.
- **Permission**: a named right, such as `claims.submit` or `audit.view`.
  Each permission says whether an agent may hold it.
- **Person-only permission**: a permission with `agent_allowed=False`, which a CLI login never holds, such as `claims.decide`.
- **Data scope**: which departments a person may see.
  Finance and admins see all of them, and a manager sees their own department.
- **Session**: a person's browser sign-in, kept in the `garden_session` cookie.
  It ends after 30 minutes without a request.
- **Agent**: a program that acts for a person through a CLI login, such as a coding agent running the `garden` command or its MCP server.
- **CLI login**: an agent's sign-in, which is one bearer token for one person on one device.
  Every save made through it is marked as made by an agent, and the person can revoke it under CLI logins.
- **Device**: the machine a CLI login belongs to, named when the login is made.
- **Acting as agent**: a request that came through a CLI login, so the audit row and the confirmation gate treat it as the agent's.

## Platform

- **Module**: a folder that owns one part of the product.
  A **core module** is part of the foundation (users, audit, files, approvals and agent), and a **business module** is part of the product (claims, budgets and knowledge).
- **Schema**: the Postgres schema that holds one module's tables.
  Each module has one schema, and no foreign key crosses schemas.
- **Operation**: a route's stable name, `module.resource.verb`, with a description for agents.
  It appears in the contract as `operationId`.
- **Tool**: an operation that the `garden` command and the MCP server offer to an agent.
  An operation becomes a tool only when it is not marked `tool=False` and its permission is one the person holds.
- **Critical operation**: an operation marked `confirm=True`, which an agent may call only after the person confirms it.
- **Contract**: the OpenAPI document the API publishes, with `x-agent` and `x-permission` on each route.
  The **contract version** is its fingerprint, sent in the `X-Garden-Contract-Version` header.
- **Scaffold**: the template that `just new-module` uses to add a business module.
- **Public route**: a route that needs no permission, listed in the architecture tests, such as the health check and the sign-in redirects.
- **Problem document**: an RFC 9457 error response with the media type `application/problem+json`.
- **Audit event**: one saved change, with who made it, whether an agent made it, the operation, the target and a summary.
- **Confirmation**: a person's approval for one critical action taken by an agent.
  It is bound to that person, that CLI login, that operation and a digest of the request, works once and lasts ten minutes.
- **Confirmation gate**: the check that makes an agent's critical request wait for a confirmation.
  It answers `428 Precondition Required` until the person approves.
- **Stored file**: content saved once by its SHA-256, and shared by every upload of the same bytes.
- **Version**: the counter on a record that people or agents edit, which rejects a stale write with `409 Conflict`.
- **Replace**: a correction that writes a new version of a business record with a reason, and keeps the old one.
- **Void**: a correction that marks a business record as not counting, with a reason, and keeps its rows.
- **Ledger lock**: a Postgres advisory lock that keeps two writers from racing.
  The budgets module takes `budgets.lines` for budget line writes and `budgets.months` for month closes.
- **Model gateway**: the OpenAI-compatible endpoint under `/api/agent/model/v1`, which forwards to a configured upstream and counts usage.
  It is off until an upstream is set, and it forwards only the models on its allowlist.
- **Model usage**: the counts the model gateway keeps for each request, which are the time, the model, the status and the token numbers, and never the prompt or the reply.

## Approvals

- **Approval flow**: an ordered list of steps that a module uses to decide its subject, stored as rows.
  The seeded flows are `claims.expense` and `knowledge.batch`.
- **Flow step**: one step of a flow, which names a role, whether the step is scoped to the subject's department, and the amount above which it applies.
- **Subject**: the record an approval run decides, such as a claim or a knowledge batch.
- **Approval run**: one run of a flow for one subject.
  It copies the steps that apply, and its state is computed from its steps and decisions.
  Its states are `pending`, `approved`, `rejected` and `cancelled`.
- **Run step**: one step copied into a run when the run starts, so a later change to the flow does not change the run.
- **Decision**: a person's approve or reject on a run step, with who, when and a reason.
- **Starter**: the person who started an approval run.
- **Separation of duties**: the four rules the approvals core applies to a decision.
  The starter never decides, a person who decided an earlier step never decides a later one, the decider must hold the step's role, and a department-scoped step needs a decider from the subject's department.

## Budgets

- **Budget line**: the budget for one department, month and category, with each replacement kept as a version with its reason.
- **Category**: one of the fixed categories `equipment`, `marketing`, `meals`, `office`, `other`, `services`, `software` and `travel`.
  Budget lines and claim lines use this same list.
- **Year-month**: a month written as `YYYY-MM`, such as `2026-10`.
- **Actual spend**: the spend in a department, month and category, from approved claim lines only.
- **Variance**: the budget minus the actual spend for one department, month and category.
  A negative variance means spend above the budget.
- **Month close**: the closing of one year-month in budgets, with a reason.
  A closed month never reopens, and claims refuses to add, void or submit a claim line dated in it.

## Claims

- **Claim**: one request by a person for money spent on the company's behalf, with a title and its lines.
  Its status is computed from its latest approval run, and it is one of `draft`, `submitted`, `approved`, `rejected` or `withdrawn`.
- **Claim line**: one spend item on a claim, with a category, the date it was spent, an amount, a description and a receipt.
  A line is voided, never removed.
- **Receipt**: the stored file that a claim line refers to by its SHA-256.
  The file must be uploaded before the line is added.
- **Certify and submit**: the person-only step that sends a draft or rejected claim into its approval run.
- **Withdraw**: the claimant's cancellation of a submitted claim before anyone has decided it.
  It is a critical operation.
- **Decision**: an approval or rejection of a claim's current step.
  A rejection needs a reason, and the claimant may correct the claim and submit it again.
- **Approval queue**: the claims waiting for a decision that the caller may make.
- **Finance threshold**: the claim amount above which finance must decide after the manager.
  It is USD 500.00 in the seeded flow.

## Knowledge

- **Collection**: a set of documents that one access policy covers.
- **Collection rule**: one grant that lets people who hold a role read a collection, either in named departments or in all departments.
  A collection with no rules is readable by nobody.
- **Document**: one uploaded file, named within its collection.
  A batch replaces the live document with the same name.
- **Import batch**: one handover of files to a collection, which is prepared, submitted for approval and then applied live, or rejected or cancelled.
- **Batch file**: one file in an import batch, with its SHA-256, its size and its translation group.
- **Translation group**: a set of files that are translations of one document.
  A preview returns one result per group in the query's language.
- **Batch mode**: `full_set` or `partial`.
  A full set also closes the live documents it does not name, and a partial batch leaves them alone.
- **Prepare**: the background job that reads, chunks and embeds a batch's files.
- **Chunk**: one passage of a document, with its embedding and its keyword index.
- **Staged result**: the batch's mode, access rules, files and chunks, as a digest fixed when the batch is submitted.
  Approval refuses a batch whose staged result has changed.
- **Approve**: a person's decision that applies a submitted batch live.
  Only a person may approve.
- **Live**: a chunk is live when it has a validity span that is open now.
- **Validity span**: a period in which a chunk is live, from the time it opened to the time it closed, and the batch that did each.
  A chunk may have several spans, because undo reopens them.
- **Undo**: the reversal of an applied batch.
  It closes the spans the batch opened, reopens the spans it closed, and restores the access rules in force before it.
  It is refused when a later batch has changed the same collection.
- **Erase**: the person-only removal of a document from every batch, from search and from file storage, with a reason.
- **Risk trigger**: a flag on a batch with a large removal or a change to access rules.
  A risk trigger flags the batch for review and does not block its approval.
- **Preview search**: a search of one prepared batch before it is applied.
  It returns results under the caller's access rules.
- **Search**: a search of the live knowledge, or of the knowledge live at an `as_of` time, under the caller's access rules.
- **Search log**: the record of one search, with the person, whether an agent ran it, and the chunks it returned.
  Only a person may read it.
- **Hybrid search**: a search that fuses the nearest vectors, keyword matches and identifier matches with reciprocal rank fusion.

## Agent evals and telemetry

- **Eval**: a test of whether a real agent, with a real model, finishes a task with garden's tools.
- **Task**: one thing an eval asks the agent to do, kept as data in git, with its prompt, its person, its expected confirmation and its graders.
- **Suite**: whether a task is a `capability` task, which the agent may still fail, or a `regression` task, which it should always pass.
- **Trial**: one attempt at one task by one model, in its own cloned database and sealed workspace.
- **Eval run**: one execution of `just agent-eval`, made of many trials.
- **Grader**: a check that decides whether a trial passed.
  A state grader reads the result through the API, a trajectory grader checks what the agent did, and a judge grader asks a model to grade a knowledge answer.
- **Reference solution**: a script that drives the tools without a model to prove a task's graders.
  A perfect script must pass and a wrong script must fail.
- **pass^k**: a task passes for a model only when all of its k repeats pass.
- **Budget**: the most tool calls, time and tokens a trial may use before the runner stops it.
- **Finding**: something worth seeing that does not fail a trial on its own, such as a loop or the use of a tool that is not garden's.
- **Trace contract**: the span names and attributes that every producer of agent telemetry follows (ADR 0029).
- **Agent conversation**: one conversation in an agent harness, which is not a browser session.
- **Content capture**: the development and eval setting that lets the model gateway record prompts and replies in its spans.

## Terms to avoid

| Avoid | Say instead | Why |
| --- | --- | --- |
| user (for the human in the business) | person | The `users` module and the `/api/users` routes keep their names. |
| team | department | A department sets data scope, and a team has no meaning in the code. |
| approval (for an agent's critical action) | confirmation | An approval is a decision on a claim, batch or run, and a confirmation is the agent gate. |
| bot, AI user (for a CLI login) | agent, CLI login | The login is one person's token on one device, and the agent is the program that uses it. |
| expense report, reimbursement | claim | The code and the API use claim throughout. |
| edit (for a budget line or claim) | replace (budget line) or void (claim line) | Business records are replaced or voided, never edited in place (ADR 0005). |
| delete (for a claim or budget line) | void (or replace) | Only stored files can be deleted, and voiding keeps the history. |
| session (for an agent's conversation) | agent conversation | A session is a person's browser sign-in. |
| file (for a knowledge document) | document | A document is the knowledge item, and its batch file holds the uploaded bytes. |
| endpoint (for an operation) | operation | An operation is a route's named contract entry, and the command and MCP server show it as a tool. |
