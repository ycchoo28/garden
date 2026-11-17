# Agent evals

This folder contains typed tasks, graders, reference solutions and the runner for Garden's agent evals.
See the [design and OpenObserve views](../../../docs/agent-evals.md) for the runner flow, model comparison grid and trial trace thread.

## Run the evals

Run the scripted reference solutions without a model:

```sh
just agent-eval --reference --fake-embedding
```

Reference mode requires each selected task to provide both `perfect` and `wrong` solutions, which run against the same Garden tools and real trial API used by model runs.
Each attempt gets a fresh clone of the seeded template database, an API process and a sealed dsh home and workspace.
The fake embedder is deterministic, runs on an isolated loopback port and does not download a model.
Without `--fake-embedding`, a knowledge run reuses the configured embedding service or starts it with `just embedding-up` if it must download the pinned model.
Use `--no-embedding` to skip selected knowledge tasks that require embeddings.
Use `--task <id>` to select a task, or repeat the option to select several.
Use `--keep-failed` to retain failed trial databases for inspection.

The default mode runs real models through the Garden gateway.
Set `GARDEN_EVAL_UPSTREAM_URL` and the comma-separated `GARDEN_EVAL_MODELS`, and set `GARDEN_EVAL_UPSTREAM_KEY` when the upstream requires it.
Use `--model <id>` to select one configured model, `--repeat <n>` to change the default three trials per task and model, or `--jobs <n>` to change the default concurrency limit of two.

Every run writes JSON results under `agent/dist/evals/runs/`.
OpenObserve receives trial records and traces when its local service is available, while JSON output remains available when telemetry export fails.
Start the service and its UI with `just eval-ui`.
Apply the checked-in dashboards and regression alert with `just eval-dashboards` after at least one trial has created the `garden_eval_trials` stream.
The alert also requires an existing OpenObserve notification destination named by `GARDEN_OPENOBSERVE_ALERT_DESTINATION`.
Stop the service with `just eval-down`; its named data volume and generated password file remain available for the next run.

## Add a task

Tasks are typed Python values that conform to the `EvalTask` protocol in [`task_types.py`](task_types.py).
Each task supplies a unique id, prompt, asking person, expected confirmation (`approve`, `deny` or `none`), suite, area, graders, reference solutions and any task-specific fields.
Optional fields set tool-call, time or token budgets, disable web tools, or mark a task as requiring embeddings.
Choose an asking person whose permissions match the real user workflow the task is meant to measure.

Use the closest existing task as the template.
Claim tasks are defined in [`tasks.py`](tasks.py) and auto-discovered `task_*.py` modules.
Knowledge tasks are defined in [`knowledge_tasks.py`](knowledge_tasks.py), set `requires_embedding` and judge criteria, and are added to that module's `TASKS` dictionary.
The [`registry.py`](registry.py) module combines the registered tasks and supplies their ids to the CLI.

Keep prompts grounded in a user outcome and make the required state observable.
Give each task a stable id and an `area` that groups related work in the dashboard.
Set `suite` to `capability` for a newly measured ability or `regression` for a known behavior being protected.
Add a task-specific pytest that exercises its expected boundary where practical.

## Add a grader

Every grader has a stable `id` and returns a result with a `pass`, `fail` or `unknown` verdict and a short reason.
A task passes only when all of its grader results pass.

Use a state grader to verify persisted records through the trial API with a read-capable Agent login.
Do not use the final answer alone to infer whether a business change happened.
Use a trajectory grader for response requirements or hard constraints that cannot be established from persisted state.
Knowledge answer graders may use an LLM judge; a judge result of `unknown` fails the trial.
Reference runs use scripted judges so grader behavior is repeatable.

Put state graders beside their task domain in [`grading.py`](grading.py) or the matching suite module, and add focused positive and negative grader tests.
The claim grader in `grading.py` shows API-backed state checks, while [`knowledge_grading.py`](knowledge_grading.py) shows the judge interface.

## Add reference solutions

Give every task selected by reference mode both a `perfect` solution and a `wrong` solution that demonstrates behavior the grader must reject.
The runner fails setup if either reference variant is missing.
Solutions receive a `ReferenceContext` and the typed task, call Garden tools through `context.call(...)`, and return the agent's final answer.
Use `person_call(...)` when a reference solution must perform a person-only API action.
The context supplies a fresh trial API, signed-in Agent login, database and workspace for each attempt.
Reference solutions do not call a model, but they exercise the real tool and grader path so they can prove that a grader accepts the intended outcome and rejects the counterexample.

For a new task, add both reference variants when the boundary has a meaningful negative case.
Run the selected pair with `just agent-eval --reference --task <id> --fake-embedding`.
The runner prints the grader reasons and writes the same trial records used by the dashboard and trace views.
