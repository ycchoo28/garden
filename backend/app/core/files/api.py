"""HTTP routes of the files module."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, UploadFile
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.core.confirmations import confirm_agent_action
from app.core.files.service import FileService, get_file_service
from app.core.users import CurrentPerson, require
from app.shared.operations import op

router = APIRouter()


class UploadedFileOut(BaseModel):
    sha256: str
    size_bytes: int
    content_type: str
    url: str


@router.post(
    "/api/files",
    response_model=UploadedFileOut,
    **op("files.file.upload", "Upload one file. Accepts PDF, PNG, JPEG or plain text."),
)
async def upload_file(
    file: UploadFile,
    person: Annotated[CurrentPerson, require("files.upload")],
    service: Annotated[FileService, Depends(get_file_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UploadedFileOut:
    # Read one byte past the limit, so an oversized upload is told apart from a limit-sized one.
    data = await file.read(settings.files_max_bytes + 1)
    name = file.filename or "upload"
    stored = await service.upload(data=data, name=name, actor_id=person.id)
    return UploadedFileOut(
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        content_type=stored.content_type,
        url=f"/api/files/{stored.sha256}",
    )


@router.get(
    "/api/files/{sha256}",
    **op("files.file.read", "Download a file by its SHA-256."),
)
async def read_file(
    sha256: str,
    _person: Annotated[CurrentPerson, require("files.view")],
    service: Annotated[FileService, Depends(get_file_service)],
) -> Response:
    stored, data = await service.read(sha256)
    return Response(
        content=data,
        media_type=stored.content_type,
        headers={"Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/api/files/{sha256}",
    status_code=204,
    dependencies=[confirm_agent_action("files.file.delete")],
    **op(
        "files.file.delete",
        "Delete a file you first uploaded. The person must approve it in the browser first.",
        confirm=True,
    ),
)
async def delete_file(
    sha256: str,
    person: Annotated[CurrentPerson, require("files.delete")],
    service: Annotated[FileService, Depends(get_file_service)],
) -> Response:
    await service.delete(sha256=sha256, actor_id=person.id)
    return Response(status_code=204)
