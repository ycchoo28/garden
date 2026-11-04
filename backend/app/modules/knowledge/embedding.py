"""The embedding port: text in, vectors out. The real adapter calls bge-m3 on llama.cpp.

Tests replace the port with a deterministic fake, so the checks never need the model.
"""

import math
from collections.abc import Sequence
from http import HTTPStatus
from typing import Annotated, Protocol

import httpx
from fastapi import Depends

from app.config import Settings, get_settings
from app.shared.errors import ProblemError

# bge-m3 produces 1024-dimensional vectors, and the chunks column is declared with the same size.
EMBEDDING_DIMENSIONS = 1024


class EmbeddingUnavailable(ProblemError):
    """The embedding service did not answer. Search and preparation both need it."""

    status = HTTPStatus.SERVICE_UNAVAILABLE
    title = HTTPStatus.SERVICE_UNAVAILABLE.phrase


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LlamaCppEmbedder:
    """Calls llama.cpp's OpenAI-compatible `/v1/embeddings` endpoint and normalises the vectors."""

    def __init__(self, base_url: str, timeout_seconds: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/v1/embeddings",
                    json={"input": list(texts)},
                )
                _ = response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise EmbeddingUnavailable("The embedding service did not answer.") from error
        vectors: list[list[float]] = []
        for item in sorted(body["data"], key=lambda row: row["index"]):
            vector = [float(value) for value in item["embedding"]]
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise EmbeddingUnavailable(
                    f"The embedding service returned {len(vector)} dimensions, not 1024."
                )
            vectors.append(_normalise(vector))
        if len(vectors) != len(texts):
            raise EmbeddingUnavailable(
                "The embedding service returned the wrong number of vectors."
            )
        return vectors


def _normalise(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        return vector
    return [value / length for value in vector]


def get_embedder(settings: Annotated[Settings, Depends(get_settings)]) -> Embedder:
    return LlamaCppEmbedder(settings.embedding_url)
