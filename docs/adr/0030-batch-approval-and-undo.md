# 0030. Batch approval and undo

Status: Accepted, 2026-11-04.

## Context

An import batch changes what readers see.
A large removal or a change to who may read a collection can hide knowledge from people who need it, so the change needs a second pair of eyes before it goes live.
Agents can prepare batches, but a person must decide whether the result goes live.

## Decision

A batch goes live only through approval.
The starter asks for approval, which records the staged result as a digest of the batch mode, its access rules, its files and its chunks, including embeddings.
Approval recomputes the digest and refuses with `staged_result_changed` if it no longer matches, so a person approves exactly what they saw.
The approvals core runs the decision, so the starter never decides their own batch, and a rejection needs a reason.
Only the approval applies the batch, so there is no separate apply step in the state machine.

Each chunk that goes live gets a validity span, which records when it opened and when it closed and which batch did each.
A full-set batch closes the documents it no longer contains.
Undo closes the spans the batch opened, reopens the spans it closed and restores the access rules that were in force before, and it refuses when a later batch has since been applied to the same collection.
Risk triggers flag a large removal and a change to access rules, but they only flag: they do not block the approval.

Search reads the live spans at the chosen moment, and the access filter sits in the SQL, so an empty or missing rule fails closed.
Every search writes a search log row with the person and whether an agent ran it.
Erase is person-only: it removes the document's batch files, its chunks and spans from search, and the stored blob once no other batch uses it.

## Consequences

- A batch that was approved cannot change under the reviewer's feet, and a stale approval is refused rather than silently applied.
- Undo has a limit: the ordering check compares applied times, so two batches applied in the same instant cannot be ordered, and the tests advance the clock to avoid this.
- The starter of a batch needs a second knowledge admin to apply it, which slows small fixes but keeps the live content reviewed.
- Agents can prepare, submit and search, and they cannot approve, reject or erase, which keeps the decision to change knowledge with a person.
