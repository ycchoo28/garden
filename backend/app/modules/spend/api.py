"""HTTP endpoints of the spend module: card statement import, voids and month close."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, UploadFile

from app.core.confirmations import confirm_agent_action
from app.core.users import CurrentPerson, require
from app.modules.spend.months import YEAR_MONTH_PATTERN
from app.modules.spend.schemas import (
    CloseIn,
    MonthOut,
    StatementOut,
    StatementPreviewOut,
    VoidIn,
)
from app.modules.spend.service import SpendService, get_spend_service
from app.modules.spend.statement_csv import MAX_BYTES
from app.shared.operations import op

router = APIRouter(prefix="/api/spend", tags=["spend"])


async def _read_upload(file: UploadFile) -> bytes:
    # One byte past the limit, so an oversized file is refused rather than cut short.
    return await file.read(MAX_BYTES + 1)


@router.post(
    "/statements/preview",
    response_model=StatementPreviewOut,
    **op(
        "spend.statement.preview",
        "Check a card statement CSV without saving it. Lists every row error and duplicate.",
        save=False,
    ),
)
async def preview_statement(
    file: UploadFile,
    _person: Annotated[CurrentPerson, require("spend.statement.import")],
    service: Annotated[SpendService, Depends(get_spend_service)],
) -> StatementPreviewOut:
    return StatementPreviewOut.of(await service.preview(await _read_upload(file)))


@router.post(
    "/statements",
    status_code=201,
    response_model=StatementOut,
    **op(
        "spend.statement.import",
        "Import a card statement CSV. Every row is saved, or none is when a row has an error "
        "or a duplicate.",
    ),
)
async def import_statement(
    file: UploadFile,
    person: Annotated[CurrentPerson, require("spend.statement.import")],
    service: Annotated[SpendService, Depends(get_spend_service)],
) -> StatementOut:
    statement = await service.import_statement(
        data=await _read_upload(file),
        source_name=file.filename or "statement.csv",
        actor_id=person.id,
    )
    return StatementOut.of(statement)


@router.get(
    "/statements",
    response_model=list[StatementOut],
    **op(
        "spend.statement.list",
        "List imported card statements, newest first. Voided statements are included.",
    ),
)
async def list_statements(
    _person: Annotated[CurrentPerson, require("spend.statement.view")],
    service: Annotated[SpendService, Depends(get_spend_service)],
) -> list[StatementOut]:
    return [StatementOut.of(statement) for statement in await service.statements()]


@router.post(
    "/statements/{statement_id}/void",
    response_model=StatementOut,
    dependencies=[confirm_agent_action("spend.statement.void")],
    **op(
        "spend.statement.void",
        "Void an imported card statement with a reason. Its rows stop counting as spend but are "
        "kept. An agent's request waits for the person's confirmation.",
        confirm=True,
    ),
)
async def void_statement(
    statement_id: UUID,
    body: VoidIn,
    person: Annotated[CurrentPerson, require("spend.statement.void")],
    service: Annotated[SpendService, Depends(get_spend_service)],
) -> StatementOut:
    statement = await service.void_statement(statement_id, reason=body.reason, actor_id=person.id)
    return StatementOut.of(statement)


@router.get(
    "/months",
    response_model=list[MonthOut],
    **op("spend.month.list", "List closed months, newest first."),
)
async def list_closed_months(
    _person: Annotated[CurrentPerson, require("spend.statement.view")],
    service: Annotated[SpendService, Depends(get_spend_service)],
) -> list[MonthOut]:
    return [MonthOut.of(close) for close in await service.closed_months()]


@router.post(
    "/months/{year_month}/close",
    status_code=201,
    response_model=MonthOut,
    dependencies=[confirm_agent_action("spend.month.close")],
    **op(
        "spend.month.close",
        "Close a month with a reason. Afterwards no spend for that month can be imported or "
        "voided. An agent's request waits for the person's confirmation.",
        confirm=True,
    ),
)
async def close_month(
    year_month: Annotated[str, Path(pattern=YEAR_MONTH_PATTERN)],
    body: CloseIn,
    person: Annotated[CurrentPerson, require("spend.month.close")],
    service: Annotated[SpendService, Depends(get_spend_service)],
) -> MonthOut:
    close = await service.close_month(year_month, reason=body.reason, actor_id=person.id)
    return MonthOut.of(close)
