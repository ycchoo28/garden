"""Budget lines and the variance report, through the HTTP seam: department scope, versions and
history, and the way card statements count as actual spend."""

import pytest
from httpx import AsyncClient

from tests.conftest import sign_in
from tests.fakes import FakeIdentity


async def _department_id(client: AsyncClient, code: str) -> str:
    departments = (await client.get("/api/users/departments")).json()
    return next(item["id"] for item in departments if item["code"] == code)


async def _create_line(
    client: AsyncClient, department_id: str, amount: str = "1000"
) -> dict[str, object]:
    response = await client.post(
        "/api/budgets/lines",
        json={
            "department_id": department_id,
            "year_month": "2026-09",
            "category": "software",
            "amount": amount,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


@pytest.mark.asyncio
async def test_employee_may_not_read_or_write_budgets(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    assert (await client.get("/api/budgets/lines", params={"month": "2026-09"})).status_code == 403
    response = await client.post(
        "/api/budgets/lines",
        json={
            "department_id": "00000000-0000-0000-0000-000000000000",
            "year_month": "2026-09",
            "category": "software",
            "amount": "1",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_manager_sees_only_their_own_department(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    await _create_line(client, await _department_id(client, "design"))
    await _create_line(client, await _department_id(client, "engineering"))

    await sign_in(client, identity, "priya.nair@fernhill.example")
    lines = (await client.get("/api/budgets/lines", params={"month": "2026-09"})).json()
    assert {line["department_code"] for line in lines} == {"design"}

    other = await _department_id(client, "engineering")
    refused = await client.get(
        "/api/budgets/lines", params={"month": "2026-09", "department_id": other}
    )
    assert refused.status_code == 403
    assert refused.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_manager_cannot_create_or_replace_a_line(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    line = await _create_line(client, await _department_id(client, "design"))
    await sign_in(client, identity, "priya.nair@fernhill.example")
    replaced = await client.put(
        f"/api/budgets/lines/{line['line_id']}",
        json={"version": 1, "amount": "2000", "reason": "Manager wants more"},
    )
    assert replaced.status_code == 403


@pytest.mark.asyncio
async def test_create_stores_two_decimals_and_refuses_a_second_current_line(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    department = await _department_id(client, "design")
    line = await _create_line(client, department, amount="1000")
    assert line["amount"] == "1000.00"
    assert line["version"] == 1
    assert line["current"] is True

    duplicate = await client.post(
        "/api/budgets/lines",
        json={
            "department_id": department,
            "year_month": "2026-09",
            "category": "software",
            "amount": "5",
        },
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_bad_amount_is_refused_as_problem(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    response = await client.post(
        "/api/budgets/lines",
        json={
            "department_id": await _department_id(client, "design"),
            "year_month": "2026-10",
            "category": "software",
            "amount": "12.345",
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_replace_keeps_history_with_reasons_and_rejects_a_stale_version(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    line = await _create_line(client, await _department_id(client, "design"))
    url = f"/api/budgets/lines/{line['line_id']}"

    replaced = await client.put(
        url, json={"version": 1, "amount": "1200.5", "reason": "Reforecast"}
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["version"] == 2
    assert replaced.json()["amount"] == "1200.50"
    assert replaced.json()["reason"] == "Reforecast"

    stale = await client.put(url, json={"version": 1, "amount": "1", "reason": "Stale read"})
    assert stale.status_code == 409
    assert stale.headers["content-type"].startswith("application/problem+json")

    history = (await client.get(f"{url}/history")).json()
    assert [version["amount"] for version in history] == ["1000.00", "1200.50"]
    assert [version["current"] for version in history] == [False, True]
    assert history[0]["reason"] is None


@pytest.mark.asyncio
async def test_variance_counts_only_statements_that_are_not_voided(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    await _create_line(client, await _department_id(client, "design"), amount="1000")
    csv = (
        b"transaction_ref,posted_on,department_code,category,merchant,amount_usd\n"
        b"tx-3001,2026-09-10,design,software,Figma,300\n"
    )
    imported = await client.post(
        "/api/spend/statements", files={"file": ("sept.csv", csv, "text/csv")}
    )
    assert imported.status_code == 201, imported.text

    params = {"from": "2026-09", "to": "2026-09"}
    before = (await client.get("/api/budgets/variance", params=params)).json()
    assert before[0]["budget"] == "1000.00"
    assert before[0]["actual"] == "300.00"
    assert before[0]["variance"] == "700.00"

    await client.post(
        f"/api/spend/statements/{imported.json()['id']}/void", json={"reason": "Wrong file"}
    )
    after = (await client.get("/api/budgets/variance", params=params)).json()
    assert after[0]["actual"] == "0.00"
    assert after[0]["variance"] == "1000.00"


@pytest.mark.asyncio
async def test_variance_refuses_a_backwards_range(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    response = await client.get(
        "/api/budgets/variance", params={"from": "2026-10", "to": "2026-09"}
    )
    assert response.status_code == 422
