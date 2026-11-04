# Architecture decisions

Each decision is written as a short record: the context, the decision and what follows from it.
Records are numbered in the order they were made, and a later record may replace an earlier one.

| No. | Decision |
| --- | --- |
| [0001](0001-modular-monolith.md) | One deployable app made of modules |
| [0002](0002-layers-and-dependency-injection.md) | Four layers per module, wired by dependency functions |
| [0003](0003-schema-per-module.md) | One Postgres schema and one migration branch per module |
| [0004](0004-facts-over-flags.md) | Record facts with timestamps and actors, and compute statuses |
| [0005](0005-replace-or-void.md) | Business records are replaced or voided, never edited in place |
| [0006](0006-money-and-ids.md) | Money as exact decimals, and UUIDv7 keys |
| [0007](0007-optimistic-locking.md) | A version column rejects stale writes with 409 |
| [0008](0008-audit-in-the-same-transaction.md) | The audit row commits with the change |
| [0009](0009-problem-json-errors.md) | Errors are RFC 9457 problem documents |
| [0010](0010-deny-by-default.md) | Every route needs a permission unless it is listed as public |
| [0011](0011-operation-declarations.md) | Every route declares an operation for the contract |
| [0012](0012-confirmation-gate.md) | An agent's critical action waits for a person's confirmation |
| [0013](0013-scaffold.md) | New modules come from one template that CI proves |
| [0014](0014-local-only-runtime.md) | Everything runs on the developer's machine |
| [0015](0015-single-currency.md) | One currency, USD, with no exchange rates |
| [0016](0016-approval-flows-as-data.md) | Approval flows are rows, and runs copy their steps |
| [0017](0017-separation-of-duties.md) | Nobody decides their own request, and nobody decides two steps of one run |
| [0018](0018-statement-import.md) | Statement import is a dry run, then all or nothing |
| [0019](0019-month-close.md) | Closing a month freezes its spend |
| [0020](0020-pgvector-and-local-embeddings.md) | Vectors in pgvector, and embeddings from a local model |
| [0021](0021-hybrid-search-with-rrf.md) | Hybrid search fused with reciprocal rank fusion |
