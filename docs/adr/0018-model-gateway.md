# 0018. Model gateway

Status: Accepted, 2026-11-04.

## Context

Agents need a model to work with, and the portal should hold the upstream key and set the limits.
Each person's usage should be visible to them, and the portal should not keep what they asked or what the model answered.

## Decision

The gateway is an OpenAI-compatible endpoint under `/api/gateway/v1` that forwards requests to an upstream set in configuration.
The upstream key stays on the server, and the gateway is off until an upstream is configured.
Only models on the configured allowlist are forwarded, and streaming is refused for now.
Each CLI login is limited to a number of requests per minute, counted from the usage rows it has written.
Each person is limited to a number of tokens per day, counted from the same rows, on the UTC day.
The gateway stores counts only: the time, the model, the status and the token numbers, never the prompt or the reply.
The gateway is a core module, because every agent reaches it through the same login.

## Consequences

- Tests use a fake upstream, so CI needs no model key.
- The per-minute limit counts accepted requests, so a burst of concurrent requests can pass the limit slightly.
- Usage is recorded for failed upstream calls too, with zero tokens, so retries cannot hide spend.
