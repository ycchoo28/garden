"""The preparation pipeline: read each file, parse it, chunk it, embed it, and stage the chunks.

It runs in the background worker. A batch that cannot be fully prepared ends in `failed` and stages
nothing searchable, so a half-prepared batch never reaches preview or apply.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.audit import AuditService
from app.core.files import FileService
from app.modules.knowledge.batches.lifecycle import BatchEvent, BatchState, transition
from app.modules.knowledge.batches.repository import BatchRepository
from app.modules.knowledge.models import BatchFile, Chunk, ImportBatch
from app.modules.knowledge.reading.chunking import ChunkDraft, chunk_tree
from app.modules.knowledge.reading.embedding import Embedder, EmbeddingUnavailable
from app.modules.knowledge.reading.parsing import ParseError, parse_document
from app.modules.knowledge.reading.text import count_tokens, detect_language, extract_identifiers
from app.shared.clock import Clock
from app.shared.errors import NotFound
from app.shared.ids import new_id

# Texts are sent to the embedding service in small requests, so a search is never queued behind a
# large handover.
EMBED_BATCH_SIZE = 16


@dataclass(frozen=True)
class _StagedFile:
    row: BatchFile
    drafts: list[ChunkDraft]


async def prepare_batch(
    batch_id: uuid.UUID,
    *,
    repo: BatchRepository,
    files: FileService,
    audit: AuditService,
    embedder: Embedder,
    clock: Clock,
) -> None:
    batch = await repo.get_batch(batch_id)
    if batch is None or batch.state != BatchState.PREPARING:
        return
    stored = await repo.batch_files(batch_id)
    staged: list[_StagedFile] = []
    failures: list[str] = []
    for row in stored:
        try:
            _, data = await files.read(row.sha256)
            tree = parse_document(data, row.name, row.content_type)
        except (ParseError, NotFound) as error:
            row.parse_status = "failed"
            row.parse_error = str(error)
            failures.append(f"{row.name}: {error}")
            continue
        row.parse_status = tree.status
        row.title = tree.title
        row.language = detect_language(tree.text)
        staged.append(_StagedFile(row, chunk_tree(tree, count_tokens)))

    if failures:
        await _finish_failed(
            batch,
            repo,
            audit,
            reason=f"{len(failures)} file(s) could not be parsed.",
            report={"failed_files": failures},
        )
        return

    try:
        chunks = await _build_chunks(batch, staged, embedder)
    except EmbeddingUnavailable as error:
        await _finish_failed(batch, repo, audit, reason=error.detail or error.title, report={})
        return

    repo.add_all(chunks)
    batch.state = transition(BatchState.PREPARING, BatchEvent.PREPARED).value
    batch.prepared_at = clock.now()
    batch.report = {
        "files": len(staged),
        "chunks": len(chunks),
        "embedded": sum(1 for chunk in chunks if chunk.embedding is not None),
    }
    audit.record(
        actor_id=batch.created_by,
        operation="knowledge.batch.prepared",
        target=f"batch:{batch.id}",
        summary=f"Prepared {len(staged)} file(s) into {len(chunks)} chunk(s).",
    )
    await repo.commit()


async def _finish_failed(
    batch: ImportBatch,
    repo: BatchRepository,
    audit: AuditService,
    *,
    reason: str,
    report: dict[str, object],
) -> None:
    batch.state = transition(BatchState.PREPARING, BatchEvent.PREPARE_FAILED).value
    batch.failure_reason = reason
    batch.report = report
    audit.record(
        actor_id=batch.created_by,
        operation="knowledge.batch.prepare_failed",
        target=f"batch:{batch.id}",
        summary=reason,
    )
    await repo.commit()


async def _build_chunks(
    batch: ImportBatch,
    staged: Sequence[_StagedFile],
    embedder: Embedder,
) -> list[Chunk]:
    """Turn every staged file's drafts into chunk rows, embedding the ones that are searchable."""
    rows: list[Chunk] = []
    pending: list[tuple[Chunk, str]] = []
    for item in staged:
        title = item.row.title or item.row.name
        draft_ids: dict[int, uuid.UUID] = {}
        for index, draft in enumerate(item.drafts):
            chunk_id = new_id()
            draft_ids[index] = chunk_id
            prefix = f"{title} > {draft.heading_path}" if draft.heading_path else title
            chunk = Chunk(
                id=chunk_id,
                batch_id=batch.id,
                batch_file_id=item.row.id,
                collection_id=batch.collection_id,
                parent_id=None if draft.parent is None else draft_ids[draft.parent],
                kind=draft.kind,
                position=index,
                translation_group=item.row.translation_group,
                title=title,
                language=detect_language(draft.text),
                heading_path=draft.heading_path,
                locator=draft.locator,
                body=draft.text,
                context_prefix=prefix,
                identifiers=extract_identifiers(draft.text),
                token_count=draft.token_count,
                embedding=None,
            )
            rows.append(chunk)
            if draft.kind != "parent":
                pending.append((chunk, f"{prefix}\n{draft.text}"))

    for start in range(0, len(pending), EMBED_BATCH_SIZE):
        window = pending[start : start + EMBED_BATCH_SIZE]
        vectors = await embedder.embed([text for _, text in window])
        for (chunk, _), vector in zip(window, vectors, strict=True):
            chunk.embedding = vector
    return rows
