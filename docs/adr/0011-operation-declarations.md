# 0011. Operation declarations

Status: Accepted, 2026-11-03.

## Context

Agents and the generated client need stable names and descriptions for each route, and screens do not show those.

## Decision

Each route declares its operation with `op(operation_id, description, confirm, tool, save)`.
The operation id follows `module.resource.verb`.
The declaration is emitted into the OpenAPI contract as `x-agent`, and the permission is emitted as `x-permission`.

## Consequences

- An architecture test fails when two routes share an operation id.
- A route that agents should not call sets `tool=False`, such as the sign-in redirects.
