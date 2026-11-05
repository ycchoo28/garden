# Agent (core)

## What it owns

- The approval and management of CLI logins from the browser: a person starts a login in a terminal, approves it in the browser, and can list and sign out their own logins.
  The login's records and token exchange are owned by `users` (see [users](../users/README.md)).
- The confirmation gate (ADR 0012): a critical action called with a CLI login runs only after the person approves that exact request in the browser.
  Its one table is `confirmation`.
- The model gateway (ADR 0024): an OpenAI-compatible API for CLI logins, which forwards each chat call to the configured upstream model.
  The gateway answers only for models on its allowlist and counts each person's usage.
  Its usage rows are in `usage`.
- Limits per person: a requests-per-minute limit per CLI login and a tokens-per-day allowance per person, reset at midnight UTC.
- Schema: `agent`, tables `confirmation` and `usage`.
  Prompts and completions are never stored.

## Interface

Import from `app.core.agent` only: `router`, `ROLES`, `PERMISSIONS`, `confirm_agent_action`, `CONFIRMATION_HEADER` and `get_upstream_transport`, which tests use to pass a fake upstream.

- `GET /api/agent/cli/authorize` (no sign-in): starts a CLI login and redirects the browser to the identity provider.
- `POST /api/agent/cli/approvals` (no sign-in): finishes a CLI login with the code and the PKCE verifier, and answers the token once.
- `GET /api/agent/my-logins` (`agent.logins.view`): the caller's CLI logins, newest first.
- `POST /api/agent/my-logins/{login_id}/sign-out` (`agent.logins.revoke`, browser only): revokes one of the caller's CLI logins.
- `GET /api/agent/my-confirmations/{confirmation_id}` (`users.self`): one confirmation and its status.
  The CLI polls it after a 428, and only the person who asked may read it.
- `POST /api/agent/my-confirmations/{confirmation_id}/decision` (`agent.confirmations.decide`, browser only): approves or rejects a pending confirmation.
- `GET /api/agent/model/v1/models` (`agent.model.use`): the models the gateway forwards.
- `POST /api/agent/model/v1/chat/completions` (`agent.model.use`): an OpenAI chat completion for a model on the allowlist.
- `GET /api/agent/model/my-usage` (`agent.model_usage.view`): the caller's requests and tokens today, with the daily allowance.

Critical routes declare `confirm_agent_action(...)` after their `require(...)`.
Today these are the file delete, the claim withdrawal, the budget month close and the batch undo.
A critical route answers 428 "Confirmation Required" to a CLI login until the person approves that exact call.
The 428 names the confirmation id and a URL to open, which is `{public_url}/agent/confirmations/{id}` in the web app.

## Key decisions

- Only agent requests pass through the gate.
  Browser requests are not checked.
- The first agent attempt without a confirmation id creates a pending row and answers 428 with its id.
  The person approves it in the browser, and the agent retries with the `X-Garden-Confirmation` header.
- A confirmation is bound to the person, the CLI login, the operation, the method, the target and a digest of the body, and it works once.
  It expires after ten minutes.
- A CLI login never holds `agent.confirmations.decide` or `agent.logins.revoke`, so an agent cannot approve its own request or sign out its person's logins.
- The person reads a summary of the request before deciding, cut at 2000 characters.
- The approval of a CLI login redirects only to a loopback callback (`127.0.0.1`), so the code never leaves the person's machine.
  The check lives in `users`.
- The model gateway counts usage only.
  The limits are checked before a request is forwarded, and a request over the per-minute limit answers 429 with `Retry-After: 60`.
- A failed upstream call is still recorded, with status `upstream_error`.
- With no upstream configured the gateway answers 503, and it refuses streaming.
- Token counts that are not whole numbers count as zero.
