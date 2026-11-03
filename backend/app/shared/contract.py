"""The contract version and the minimum CLI version, sent on every API response.

The contract version is a fingerprint of the OpenAPI document, so it changes exactly when the
contract does, and a CLI can refresh its tools when it sees a new one.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

CONTRACT_VERSION_HEADER = "X-Garden-Contract-Version"
MIN_CLI_VERSION_HEADER = "X-Garden-Min-CLI-Version"
# The oldest garden CLI the portal still serves. An older CLI stops and says where to download one.
MIN_CLI_VERSION = "0.1.0"

_CONTRACT_VERSION_LENGTH = 12


def contract_version(app: FastAPI) -> str:
    """A short fingerprint of the OpenAPI document."""
    document = json.dumps(app.openapi(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode()).hexdigest()[:_CONTRACT_VERSION_LENGTH]


def install_contract_headers(app: FastAPI) -> None:
    """Add both headers to every response, computing the fingerprint once, on the first one."""
    version: str | None = None

    @app.middleware("http")
    async def contract_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        nonlocal version
        response = await call_next(request)
        if version is None:
            version = contract_version(request.app)
        response.headers[CONTRACT_VERSION_HEADER] = version
        response.headers[MIN_CLI_VERSION_HEADER] = MIN_CLI_VERSION
        return response
