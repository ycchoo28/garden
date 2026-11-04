# 0012. Confirmation gate

Status: Accepted, 2026-11-03.

## Context

An agent acting for a person should not be able to do something critical on its own.

## Decision

A route marked as critical, when an agent calls it, returns 428 Precondition Required with a confirmation id and a browser URL.
The person approves the confirmation in the browser, and the agent retries with the `X-Garden-Confirmation` header.
A confirmation is bound to the person, the agent login, the operation and a digest of the request, and it expires after ten minutes.
Each confirmation works once.
Only people hold `confirmations.decide`, and agents never do.

## Consequences

- A retry with a changed request is refused, because the digest no longer matches.
- Browser requests pass the gate without a confirmation.
- The approval page at `/confirm/<id>` shows the request, and the person approves or declines it there.
- The decision API stays available for other clients, and it is not open to agents.
