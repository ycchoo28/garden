# Agent evals

Run the first task's scripted reference trials with `just agent-eval --reference --task draft-one-receipt`.
Reference mode exercises the Garden MCP tools against the real trial API and runs both the perfect and deliberately wrong solutions without a model.
Each solution gets a fresh clone of the seeded template database, its own API process, and a sealed dsh home and workspace.
The state grader reads the resulting claim through the API with an admin Agent login.
The runner writes a full trial record for each solution to `agent/dist/evals/runs/<run-id>.json`.
Use `--keep-failed` to retain failed trial databases.

Tasks are typed data in `tasks.py`.
Each task names its asking person, prompt, expected confirmation, graders, suite, area, optional budget overrides, and optional web-tools-off setting.
Add reference solutions through the harness adapter and state graders through `grading.py`.
