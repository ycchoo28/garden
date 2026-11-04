# 0017. Separation of duties

Status: Accepted, 2026-11-04.

## Context

A person who asks for money should not be the person who approves it, whatever roles they hold.
A manager who is also finance should not approve the same claim twice.

## Decision

The approvals core refuses a decision in four cases, and the rules are pure functions that the tests cover directly.
The person who started a run never decides it.
A person who has already decided an earlier step of the same run never decides a later step.
A decision needs the role the step names.
A department-scoped step needs the approver to be in the subject's department.

The claims module adds one rule on top: certifying, submitting, approving and rejecting are person-only, so an agent login never holds those permissions.

## Consequences

- A claim above the threshold needs a manager and then finance, and the same person cannot give both approvals.
- The refusal messages name the rule that refused, so a person knows why they cannot act.
- Agents can draft and withdraw, and they cannot decide, which keeps the approval of money with a person.
