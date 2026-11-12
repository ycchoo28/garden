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
The contract is cached in the process by contract version, while permissions are fetched on every request.

The endpoint supports protocol versions `2025-06-18` and `2025-03-26` without a session id or server-initiated GET stream.
Notifications receive 202, and GET and DELETE receive 405.
The endpoint accepts one JSON-RPC message per POST.

Critical calls stream the existing confirmation notification and then the result over SSE.
The engine waits in a worker thread and stops when the caller disconnects, before retrying the action.
Concurrent confirmation waits are capped per CLI login and in total, with the total below the 40-thread worker pool.

Hosted file fields use `{filename, content_type, data_base64}` objects.
The engine decodes only in-memory bytes in hosted mode and refuses strings and path-like filenames.
Stdio keeps its local path arguments.
The hosted request body limit derives from `files_max_bytes`, and upload operations still enforce their own size and type rules.

The `users.cli_login` row records a nullable expiry and whether the login came from the `garden` command or the browser.
Browser creation is a person-only operation that returns the raw `ag_` token once and commits its audit row in the same transaction.
The audit schema stays unchanged; the source is included in the event summary alongside the device and login creation time.

## Consequences

- Operators control whether the endpoint is enabled, its API base URL, allowed origins, file-size limit and confirmation-wait caps through settings.
- The CLI logins page creates browser logins, shows their source and expiry, displays each raw token once and keeps revocation unchanged.
- Hosted clients use a CLI login bearer token; OAuth 2.1 and a standalone service remain separate work.
- Changing the API base URL is sufficient to point the hosted handler at a separately deployed portal later.
