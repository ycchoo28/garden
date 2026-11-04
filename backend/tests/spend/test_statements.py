"""Card statements and closed months, through the HTTP seam: who may act, dry runs, all-or-nothing
imports, voids, month close and the confirmation gate for agents."""

from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.core.confirmations import CONFIRMATION_HEADER
from app.shared.money import format_money, parse_money
from tests.conftest import bearer, cli_sign_in, sign_in
from tests.fakes import FakeIdentity

HEADER = "transaction_ref,posted_on,department_code,category,merchant,amount_usd"
GOOD_ROWS = [
    "tx-1001,2026-09-14,design,software,Figma,120.00",
    "tx-1002,2026-09-15,engineering,travel,Airline,845.5",
]


def statement_csv(*rows: str) -> bytes:
    return "\n".join([HEADER, *rows]).encode()


async def _preview(client: AsyncClient, data: bytes) -> dict[str, object]:
    response = await client.post(
        "/api/spend/statements/preview", files={"file": ("sept.csv", data, "text/csv")}
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def _import(client: AsyncClient, data: bytes, name: str = "sept.csv") -> dict[str, object]:
    response = await client.post("/api/spend/statements", files={"file": (name, data, "text/csv")})
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


@pytest.mark.asyncio
async def test_only_finance_and_admin_may_preview_or_import(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    files = {"file": ("a.csv", statement_csv(*GOOD_ROWS), "text/csv")}
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    preview = await client.post("/api/spend/statements/preview", files=files)
    assert preview.status_code == 403
    await sign_in(client, identity, "priya.nair@fernhill.example")
    imported = await client.post("/api/spend/statements", files=files)
    assert imported.status_code == 403
    assert (await client.get("/api/spend/statements")).status_code == 403
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    assert (await client.get("/api/spend/statements")).status_code == 200


@pytest.mark.asyncio
async def test_dry_run_reports_the_totals_and_writes_nothing(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    preview = await _preview(client, statement_csv(*GOOD_ROWS))
    assert preview["rows_read"] == 2
    assert preview["total_amount"] == "965.50"
    assert preview["can_apply"] is True
    assert preview["errors"] == [] and preview["duplicates"] == []
    assert (await client.get("/api/spend/statements")).json() == []


@pytest.mark.asyncio
async def test_one_bad_row_blocks_the_whole_import(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    bad = statement_csv(GOOD_ROWS[0], "tx-1003,2026-09-16,design,software,Notion,12.345")
    response = await client.post("/api/spend/statements", files={"file": ("a.csv", bad)})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    fields = [error["field"] for error in response.json()["errors"]]
    assert fields == ["rows[2].amount_usd"]
    assert (await client.get("/api/spend/statements")).json() == []


@pytest.mark.asyncio
async def test_duplicate_inside_one_file_is_refused(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    preview = await _preview(client, statement_csv(GOOD_ROWS[0], GOOD_ROWS[0]))
    assert preview["can_apply"] is False
    assert [issue["row"] for issue in preview["duplicates"]] == [2]


@pytest.mark.asyncio
async def test_reference_already_saved_is_refused_and_names_the_statement(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    await _import(client, statement_csv(*GOOD_ROWS), name="first.csv")
    again = await client.post(
        "/api/spend/statements", files={"file": ("second.csv", statement_csv(GOOD_ROWS[0]))}
    )
    assert again.status_code == 422
    messages = [error["message"] for error in again.json()["errors"]]
    assert messages == ["Already imported in statement first.csv."]


@pytest.mark.asyncio
async def test_import_saves_rows_and_totals_to_two_decimals(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    statement = await _import(
        client, statement_csv("tx-2001,2026-09-02,design,office,Stationery,5")
    )
    assert statement["total_amount"] == "5.00"
    assert statement["row_count"] == 1
    assert statement["voided"] is False


@pytest.mark.asyncio
async def test_void_records_who_when_and_why_and_keeps_the_rows(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    statement = await _import(client, statement_csv(*GOOD_ROWS))
    voided = await client.post(
        f"/api/spend/statements/{statement['id']}/void", json={"reason": "Duplicate upload"}
    )
    assert voided.status_code == 200, voided.text
    body = voided.json()
    assert body["voided"] is True
    assert body["voided_at"] is not None
    assert body["voided_by"] == statement["imported_by"]
    assert body["void_reason"] == "Duplicate upload"
    assert body["row_count"] == 2
    again = await client.post(
        f"/api/spend/statements/{statement['id']}/void", json={"reason": "Again"}
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_voided_statement_references_can_be_imported_again(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    statement = await _import(client, statement_csv(*GOOD_ROWS))
    await client.post(
        f"/api/spend/statements/{statement['id']}/void", json={"reason": "Wrong file"}
    )
    preview = await _preview(client, statement_csv(*GOOD_ROWS))
    assert preview["can_apply"] is True


@pytest.mark.asyncio
async def test_month_close_blocks_imports_and_voids_for_that_month(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    statement = await _import(client, statement_csv(*GOOD_ROWS))
    closed = await client.post(
        "/api/spend/months/2026-09/close", json={"reason": "September books sent to auditors"}
    )
    assert closed.status_code == 201, closed.text
    assert closed.json()["closed_by"] == statement["imported_by"]
    assert closed.json()["reason"] == "September books sent to auditors"

    late = await client.post(
        "/api/spend/statements",
        files={"file": ("late.csv", statement_csv("tx-9,2026-09-30,design,meals,Cafe,9.00"))},
    )
    assert late.status_code == 422
    assert "closed" in late.json()["errors"][0]["message"]

    void = await client.post(
        f"/api/spend/statements/{statement['id']}/void", json={"reason": "Too late"}
    )
    assert void.status_code == 409
    assert void.headers["content-type"].startswith("application/problem+json")

    again = await client.post("/api/spend/months/2026-09/close", json={"reason": "Twice"})
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_agent_void_waits_for_the_person_to_approve(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    statement = await _import(client, statement_csv(*GOOD_ROWS))
    agent = bearer(await cli_sign_in(client, identity, "lena.fischer@fernhill.example"))
    url = f"/api/spend/statements/{statement['id']}/void"
    challenge = await client.post(url, json={"reason": "Agent request"}, headers=agent)
    assert challenge.status_code == 428, challenge.text
    confirmation_id = challenge.json()["confirmation_id"]

    approved = await client.post(
        f"/api/confirmations/{confirmation_id}/decision", json={"decision": "approve"}
    )
    assert approved.json()["status"] == "approved"

    voided = await client.post(
        url,
        json={"reason": "Agent request"},
        headers={**agent, CONFIRMATION_HEADER: confirmation_id},
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["voided"] is True


@pytest.mark.asyncio
async def test_agent_month_close_waits_for_the_person_to_approve(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    agent = bearer(await cli_sign_in(client, identity, "lena.fischer@fernhill.example"))
    url = "/api/spend/months/2026-08/close"
    challenge = await client.post(url, json={"reason": "Agent request"}, headers=agent)
    assert challenge.status_code == 428
    confirmation_id = challenge.json()["confirmation_id"]

    await client.post(
        f"/api/confirmations/{confirmation_id}/decision", json={"decision": "approve"}
    )
    closed = await client.post(
        url,
        json={"reason": "Agent request"},
        headers={**agent, CONFIRMATION_HEADER: confirmation_id},
    )
    assert closed.status_code == 201, closed.text


def test_money_reads_and_writes_two_decimals() -> None:
    assert format_money(Decimal("5")) == "5.00"
    assert format_money(parse_money("1200.5")) == "1200.50"
    assert format_money(parse_money("-35")) == "-35.00"
    with pytest.raises(ValueError):
        _ = parse_money("1,200.00")
    with pytest.raises(ValueError):
        _ = parse_money("1.005")
