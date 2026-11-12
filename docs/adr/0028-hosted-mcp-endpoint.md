# 0028. Hosted MCP endpoint

Status: Accepted, 2026-11-12.

## Context

The `garden` command serves MCP over stdio for local agents, as recorded in [ADR 0023](0023-cli-discovery-and-mcp.md).
Hosted agents cannot start that local process, and a person needs a browser path to create a CLI login for them.

## Decision

The agent package serves the same tools over stateless MCP Streamable HTTP at `POST /api/agent/mcp`.
The backend mounts that ASGI app and supplies settings; it does not reimplement MCP.
The MCP handler reuses `handle`, the contract compiler and the Engine.
The Engine calls the API over loopback with the request's `ag_` bearer token through an injectable HTTP transport.
The hosted handler keeps no privileged token, ignores cookies, checks any `Origin` against the configured allowlist, and returns 401 with `WWW-Authenticate: Bearer` when the CLI login is invalid.
The contract is cached in the process by contract version.
A request fetches the contract only when the portal reports a version the process has not cached, and that fetch runs outside the shared lock so a slow portal does not block other requests.
Permissions are read once per request, not once per tool lookup.

The endpoint supports protocol versions `2025-06-18` and `2025-03-26` without a session id or server-initiated GET stream.
Notifications receive 202, and GET and DELETE receive 405.
The endpoint accepts one JSON-RPC message per POST.

Errors are split by where they happen.
A refusal before a JSON-RPC message is accepted is an RFC 9457 problem document (`application/problem+json`): 404, 400 for a bad header or framing, 403 for an origin or a CLI login that may not use MCP, 405 with `Allow`, 406, 413, 415, 401 with `WWW-Authenticate`, and 503 when no API address is known.
Once a message is accepted, its failure is a JSON-RPC error that carries the request's id, as MCP requires.
A body that is not JSON is a `-32700` parse error with HTTP 400, and a portal that cannot be reached is a `-32000` error with HTTP 502.
A portal that rejects the login with 401 still answers as a 401 problem document.

Critical calls stream the existing confirmation notification and then the result over SSE.
The engine waits in a worker thread and stops when the caller disconnects, before retrying the action.
Concurrent confirmation waits are capped per CLI login and in total, with the total below `THREAD_POOL_SIZE`, the 40-thread worker pool defined in the agent package and imported by the backend settings.

Hosted file fields use `{filename, content_type, data_base64}` objects.
The engine decodes only in-memory bytes in hosted mode and refuses strings and path-like filenames.
Stdio keeps its local path arguments.
The hosted request body limit derives from `files_max_bytes`, and upload operations still enforce their own size and type rules.

The `users.cli_login` row records a nullable expiry and whether the login came from the `garden` command or the browser.
Browser creation is a person-only operation that returns the raw `ag_` token once and commits its audit row in the same transaction.
The audit schema stays unchanged; the source is included in the event summary alongside the device and login creation time.

## Known limits

- **Approval check-then-act window.** The engine reads the confirmation's status, and when it is approved it sends the retry carrying the confirmation id.
  The portal spends the approval on that one retry, so the action runs at most once.
  Two gaps remain. A caller that disconnects after the cancel check but before the retry is sent leaves the action in place, because the portal cannot tell that caller from one that completed.
  The approval can also expire or be rejected between the status read and the retry, in which case the portal refuses the retry and the action does not run.
  Closing the first gap needs the portal to take the approval and run the action in one step, or to accept a cancellation of a retry it has not started. Neither exists today.
- **Params per transport.** Stdio keeps main's behavior: any falsy `params` (null, `[]`, `0`, `""`, `false`) counts as no params, and a truthy non-object is refused with `-32602`.
  The hosted endpoint is stricter: only absent or null params count as none, and any other non-object is refused with `-32602`.

## Consequences

- Operators control whether the endpoint is enabled, its API base URL, allowed origins, file-size limit and confirmation-wait caps through settings.
  An empty API base URL means the loopback address of the port the request arrived on, so a port moved by `just demo` needs no change.
  Set the base URL only when the backend cannot be reached at its own port on loopback.
- The CLI logins page creates browser logins, shows their source and expiry, displays each raw token once and keeps revocation unchanged.
- Hosted clients use a CLI login bearer token; OAuth 2.1 and a standalone service remain separate work.
- Changing the API base URL is sufficient to point the hosted handler at a separately deployed portal later.
