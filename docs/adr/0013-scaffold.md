# 0013. Scaffold

Status: Accepted, 2026-11-03.

## Context

Agents adding a module by hand copy the wrong conventions from whichever file they read first.

## Decision

`just new-module <name>` creates a module from the templates in `scripts/templates/module`.
It fills in names, registers the module in `composition.py` at the anchor comments, and regenerates the contract and client.
CI runs the scaffold on a throwaway module and then runs the full check against it.

## Consequences

- A new module starts from the same shape as the others, and the check proves the shape still works.
- The scaffold must change whenever the core API changes, or CI fails.
