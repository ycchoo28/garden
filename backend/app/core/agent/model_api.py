"""HTTP routes of the model gateway: an OpenAI-compatible model proxy and the caller's usage."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.agent.model_service import ModelService, get_model_service
from app.core.agent.schemas import UsageTodayOut
from app.core.users import CurrentPerson, require
from app.shared.operations import op
from app.shared.request_login import current_request_login

router = APIRouter()


@router.post(
    "/api/agent/model/v1/chat/completions",
    **op(
        "agent.model.chat_completion",
        "Send an OpenAI-compatible chat completion to a model on the gateway allowlist.",
        tool=False,
    ),
)
async def chat_completions(
    body: dict[str, Any],
    person: Annotated[CurrentPerson, require("agent.model.use")],
    service: Annotated[ModelService, Depends(get_model_service)],
) -> dict[str, Any]:
    login = current_request_login.get()
    return await service.complete(
        person_id=person.id,
        cli_login_id=login.cli_login_id if login else None,
        body=body,
    )


@router.get(
    "/api/agent/model/v1/models",
    **op("agent.model.list", "List the models the gateway forwards.", tool=False),
)
async def list_models(
    _person: Annotated[CurrentPerson, require("agent.model.use")],
    service: Annotated[ModelService, Depends(get_model_service)],
) -> dict[str, Any]:
    return {"object": "list", "data": service.models()}


@router.get(
    "/api/agent/model/my-usage",
    response_model=UsageTodayOut,
    **op(
        "agent.my_model_usage.read",
        "Read today's model requests and tokens for the signed-in person. Counts only.",
    ),
)
async def usage_today(
    person: Annotated[CurrentPerson, require("agent.model_usage.view")],
    service: Annotated[ModelService, Depends(get_model_service)],
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
