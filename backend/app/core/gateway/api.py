"""HTTP routes of the gateway module: an OpenAI-compatible model proxy and today's usage."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.gateway.service import GatewayService, get_gateway_service
from app.core.users import CurrentPerson, require
from app.shared.operations import op
from app.shared.request_login import current_request_login

router = APIRouter()


class UsageTodayOut(BaseModel):
    day: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens_limit: int


@router.post(
    "/api/gateway/v1/chat/completions",
    **op(
        "gateway.model.complete",
        "Send an OpenAI-compatible chat completion to a model on the gateway allowlist.",
        tool=False,
    ),
)
async def chat_completions(
    body: dict[str, Any],
    person: Annotated[CurrentPerson, require("gateway.model.use")],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> dict[str, Any]:
    login = current_request_login.get()
    return await service.complete(
        person_id=person.id,
        cli_login_id=login.cli_login_id if login else None,
        body=body,
    )


@router.get(
    "/api/gateway/v1/models",
    **op("gateway.model.list", "List the models the gateway forwards.", tool=False),
)
async def list_models(
    _person: Annotated[CurrentPerson, require("gateway.model.use")],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> dict[str, Any]:
    return {"object": "list", "data": service.models()}


@router.get(
    "/api/gateway/usage/today",
    response_model=UsageTodayOut,
    **op(
        "gateway.usage.read",
        "Read today's model requests and tokens for the signed-in person. Counts only.",
    ),
)
async def usage_today(
    person: Annotated[CurrentPerson, require("gateway.usage.view")],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> UsageTodayOut:
    usage = await service.usage_today(person_id=person.id)
    return UsageTodayOut(
        day=usage.day,
        requests=usage.requests,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        tokens_limit=usage.tokens_limit,
    )
