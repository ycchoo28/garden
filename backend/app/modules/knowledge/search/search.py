"""Shape hybrid search candidates into results. Pure: the database and the embedder stay outside.

Candidates arrive best first. A child chunk answers with its parent's text, so a parent
appears once.
A translation group appears once, in the query's language when one of its versions is in
that language.
Results are cut to the limit, then to the token budget, and the truncated flag says when the
budget cut them.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    chunk_id: uuid.UUID
    # The parent for a child chunk, otherwise the chunk itself. Results are one per unit.
    unit_id: uuid.UUID
    batch_file_id: uuid.UUID
    translation_group: str
    title: str
    language: str
    heading_path: str
    locator: str
    text: str
    token_count: int
    score: float


@dataclass(frozen=True)
class Hit:
    chunk_ids: tuple[uuid.UUID, ...]
    batch_file_id: uuid.UUID
    translation_group: str
    title: str
    language: str
    heading_path: str
    locator: str
    text: str
    token_count: int
    score: float


def assemble(
    candidates: Sequence[Candidate],
    *,
    query_language: str,
    limit: int,
    max_tokens: int,
) -> tuple[list[Hit], bool]:
    units = _one_per_unit(candidates)
    chosen = _one_per_translation_group(units, query_language)
    hits: list[Hit] = []
    used = 0
    truncated = False
    for unit in chosen[:limit]:
        if used + unit.token_count > max_tokens:
            truncated = True
            break
        used += unit.token_count
        hits.append(
            Hit(
                chunk_ids=tuple(unit.chunk_ids),
                batch_file_id=unit.batch_file_id,
                translation_group=unit.translation_group,
                title=unit.title,
                language=unit.language,
                heading_path=unit.heading_path,
                locator=unit.locator,
                text=unit.text,
                token_count=unit.token_count,
                score=unit.score,
            )
        )
    return hits, truncated


@dataclass
class _Unit:
    unit_id: uuid.UUID
    chunk_ids: list[uuid.UUID]
    batch_file_id: uuid.UUID
    translation_group: str
    title: str
    language: str
    heading_path: str
    locator: str
    text: str
    token_count: int
    score: float


def _one_per_unit(candidates: Sequence[Candidate]) -> list[_Unit]:
    units: dict[uuid.UUID, _Unit] = {}
    for candidate in candidates:
        existing = units.get(candidate.unit_id)
        if existing is not None:
            existing.chunk_ids.append(candidate.chunk_id)
            continue
        units[candidate.unit_id] = _Unit(
            unit_id=candidate.unit_id,
            chunk_ids=[candidate.chunk_id],
            batch_file_id=candidate.batch_file_id,
            translation_group=candidate.translation_group,
            title=candidate.title,
            language=candidate.language,
            heading_path=candidate.heading_path,
            locator=candidate.locator,
            text=candidate.text,
            token_count=candidate.token_count,
            score=candidate.score,
        )
    return list(units.values())


def _one_per_translation_group(units: list[_Unit], query_language: str) -> list[_Unit]:
    """Keep one unit per translation group, the best in the query's language when there is one."""
    winners: dict[str, _Unit] = {}
    for unit in units:
        current = winners.get(unit.translation_group)
        if current is None or (
            unit.language == query_language and current.language != query_language
        ):
            winners[unit.translation_group] = unit
    kept_ids = {unit.unit_id for unit in winners.values()}
    return [unit for unit in units if unit.unit_id in kept_ids]
