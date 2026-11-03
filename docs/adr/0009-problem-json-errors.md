# 0009. Problem JSON errors

Status: Accepted, 2026-11-03.

## Context

Clients and agents need errors they can read the same way on every route, and the browser needs one shape to show.

## Decision

Errors are RFC 9457 problem documents with the media type `application/problem+json`.
Code raises a `ProblemError` subclass, such as `Unauthorized`, `Forbidden`, `NotFound`, `Conflict`, `Unprocessable`, `ConfirmationRequired` or `PayloadTooLarge`.
The contract lists these responses on every route.

## Consequences

- A 401 from an expired session sends the browser back to sign-in.
- A new error is a new subclass, not a new response shape.
