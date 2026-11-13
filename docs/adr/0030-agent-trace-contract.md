# 0030. Agent trace contract

Status: Accepted, 2026-11-13. It amends [ADR 0024](0024-model-gateway.md).

## Context

Evals need to show what an agent did in a trial, and later the same view should monitor agents in daily use.
Some agents are harnesses we do not own, such as dsh, and some will be agents the repository deploys itself.
dsh ships an OpenTelemetry transport but no model or tool spans, and its session telemetry sends feedback to the vendor's collector unless it is turned off.
The model gateway already sees every model call an agent makes through an Agent login, and the API already sees every operation.
[ADR 0024](0024-model-gateway.md) says the gateway keeps counts only, never the prompt or the reply.

## Decision

Every producer of agent telemetry follows one trace contract: the OpenTelemetry GenAI conventions plus a few garden attributes.

- **Conventions.** Spans use `gen_ai.operation.name` (`chat`, `invoke_agent`, `execute_tool`), `gen_ai.request.model` and `gen_ai.usage.*`.
- **Garden attributes.** Every span carries the person, the Agent login, the agent conversation when it is known, the eval run and trial when there is one, and whether content was captured.
  Dashboards, alerts and graders read only these fields, never a producer's own format.
- **The model gateway emits a `chat` span for every call.**
  By default the span holds metadata only: the model, the status, the token numbers, the timing and the tool names the model asked for.
  The prompt and the reply are recorded only when content capture is turned on by a development or eval setting, which is off by default and refused outside development.
  The usage rows of ADR 0024 stay as they are, so limits and counts do not depend on telemetry.
- **The API and the MCP server trace every operation** through OpenTelemetry instrumentation, for every agent, including ones that never call the gateway and hosted agents calling the HTTP MCP endpoint from [ADR 0028](0028-hosted-mcp-endpoint.md).
  They accept a W3C `traceparent` and carry it on, so an outside trace links to garden's spans.
- **Agents the repository deploys send their model calls through the gateway**, where the upstream key and the limits live, and instrument themselves with OpenTelemetry, passing `traceparent` on their calls.
  The gateway and API spans then sit inside the agent's own trace.
- **Harnesses we do not own** are traced at the gateway and the API.
  For dsh, the gateway's spans are grouped into turns, and the agent conversation is inferred from the Agent login and the gaps between calls.
  A dsh plugin that traces from inside is added only when these traces miss something we need.
- **dsh vendor telemetry is off.** Garden's dsh patch sets the session telemetry mode to `DISABLED`.

## Consequences

- One set of dashboards serves evals and monitoring, whichever agent produced the spans.
- The gateway can count tokens and show turns for any harness without harness-specific code.
- Tool durations for dsh are not real, because the gateway sees only the gaps between model calls.
- An agent that does not use the gateway shows its garden operations but not its model calls, unless its platform exports its own traces.
- Content capture is a deliberate switch, so a production trace never holds a prompt by accident.
