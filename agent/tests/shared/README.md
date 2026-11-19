# Shared test code

This folder holds the helpers that more than one kind of test uses: the fake portal, the CLI
driver, the stack's processes and portal calls, the transcript, the headless sign-in, the dsh
launch and patch overlay, and the scripted browser approvals.

It is test-only. It is not the backend's `Shared` layer (`backend/app/shared`), which is product
plumbing. Nothing under this folder ships.

Rules, enforced by `agent/tests/architecture/test_test_code_boundaries.py`:

- Shared test code imports neither `tests.e2e` nor `tests.evals`.
- Product code (`agent/src`, the agent's build scripts and installers, and `backend/app`) never
  imports test code.
