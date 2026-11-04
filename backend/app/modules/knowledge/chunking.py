"""Chunk a document tree by its structure.

Pure: the token counter is passed in, so tests need no model.

A section of 800 tokens or less is one chunk. A larger section becomes parents of up to 2,000
tokens, and each parent is split into children of about 400 tokens that are embedded. Small
neighbouring sections merge while they stay within 800 tokens. Parents are stored but not embedded.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.modules.knowledge.parsing import DocumentTree, Section
from app.modules.knowledge.text import count_tokens

MAX_CHUNK_TOKENS = 800
SMALL_PIECE_TOKENS = 100
CHILD_TOKENS = 400
PARENT_TOKENS = 2000

_SENTENCE_END = re.compile(r"(?<=[.!?。\uff01\uff1f])\s+|(?<=[。\uff01\uff1f])")


@dataclass(frozen=True)
class ChunkDraft:
    kind: Literal["single", "parent", "child"]
    # Index of the parent draft in the returned list, for children only.
    parent: int | None
    heading_path: str
    locator: str
    text: str
    token_count: int


@dataclass(frozen=True)
class _Piece:
    heading_path: str
    locator: str
    text: str
    tokens: int


def chunk_tree(
    tree: DocumentTree,
    count: Callable[[str], int] = count_tokens,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    buffer: _Piece | None = None

    def emit_buffer() -> None:
        nonlocal buffer
        if buffer is not None:
            drafts.append(_single(buffer))
            buffer = None

    for section in tree.sections:
        tokens = count(section.text)
        if tokens > MAX_CHUNK_TOKENS:
            emit_buffer()
            _split_large(section, count, drafts)
            continue
        piece = _Piece(section.heading_path, section.locator, section.text, tokens)
        if buffer is None:
            buffer = piece
        elif (
            buffer.tokens < SMALL_PIECE_TOKENS or tokens < SMALL_PIECE_TOKENS
        ) and buffer.tokens + tokens <= MAX_CHUNK_TOKENS:
            buffer = _merge(buffer, piece)
        else:
            emit_buffer()
            buffer = piece
    emit_buffer()
    return drafts


def _single(piece: _Piece) -> ChunkDraft:
    return ChunkDraft("single", None, piece.heading_path, piece.locator, piece.text, piece.tokens)


def _merge(first: _Piece, second: _Piece) -> _Piece:
    return _Piece(
        heading_path=first.heading_path,
        locator=first.locator,
        text=f"{first.text}\n\n{second.text}",
        tokens=first.tokens + second.tokens,
    )


def _split_large(section: Section, count: Callable[[str], int], drafts: list[ChunkDraft]) -> None:
    for parent_text in _pack(list(section.paragraphs), PARENT_TOKENS, count):
        parent_index = len(drafts)
        drafts.append(
            ChunkDraft(
                "parent",
                None,
                section.heading_path,
                section.locator,
                parent_text,
                count(parent_text),
            )
        )
        for child_text in _pack(_paragraph_units(parent_text), CHILD_TOKENS, count):
            drafts.append(
                ChunkDraft(
                    "child",
                    parent_index,
                    section.heading_path,
                    section.locator,
                    child_text,
                    count(child_text),
                )
            )


def _paragraph_units(text: str) -> list[str]:
    return [paragraph for paragraph in text.split("\n\n") if paragraph.strip()]


def _pack(units: list[str], limit: int, count: Callable[[str], int]) -> list[str]:
    """Join units greedily into pieces of at most `limit` tokens.

    Any unit that is too big is split first.
    """
    pieces: list[str] = []
    current: list[str] = []
    for unit in _fit(units, limit, count):
        candidate = "\n\n".join([*current, unit])
        if current and count(candidate) > limit:
            pieces.append("\n\n".join(current))
            current = [unit]
        else:
            current = [*current, unit]
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def _fit(units: list[str], limit: int, count: Callable[[str], int]) -> list[str]:
    """Break every unit that is over the limit: first at sentence ends, then by halving."""
    fitted: list[str] = []
    for unit in units:
        if count(unit) <= limit:
            fitted.append(unit)
            continue
        sentences = [part for part in _SENTENCE_END.split(unit) if part and part.strip()]
        if len(sentences) > 1:
            fitted.extend(_fit(sentences, limit, count))
        else:
            middle = len(unit) // 2
            fitted.extend(_fit([unit[:middle], unit[middle:]], limit, count))
    return fitted
