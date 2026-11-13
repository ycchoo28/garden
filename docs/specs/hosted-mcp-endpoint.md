# Spec: Provider-hosted (Streamable HTTP) MCP endpoint

Spec for #6, agreed in discussion on 2026-11-12.

## Problem Statement

Agents can reach Garden only through `garden mcp`, a stdio MCP server that runs as a subprocess on the person's own machine. That requires the `garden` command to be installed and executable there. Cloud and server-hosted agent frameworks (LangGraph Cloud, OpenAI Agents SDK, hosted enterprise agent platforms) and zero-install clients cannot spawn a local subprocess, so they cannot use Garden's tools at all. A person who wants such an agent to act for them also has no way to get a CLI login without running the `garden` command's browser login on a machine they control.

## Solution

The portal serves the same tools over MCP Streamable HTTP at one operation, `POST /api/agent/mcp`. An agent sends `Authorization: Bearer ag_...` on every request. The portal answers with exactly the tools that the person behind that CLI login may use. Every tool call goes through the same permission checks, person-only filtering, confirmation gate and audit as a CLI call. A person can create a CLI login for a hosted agent from the CLI logins page in the browser: they name it, choose when it expires, copy the token once and revoke it later. Tools that upload a file take the file's content as base64 instead of a path, so a hosted agent can upload bytes it holds and can never make the server read its own disk.

## User Stories

1. As a person using a cloud-hosted agent, I want to point it at one Garden MCP URL, so that it can use Garden's tools without installing anything.
2. As a person, I want to create a CLI login from the CLI logins page in the browser, so that I can give a hosted agent a token without running the `garden` command.
3. As a person, I want to name the CLI login when I create it, so that I can tell my hosted agents apart in the list later.
4. As a person, I want to choose an expiry (30, 90 or 365 days, or none) for a CLI login I create in the browser, with 90 days as the default, so that a token pasted into a third-party platform stops working on its own.
5. As a person, I want to see the raw token exactly once, with a clear copy action and a warning that it will not be shown again, so that it is not stored or shown anywhere afterwards.
6. As a person, I want the CLI logins page to show whether each login was made by the `garden` command or in the browser, and when it expires, so that I understand what each one is.
7. As a person, I want to revoke a browser-made CLI login from the same page, so that a leaked or retired token stops working at once.
8. As a person, I want an expired CLI login refused just like a revoked one, so that expiry is a real boundary.
9. As a person, I want creating a CLI login to be a person-only permission, so that an agent can never mint more tokens for itself.
10. As an agent, I want `initialize` to return the protocol version, server info and tool capability, so that a standard MCP client can connect.
11. As an agent, I want `tools/list` to return exactly the tools my CLI login may use, with the same names, descriptions, input schemas and `readOnlyHint` / `destructiveHint` annotations as `garden mcp`, so that hosted and local agents see one consistent toolset.
12. As an agent, I want `tools/call` to run the operation as my person through my CLI login, so that the result is the same as calling it from the `garden` command.
13. As an agent, I want a request without a valid bearer token to get HTTP 401 with a `WWW-Authenticate: Bearer` header, so that my client knows it needs credentials.
14. As an agent, I want a revoked or expired token refused on the very next request, so that I get no stale access.
15. As a person, I want a change to my permissions to show in my agent's next `tools/list`, so that the toolset is never stale.
16. As an agent calling a critical operation, I want the reply to stream a notification with the confirmation link while the call waits, then the result once the person confirms, so that I behave just as I do over stdio.
17. As an agent, I want a rejected or expired confirmation to end the call with a clear error and the action not run, so that I can tell the person what happened.
18. As a person, I want a critical action whose agent disconnected before I confirmed it never to run, even if I confirm afterwards, so that nothing runs without a waiting caller.
19. As an operator, I want a limit on waiting confirmations per CLI login and in total, with a clear "too many pending confirmations" error when it is hit, so that one agent cannot use up the server.
20. As an agent, I want to upload a file by sending its filename, content type and base64 content in the tool arguments, so that I can upload bytes I hold.
21. As an operator, I want any hosted file argument that looks like a path refused, and no hosted call ever to read the server's filesystem, so that an agent cannot leak the server's files.
22. As an agent, I want an oversized request refused before it is decoded, with an error that states the maximum file size, so that I know how to fix the call.
23. As an operator, I want requests with an unexpected `Origin` header refused, so that a malicious web page cannot drive tools through a person's browser.
24. As an operator, I want browser session cookies ignored by the MCP operation, so that it acts only for a bearer-token CLI login and cross-site requests cannot reach it.
25. As an auditor, I want saves made through the hosted MCP operation marked as made by an agent, with the CLI login's device, source and creation time, so that the audit trail stays complete.
26. As an agent, I want malformed JSON, non-object messages, unknown methods and bad params to get standard JSON-RPC errors, so that my client can handle them.
27. As an agent, I want notifications (messages with no id) accepted with HTTP 202 and no body, so that the transport follows the Streamable HTTP rules.
28. As an agent, I want `GET` on the MCP operation answered with 405, so that my client knows there is no server-initiated stream.
29. As an agent sending a JSON-RPC batch, I want either correct per-message replies or one clear invalid-request error, so that the behaviour is defined. Follow the chosen protocol version.
30. As a developer of `garden mcp` over stdio, I want stdio behaviour unchanged, file paths included, so that existing local agents keep working.
31. As a maintainer, I want the HTTP MCP handler to live with the stdio server in the agent package and the backend only to mount it, so that it can later run as a standalone service by changing only the API base URL.
32. As an operator, I want the hosted MCP operation off or on by a setting, with its API base URL, wait limits and allowed origins also set by settings, so that I control the deployment.
33. As a maintainer, I want an ADR and README section that explain the hosted endpoint and how it differs from stdio, so that the design is recorded.

## Implementation Decisions

- **Placement.** The Streamable HTTP MCP handler is a self-contained ASGI application in the agent package, next to the stdio server. It reuses the existing transport-agnostic `handle(engine, message, notify)` dispatcher, the contract-to-tool compiler and the Engine, so tool lists, annotations and execution match `garden mcp`. The backend's agent core module mounts it at `/api/agent/mcp` and passes settings in. It does not reimplement MCP. The backend takes the agent package as a local path dependency. The architecture checks must still pass: core never imports business modules, and the mounted route is either declared with `op(...)` and a permission or listed as public with its reason, in the architecture tests.
- **Engine to API.** For each request the handler builds an Engine with the caller's bearer token, pointed at a configurable API base URL (default: the backend's own loopback address). The Engine runs in a worker thread because it is synchronous. Its HTTP client sits behind a small transport interface, so a later in-process transport, or a standalone service pointed at a public portal URL, needs no change to tool logic. The MCP layer never holds or uses a shared privileged token.
- **Auth.** The operation accepts only `Authorization: Bearer ag_...` and ignores browser session cookies. A missing or invalid token returns HTTP 401 with `WWW-Authenticate: Bearer` (realm and a `resource_metadata` parameter may be added later for OAuth). The `Origin` header, when present, must be in an allowed-origins setting, or the request is refused with 403. The token is validated by the backend's normal CLI login resolution, through the API calls the Engine makes, so revocation and expiry apply on the next request.
- **Transport.** Streamable HTTP only, stateless, no `Mcp-Session-Id`. Protocol versions are the ones the stdio server already supports (2025-06-18, 2025-03-26), with the same negotiation. `POST` carries one JSON-RPC message. A request gets either `application/json` or a `text/event-stream` reply. Notifications get 202. `GET` and `DELETE` get 405. The legacy 2024-11-05 HTTP+SSE transport is not supported.
- **Caching.** The published contract is cached per process by contract version (`X-Garden-Contract-Version`). The person's permissions from `/api/users/me` are fetched fresh on every request and never cached across requests or people.
- **Critical operations.** A `tools/call` that meets the confirmation gate (HTTP 428) streams over SSE: first the existing `notifications/message` with the confirmation link, then the final result or error. It waits until the confirmation's expiry, using the existing approval-wait logic. If the client disconnects, waiting stops and the request is never retried, so the action does not run. Waiting calls are capped per CLI login (default 3) and in total (default 20), both settings. The total must stay below the worker thread pool size, and a test enforces this. A call over a cap gets a JSON-RPC error that says too many confirmations are pending.
- **File arguments (hosted only).** In the hosted `tools/list`, every binary form field's input schema is an object `{ filename: string, content_type: string, data_base64: string }` instead of a path string. The Engine gains a bytes mode: hosted calls build multipart parts only from decoded in-memory bytes and never call the filesystem. A string value, or any path-like value, for a file argument is refused. Stdio keeps path arguments unchanged. The request body cap is derived from the existing `files_max_bytes` setting (about ×1.4 for base64 plus a small JSON allowance) and is checked before decoding. The backend's upload operations stay the final authority on size and type.
- **Schema change: `users.cli_login`.** Add nullable `expires_at` (existing rows and `garden` command logins stay null, meaning no expiry) and `source` (`cli` | `web`, existing rows backfilled to `cli`). The migration goes in the users module's Alembic branch. CLI login resolution refuses a login whose `expires_at` has passed, the same way it refuses a revoked one.
- **New operation: create a CLI login in the browser.** A person-only (`agent_allowed=False`) operation under the agent core module that takes a device label (the existing 120-character limit) and an expiry choice (30, 90 or 365 days, or none; default 90). It creates a `cli_login` row with `source=web`, stores only the SHA-256 hash, returns the raw token once and writes an audit row in the same transaction. Errors are RFC 9457 problem documents. It is `tool=False`.
- **Listing.** The existing "my logins" listing returns `source` and `expires_at`. `RequestLogin` carries `source`, so audit marking can tell `garden` command logins from browser-made ones. The audit table itself does not change.
- **Frontend.** The CLI logins page gains a "New CLI login" action: a form with label and expiry, a one-time token display with a copy button and a "this will not be shown again" warning, and source and expiry columns in the list. Revocation is unchanged. Regenerate the OpenAPI contract and the frontend client with `just api`. Never hand-edit generated files.
- **Settings.** Hosted MCP enabled flag, API base URL for the in-backend Engine, allowed origins, per-login and total confirmation-wait caps.
- **Docs.** A new ADR, "Hosted MCP endpoint", amends ADR 0023. It records the transport, auth, caching, waiting confirmations, the hosted file-argument difference and the standalone-ready placement. Add README and GLOSSARY updates (CLI login now also made in the browser, with a source and an optional expiry).

## Testing Decisions

- A good test drives the feature through its public seam and checks external behaviour: HTTP status, headers, JSON-RPC replies, SSE events, database rows and audit rows. It does not assert internal calls or private helpers.
- **Primary seam:** `POST /api/agent/mcp` on the running backend app, in backend integration tests with real CLI logins and seeded people. Cover: 401 without, or with a bad, revoked or expired token; cookies ignored; bad `Origin` refused; `initialize`; `tools/list` equal to the stdio tool list for the same person, apart from the documented file-argument shape; person-only operations absent; a permission change visible on the next list; a `tools/call` read and save with an agent-marked audit row; a critical operation streaming the confirmation link, then running after confirmation; rejection or expiry not running; client disconnect never running even if confirmed later; caps producing the documented error; JSON-RPC error cases; 202 for notifications; 405 for `GET`.
- **File safety:** a hosted file tool call with base64 content uploads the exact bytes. A string or path-like value (for example `/etc/passwd` or the backend `.env`) is refused, and the server never opens it. An oversized body is refused before decoding.
- **Browser CLI login:** creating one returns the token once and stores only its hash. Expiry defaults and choices behave as specified, an agent cannot call the create operation, the listing shows source and expiry, and revoking works.
- **Smoke:** one test connects the official MCP Python client over Streamable HTTP to the endpoint, lists tools and calls a read tool.
- **Stdio regression:** the existing stdio MCP tests still pass, and file paths still work over stdio.
- **Architecture:** `just architecture-check` and `just guardrail-test` pass, and the new route is covered by the operation and permission architecture tests.
- **Prior art:** `agent/tests/test_mcp.py` (dispatcher behaviour), `agent/tests/fake_portal.py` and `test_uploads.py` (uploads), `test_confirmation.py` (waiting confirmations), backend `core/agent/tests/test_cli_logins.py` (CLI login lifecycle) and `test_gate.py` (confirmation gate).

## Out of Scope

- MCP-native OAuth 2.1 and PKCE for hosted clients (protected-resource and authorization-server metadata, dynamic client registration, non-loopback redirects, a standards token operation). This goes to a separate follow-up issue. The 401 here is shaped so it can be extended.
- A standalone `garden mcp --http` command or separately deployed MCP service. The placement makes it easy, but it is not built here.
- An in-process ASGI transport for the Engine.
- The legacy 2024-11-05 HTTP+SSE transport, and server-initiated streams over `GET`.
- Changes to the confirmation gate, permissions model or audit table.
- Rate limiting beyond the confirmation-wait caps.

## Further Notes

- Use glossary terms: CLI login (also for browser-made tokens), confirmation (not "approval") for the critical-action gate, critical operation, operation and tool.
- Follow the repository rules in AGENTS.md: no em dash characters, no hand-edited generated files, surgical changes, `just quick` before handover and `just check` before the PR.
