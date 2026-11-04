# 0017. CLI discovery and MCP

Status: Accepted, 2026-11-04.

## Context

An agent should find what it may do without a hand-written client for each portal change.
The contract that the API publishes already says which operations exist, what they need and which ones are for agents.

## Decision

The `garden` command builds its command tree from the published contract.
It includes an operation only when the operation is marked `tool: true` and the signed-in person holds its permission, as listed by `/api/users/me`.
The portal sends a contract version in the `X-Garden-Contract-Version` header, and the command refreshes its tree when that version changes.
The portal sends a minimum command version in `X-Garden-Min-CLI-Version`, and the command refuses to run below it and says how to upgrade.
The same tools are served over stdio as an MCP server, with `readOnlyHint` for reads and `destructiveHint` for operations that need confirmation.
The skill file that tells an agent how to use the command ships inside the package.

## Consequences

- An operation that is not marked as a tool never appears in the command or in MCP.
- A new permission or operation reaches agents with the next contract, and no client code changes.
- Each contract change costs one fetch of the contract, and the command caches it per version.
