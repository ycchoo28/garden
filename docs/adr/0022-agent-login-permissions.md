# 0022. Agent login permissions

Status: Accepted, 2026-11-04.

## Context

A person uses the `garden` command, and an agent running on that device uses the same command.
The agent must act as the person, but it must not do everything the person can do.
Some actions, such as revoking a device's sign-in, must always be the person's own choice.

## Decision

A CLI login is a token tied to one person and one device.
It holds the person's permissions minus every permission the owner declares with `agent_allowed=False`.
Every permission declares `agent_allowed`, with no default, so a new permission is never granted to an agent by accident.
`agent.logins.revoke` is person-only, so an agent cannot sign its own device or another device out.
Browser sessions keep all of the person's permissions.

## Consequences

- An agent's reach is set by the permission declarations, and the architecture tests fail when a declaration is missing.
- `garden logout` calls `DELETE /api/agent/cli/token` with that device's own token so the portal revokes that login row, and then clears the token on the device; it still cannot revoke another device's login.
- A CLI login does not outlive its person, because the row is removed with the person.
