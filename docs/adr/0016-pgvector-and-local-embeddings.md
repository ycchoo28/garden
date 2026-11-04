# 0016. pgvector and local embeddings

Status: Accepted, 2026-11-03.

## Context

The knowledge module must find passages by meaning, and the data must stay on the developer's machine, as recorded in 0014.
A separate vector database would be a second system to back up, secure and keep in step with Postgres.
Hosted embedding APIs would send company documents off the machine.

## Decision

Vectors are stored in Postgres with the pgvector extension, in a `vector(1024)` column on the chunk table.
The index is HNSW with cosine distance (`vector_cosine_ops`).
Embeddings come from bge-m3 served by llama.cpp in Compose, through the `Embedder` port in the knowledge module.
The model file is pinned by revision and SHA-256 in `infra/embedding/model.env`, and `just models` refuses a file that does not match.
Tests use a deterministic fake embedder, so they never need the model.

## Consequences

- Chunks, their vectors and their keyword index are in one transaction, and one backup covers all of them.
- The Postgres image must carry pgvector, which `infra/postgres` builds.
- The embedding service is optional for `just up`. Preparing a batch fails with a clear error when it is not running.
- Changing the model changes the vector size or the scores, and that is a decision that replaces this record.
