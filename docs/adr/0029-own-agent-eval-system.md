# 0029. Our own agent eval system

Status: Accepted, 2026-11-13.

## Context

The `garden` command, its MCP server and the skills exist so that an agent can do real work for a person.
Unit and end-to-end tests prove that the tools behave, but not that a real model, in the real harness, picks the right tools, respects the boundaries and finishes the task.
The harness the repository sets up is dsh, so that is the agent to test.

Eval frameworks such as promptfoo are built around a prompt in and text out, checked by assertions on the reply.
A garden agent is stateful: it signs in as a person, changes records through operations, and sometimes waits for a confirmation.
The hard parts are the environment, the reset between trials and graders that read real state, and those are custom code in any framework.

## Decision

Garden has its own eval runner, written in Python inside the agent package's tests, and run with `just agent-eval`.

- **Purpose.** Evals are a development loop and a showcase, with model comparison as a side benefit.
  They are never a merge gate, because model results vary from run to run.
- **Tasks are data in git.** A task is typed Python data: a prompt, the person who asks, the expected confirmation behaviour (`approve`, `deny` or `none`), its graders, a `suite` (`capability` or `regression`), an `area` tag and optional budget overrides.
  A task moves from capability to regression by hand.
- **One trial, one world.** Each trial gets a database cloned from a seeded template with `CREATE DATABASE ... TEMPLATE`, its own API process, and a sealed workspace and home folder for dsh.
  The Agent logins are made once in the template, so a trial starts signed in.
  A failed trial's database is kept only with `--keep-failed`.
- **Real models through the gateway.** dsh talks to the model gateway, which forwards to the upstream named by `GARDEN_EVAL_UPSTREAM_URL` and `GARDEN_EVAL_UPSTREAM_KEY`, for each model in `GARDEN_EVAL_MODELS`.
- **Graders.** A trial passes only when every grader passes.
  - State graders read the result through the API with a finance or admin Agent login.
  - Trajectory graders check outcomes and hard rules, such as "no new submission" or "the final answer sends the person to the portal", never the exact order of tool calls.
  - An LLM judge grades knowledge answers only, and may answer `unknown`, which fails the trial.
- **Confirmations.** A task names the confirmation it expects, and the runner approves or denies it in a scripted browser.
  An unexpected confirmation fails the trial.
- **Repeats and the score.** Each task runs k=3 times per model by default.
  A cell shows the pass rate, coloured by pass^k (every repeat passed), with the median tool calls, tokens and duration.
  A task that every model fails on every repeat is flagged "check the task".
- **Budgets.** A trial stops at 40 tool calls, 5 minutes or 300,000 tokens, whichever comes first.
  The runner kills the whole dsh process group, records `budget_exceeded:<tool_calls|time|tokens>` and carries on.
  The same tool called with the same input 5 or more times is recorded as a loop finding.
  After 3 budget failures in one cell, the cell's remaining repeats are skipped and marked aborted.
- **Findings.** Use of a tool that is not garden's, such as `web_search` or `bash`, is recorded as a finding and does not fail the trial.
  A task may switch web tools off when it needs to.
- **Parallel trials.** Trials run in parallel up to `--jobs N`, which defaults to 2.
  Each trial is a self-contained unit with its own database, API port, home folder and budget.
- **Reference solutions.** Every task has a perfect script, and every boundary task has a wrong script, which drive the same tools without a model.
  CI runs them with the fake embedder to prove that the graders pass the right behaviour and fail the wrong one.
- **Layout.** Test code lives under `agent/tests/`: shared test code in `agent/tests/shared/`, the end-to-end run in `agent/tests/e2e/` and the evals in `agent/tests/evals/`.
  Nothing outside the tests imports test code, in the agent package and in the backend, and architecture tests enforce it.

Traces follow [ADR 0030](0030-agent-trace-contract.md), and results are stored and viewed as in [ADR 0031](0031-openobserve-for-evals-and-agent-telemetry.md).

## Consequences

- The runner owns the environment, which a framework would not have saved us from writing.
- A full run costs model tokens and takes time, so it runs on a developer's machine, not in CI.
- CI proves the graders and the tasks, not the models.
- A spike run showed why the budgets exist: on the boundary task "submit my claim", one model searched the web and then repeated the same three listing tools about 950 times instead of saying it cannot submit.
- A second harness needs only a thin adapter for launching, configuration, the final answer and tool names, because graders read the API and traces, never harness output.
