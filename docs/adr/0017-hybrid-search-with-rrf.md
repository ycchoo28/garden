# 0017. Hybrid search with reciprocal rank fusion

Status: Accepted, 2026-11-03.

## Context

Vector search finds passages with the same meaning, but it misses exact words, codes and clause numbers such as `POL-01` or `A.5.18`.
Keyword search finds those words, but it misses paraphrases and mixed Chinese and English text.
The two rankings score on different scales, so their raw scores cannot be added.

## Decision

A preview search takes three ranked lists: nearest vectors, keyword matches (`websearch_to_tsquery` over a generated `tsvector`), and identifier matches from the query.
The lists are merged with reciprocal rank fusion, score = sum of 1 / (60 + rank).
After fusion, results are one per parent unit, one per translation group in the query's language, cut to the limit, and cut again to the token budget.
When the budget cuts results, the response says `truncated`.
Access rules are applied inside each SQL query, so a chunk the caller may not read never reaches the fusion step.

## Consequences

- Scores are only comparable within one query, which is all the preview needs.
- Rank fusion needs no tuning of weights between the lists.
- The constant 60 is the value from the original RRF paper. Changing it is a decision that replaces this record.
- Reranking with a model is not part of this decision and would be a new record.
