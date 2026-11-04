# Knowledge (module)

## What it owns

- Collections of documents, and the rules that say which roles and departments may read each one.
- Import batches: a set of uploaded files that is parsed, chunked and embedded in the background, then reviewed with a preview search.
- Chunks and their embeddings, in the `knowledge` schema.
- It does not own the files themselves (the files core stores them) or the people (the users core).

## Layout

- `lifecycle.py` is the batch state machine. It is pure and tested directly.
- `parsing.py`, `chunking.py`, `text.py` and `rrf.py` are pure helpers for ingest and search.
- `ingest.py` prepares one batch. `jobs.py` runs it in the background through Procrastinate.
- `embedding.py` holds the `Embedder` port and the llama.cpp adapter. Tests use `tests/fakes.py`.
- `search.py` shapes candidates into preview results.

## Interface

Import from `app.modules.knowledge` only: `router`, `ROLES` and `PERMISSIONS`. Export a service or a type here only when another module needs it.

## Key decisions

- Access rules are enforced in the SQL that reads chunks, so a rule that matches nothing fails closed. See [0010](../../../../docs/adr/0010-deny-by-default.md).
- Vectors live in pgvector and embeddings come from a local model. See [0016](../../../../docs/adr/0016-pgvector-and-local-embeddings.md).
- Preview search fuses vector, keyword and identifier rankings with reciprocal rank fusion. See [0017](../../../../docs/adr/0017-hybrid-search-with-rrf.md).
