# Agent evals

Run all scripted reference trials with `just agent-eval --reference --fake-embedding`.
Reference mode exercises the Garden MCP tools against the real trial API and runs both the perfect and deliberately wrong solutions without a model.
Each solution gets a fresh clone of the seeded template database, its own API process, and a sealed dsh home and workspace.
The claim state grader reads the resulting claim through the API with an admin Agent login.
Knowledge tasks search the demo policy library as the asking person and use a scripted judge in reference mode.
The fake embedder runs on an isolated loopback port and does not download a model.
Without `--fake-embedding`, a knowledge run reuses the configured embedding service or starts it with `just embedding-up`, which downloads the pinned model when it is missing.
Use `--no-embedding` to skip every selected knowledge task; the command prints the skipped task ids.
Use `--task <id>` to select one task, or repeat `--task <id>` to select several.
The runner writes a full trial record for each solution to `agent/dist/evals/runs/<run-id>.json`.
Each trial is also exported as a trace and a queryable log to OpenObserve when it is available; gateway token usage is read from the trial API and the JSON summary is written even when export fails.
Use `--keep-failed` to retain failed trial databases.

Tasks are typed data under `agent/tests/evals/`.
Each task names its asking person, prompt, expected confirmation, graders, suite, area, optional budget overrides, and optional web-tools-off setting.
Claim tasks and state graders live in `tasks.py` and `grading.py`.
Knowledge tasks and their reference solutions live in `knowledge_tasks.py`, and their LLM judge lives in `knowledge_grading.py`.
The judge returns `pass`, `fail` or `unknown`; `unknown` fails the trial.
Reference trials use scripted judge models, while a real judge needs the OpenAI-compatible `/v1` base URL in `GARDEN_EVAL_JUDGE_URL`, plus `GARDEN_EVAL_JUDGE_KEY` and `GARDEN_EVAL_JUDGE_MODEL`.
