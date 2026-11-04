"""Content-addressed file storage with size and type limits. The claimed type is never trusted."""

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.core.audit import AuditService, get_audit_service
from app.core.files.models import StoredFile
from app.core.files.repository import FileRepository, get_file_repository
from app.shared.clock import Clock, get_clock
from app.shared.errors import Forbidden, NotFound, ProblemError, Unprocessable

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_NAME_LENGTH = 255


class PayloadTooLarge(ProblemError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title = "Payload Too Large"


def sniff_content_type(data: bytes) -> str | None:
    """Work out the type from the bytes themselves. Only the types Garden accepts are recognised."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    return "text/plain"


@dataclass(frozen=True)
class UploadedFile:
    sha256: str
    size_bytes: int
    content_type: str


class FileService:
    def __init__(
        self,
        repo: FileRepository,
        audit: AuditService,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._clock = clock
        self._root = settings.files_root
        self._max_bytes = settings.files_max_bytes

    def _path(self, sha256: str) -> Path:
        return self._root / sha256[:2] / sha256

    async def upload(self, *, data: bytes, name: str, actor_id: uuid.UUID) -> UploadedFile:
        if len(data) > self._max_bytes:
            raise PayloadTooLarge(f"Files may be at most {self._max_bytes} bytes.")
        content_type = sniff_content_type(data)
        if content_type is None:
            raise Unprocessable("This file type is not allowed. Use PDF, PNG, JPEG or plain text.")
        sha256 = hashlib.sha256(data).hexdigest()
        if await self._repo.get(sha256) is None:
            self._write_blob(sha256, data)
            self._repo.add(
                StoredFile(
                    sha256=sha256,
                    size_bytes=len(data),
                    content_type=content_type,
                    first_name=name[:_MAX_NAME_LENGTH],
                    first_uploaded_by=actor_id,
                    created_at=self._clock.now(),
                )
            )
        self._audit.record(
            actor_id=actor_id,
            operation="files.file.upload",
            target=f"file:{sha256}",
            summary=f"Uploaded {name[:_MAX_NAME_LENGTH]} ({len(data)} bytes, {content_type}).",
        )
        await self._repo.commit()
        return UploadedFile(sha256=sha256, size_bytes=len(data), content_type=content_type)

    def _write_blob(self, sha256: str, data: bytes) -> None:
        path = self._path(sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target, then rename, so a reader never sees a half-written file.
        temporary = path.with_suffix(".partial")
        _ = temporary.write_bytes(data)
        _ = os.replace(temporary, path)

    async def exists(self, sha256: str) -> bool:
        """Whether a file with this SHA-256 is stored. Other modules check receipts with this."""
        if _SHA256.fullmatch(sha256) is None:
            return False
        return await self._repo.get(sha256) is not None

    async def read(self, sha256: str) -> tuple[StoredFile, bytes]:
        if _SHA256.fullmatch(sha256) is None:
            raise NotFound("No file has that identifier.")
        stored = await self._repo.get(sha256)
        path = self._path(sha256)
        if stored is None or not path.is_file():
            raise NotFound("No file has that identifier.")
        return stored, path.read_bytes()

    async def delete(self, *, sha256: str, actor_id: uuid.UUID) -> None:
        """Only the first uploader may delete a file. The blob goes once its row is gone."""
        if _SHA256.fullmatch(sha256) is None:
            raise NotFound("No file has that identifier.")
        stored = await self._repo.get(sha256)
        if stored is None:
            raise NotFound("No file has that identifier.")
        if stored.first_uploaded_by != actor_id:
            raise Forbidden("Only the person who first uploaded this file may delete it.")
        await self._repo.remove(stored)
        self._audit.record(
            actor_id=actor_id,
            operation="files.file.delete",
            target=f"file:{sha256}",
            summary=f"Deleted {stored.first_name} ({stored.size_bytes} bytes).",
        )
        await self._repo.commit()
        self._path(sha256).unlink(missing_ok=True)


def get_file_service(
    repo: Annotated[FileRepository, Depends(get_file_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileService:
    return FileService(repo, audit, clock, settings)
