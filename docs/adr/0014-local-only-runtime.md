# 0014. Local-only runtime

Status: Accepted, 2026-11-03.

## Context

The point of garden is that someone can run it in minutes on a laptop, without company servers, company sign-in or private infrastructure.

## Decision

Docker Compose runs Postgres 17 with pgvector and a fake OpenID Connect provider.
The API and the web app run from the host with `just dev`.
Every port is bound to 127.0.0.1.
The repository contains no deployment, reverse proxy or secret store.

## Consequences

- CI runs the same checks with the same local services.
- The fake identity provider accepts any subject, so it must never be deployed.
