# 0010. Deny by default

Status: Accepted, 2026-11-03.

## Context

A route added without a permission check is a hole that nobody notices until it is used.

## Decision

Every route requires a permission through `require(...)`.
The only exceptions are the routes listed as public in the architecture tests: the health check, the browser sign-in and sign-out routes.
A permission that no module declares stops the app from starting.

## Consequences

- An architecture test walks every installed route and fails on a route that has neither a check nor a public entry.
- Adding a public route means changing that list, and a reviewer sees the change.
