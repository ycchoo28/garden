# 0031. OpenObserve for evals and agent telemetry

Status: Accepted, 2026-11-13.

## Context

Eval results need a place to be compared across runs, tasks and models, and the traces of [ADR 0030](0030-agent-trace-contract.md) need a place to be read.
The same place should later hold monitoring and alerts for agents in daily use.
The options were a static report, Grafana with eval tables in Postgres, or OpenObserve alone.
A spike stored real dsh trials in OpenObserve and checked trial records, traces, the thread view of a trial, dashboards and alerts.

## Decision

OpenObserve is the one store and viewer for eval results and agent telemetry.

- It runs as one container in Docker Compose on 127.0.0.1:55086 (UI and OTLP), with its data in a named volume, so [ADR 0014](0014-local-only-runtime.md) still holds.
  It is not part of `just up`; `just agent-eval` starts it, `just eval-ui` opens it and `just eval-down` stops it.
- Trial records are logs in an eval stream, and trial traces are traces, both sent over OTLP.
  Each trial is one trace whose root span is named `task / model / repeat`.
- Dashboards are code in git, built and applied by a script, and never edited only in the UI.
- Tasks stay in git ([ADR 0029](0029-own-agent-eval-system.md)), never in the platform.
- The runner also writes a JSON summary of each run to disk, so the terminal summary and CI never depend on OpenObserve.
- Postgres keeps no eval tables, and the application database is never used for eval results.

## Consequences

- One service holds evals, traces, dashboards and alerts, which is less to run than Grafana plus a database plus a trace store.
- The trace views (waterfall, thread) are OpenObserve's own, so we control what they show but not their layout.
- OpenObserve is AGPL and runs only as a separate local service, so garden's MIT code does not link to it.
- The spike found settings that matter: `ZO_DATA_DIR` must point at the volume, a strong root password is required, and local webhooks need `ZO_SSRF_ALLOWED_HOSTS`.
