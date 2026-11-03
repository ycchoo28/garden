"""Errors that become RFC 9457 application/problem+json responses."""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"


class FieldError(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    """The body of every error response. It matches what `_problem` emits."""

    type: str
    title: str
    status: int
    instance: str
    detail: str | None = None
    errors: list[FieldError] | None = None


# Declared on the app, so every route inherits them. FastAPI would otherwise advertise its own
# HTTPValidationError for 422. The media type is rewritten in `_advertise_problem_json`.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": Problem, "description": "Validation failed"},
    "default": {"model": Problem, "description": "An error, as a problem document"},
}


class ProblemError(Exception):
    """Raise to answer the request with a problem document of this status.

    `extensions` are extra members of the problem document (RFC 9457 section 3.2), such as the id
    and link an agent needs to act on the problem. `headers` are extra response headers, such as
    `Retry-After` on a 429.
    """

    status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    title: str = HTTPStatus.INTERNAL_SERVER_ERROR.phrase

    def __init__(
        self,
        detail: str | None = None,
        errors: list[FieldError] | None = None,
        extensions: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors = errors
        self.extensions = extensions or {}
        self.headers = headers or {}


class Unauthorized(ProblemError):
    status = HTTPStatus.UNAUTHORIZED
    title = HTTPStatus.UNAUTHORIZED.phrase


class Forbidden(ProblemError):
    status = HTTPStatus.FORBIDDEN
    title = HTTPStatus.FORBIDDEN.phrase


class NotFound(ProblemError):
    status = HTTPStatus.NOT_FOUND
    title = HTTPStatus.NOT_FOUND.phrase


class Conflict(ProblemError):
    status = HTTPStatus.CONFLICT
    title = HTTPStatus.CONFLICT.phrase


class TooManyRequests(ProblemError):
    """The caller has made too many requests in a short time, so it must wait and try again."""

    status = HTTPStatus.TOO_MANY_REQUESTS
    title = HTTPStatus.TOO_MANY_REQUESTS.phrase


class Unprocessable(ProblemError):
    """The request is well formed, but its content breaks a rule, such as an unknown role."""

    status = HTTPStatus.UNPROCESSABLE_ENTITY
    title = "Validation failed"


def _problem(
    request: Request,
    status: int,
    title: str,
    detail: str | None,
    errors: list[dict[str, str]] | None = None,
    extensions: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "instance": request.url.path,
    }
    if detail is not None:
        body["detail"] = detail
    if errors is not None:
        body["errors"] = errors
    body.update(extensions or {})
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


def _advertise_problem_json(app: FastAPI) -> None:
    """FastAPI describes the `model` responses as application/json. The errors are problem+json."""
    build_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        schema: dict[str, Any] = build_openapi()
        operations: list[dict[str, Any]] = [
            operation
            for path_item in schema["paths"].values()
            for operation in path_item.values()
            if isinstance(operation, dict) and "responses" in operation
        ]
        for operation in operations:
            responses: dict[str, Any] = operation["responses"]
            for status in ("422", "default"):
                response: dict[str, Any] | None = responses.get(status)
                if response and "application/json" in response.get("content", {}):
                    response["content"] = {
                        PROBLEM_MEDIA_TYPE: response["content"].pop("application/json")
                    }
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def install_problem_handlers(app: FastAPI) -> None:
    _advertise_problem_json(app)

    @app.exception_handler(ProblemError)
    async def handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        errors = [error.model_dump() for error in exc.errors] if exc.errors else None
        return _problem(
            request, exc.status, exc.title, exc.detail, errors, exc.extensions, exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return _problem(
            request, 422, "Validation failed", "One or more fields are invalid.", errors
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        title = HTTPStatus(exc.status_code).phrase
        return _problem(request, exc.status_code, title, str(exc.detail))
