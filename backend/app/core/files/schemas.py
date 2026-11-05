"""Request and response bodies of the files routes."""

from pydantic import BaseModel


class UploadedFileOut(BaseModel):
    sha256: str
    size_bytes: int
    content_type: str
    url: str
