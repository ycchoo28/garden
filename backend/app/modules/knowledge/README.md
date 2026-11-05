# Knowledge (module)

## What it owns

- Collections of documents, and the rules that say which roles and departments may read each one.
- Import batches: a set of uploaded files that is parsed, chunked and embedded in the background, then reviewed with a preview search.
- Chunks and their embeddings, in the `knowledge` schema.
- It does not own the files themselves (the files core stores them) or the people (the users core).

## Features

- `collections/` lists and creates collections, holds their access rules, and reports the module health. Tables: `collection` and `collection_rule`.
- `batches/` opens a batch, takes its files, prepares it in the background, asks for approval, approves, rejects, cancels and undoes it. It also erases one document from every batch of a collection, from search and from file storage. Tables: `import_batch`, `batch_file`, `chunk` and `chunk_range`.
  Its files are `lifecycle.py` (the state machine), `publish.py` (the pure rules for the staged digest, the documents a batch closes and the risk triggers), `ingest.py` (prepares one batch) and `jobs.py` (runs it through Procrastinate).
  Erase lives here because it deletes only batch files.
- `search/` runs hybrid search and preview search, and reads the search log. Tables: `search_log`.
  Its files are `search.py` (shapes candidates into results) and `rrf.py` (reciprocal rank fusion).
- `reading/` holds the pure helpers that turn an uploaded file into searchable text: `parsing.py`, `chunking.py` and `text.py`, plus `embedding.py` for the `Embedder` port and the llama.cpp adapter.
  Tests use `tests/fakes.py` for the embedder.
- At the module root: `models.py`, `schemas.py`, `rules.py`, `permissions.py`, `flows.py` and `api.py`.
  `rules.py` holds the access-rule helpers that need no database, and both collections and batches use them.
  `api.py` mounts the three feature routers with endpoints (`collections`, `batches`, `search`) under `/api/knowledge`.

## Cross-feature reads

- The collection service is the only way another feature reads or changes a collection.
  It exposes `collection`, which returns a frozen `CollectionView` with the rules as plain specs, `names`, and `replace_rules`.
- Batches and search call those methods, never the collections repository.
- Batches sets and restores a collection's access rules through `replace_rules`, so only the collections repository writes `collection_rule`.
- The search repository is the one repository whose SQL reads another feature's tables.
  It reads `chunk` and `chunk_range` (owned by batches) and `collection_rule` (owned by collections), read-only, because the access rule must hold in the same query that ranks the chunks.
  It is the one cross-table reader, and it never writes those tables.
  Its only write is to `search_log`, which it owns.

## Interface

Import from `app.modules.knowledge` only: `router`, `ROLES` and `PERMISSIONS`. Export a service or a type here only when another module needs it.

## Key decisions

- Access rules are enforced in the SQL that reads chunks, so a rule that matches nothing fails closed. See [0010](../../../../docs/adr/0010-deny-by-default.md).
- Vectors live in pgvector and embeddings come from a local model. See [0020](../../../../docs/adr/0020-pgvector-and-local-embeddings.md).
- Preview search fuses vector, keyword and identifier rankings with reciprocal rank fusion. See [0021](../../../../docs/adr/0021-hybrid-search-with-rrf.md).
- A batch goes live only through the approvals core, bound to the staged digest, and undo restores the validity spans. See [0025](../../../../docs/adr/0025-batch-approval-and-undo.md).
- Preview search lives in `search/`, not `batches/`. It needs only one fact from batches, that the batch is prepared, and its ranking SQL already reads `collection_rule`, so putting it in batches would add a second cross-table reader.
