"""Budget lines and the variance report, through the HTTP seam: department scope, versions and
history, and the way card statements count as actual spend."""

from decimal import Decimal
from typing import Any

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


# Variance reads card statements and approved claims. The month is far past the seeded year, so the
# test owns every row in it.
VARIANCE_MONTH = "2033-03"
MARCUS = "marcus.lee@fernhill.example"  # design employee
PRIYA = "priya.nair@fernhill.example"  # design manager
TOMAS = "tomas.berg@fernhill.example"  # engineering employee
ANA = "ana.costa@fernhill.example"  # engineering manager
LENA = "lena.fischer@fernhill.example"  # finance
RECEIPT = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


async def _receipt_sha(client: AsyncClient) -> str:
    uploaded = await client.post(
        "/api/files", files={"file": ("receipt.png", RECEIPT, "image/png")}
    )
    assert uploaded.status_code == 200, uploaded.text
    return str(uploaded.json()["sha256"])


async def _add_meal(
    client: AsyncClient, claim: dict[str, Any], amount: str, spent_on: str = f"{VARIANCE_MONTH}-05"
) -> dict[str, Any]:
    added = await client.post(
        f"/api/claims/{claim['claim']['id']}/lines",
        json={
            "expected_version": claim["claim"]["version"],
            "category": "meals",
            "spent_on": spent_on,
            "amount": amount,
            "description": "Team lunch",
            "receipt_sha256": await _receipt_sha(client),
        },
    )
    assert added.status_code == 201, added.text
    body: dict[str, Any] = added.json()
    return body


async def _claim_with_meal(client: AsyncClient, amount: str) -> dict[str, Any]:
    created = await client.post("/api/claims", json={"title": "Team lunch"})
    assert created.status_code == 201, created.text
    return await _add_meal(client, created.json(), amount)


async def _submit(client: AsyncClient, claim: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(
        f"/api/claims/{claim['claim']['id']}/submit",
        json={"expected_version": claim["claim"]["version"]},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _decide(
    client: AsyncClient, claim: dict[str, Any], decision: str, reason: str | None = None
) -> None:
    response = await client.post(
        f"/api/claims/{claim['claim']['id']}/decision",
        json={
            "expected_version": claim["claim"]["version"],
            "decision": decision,
            "reason": reason,
        },
    )
    assert response.status_code == 200, response.text


async def _import_statement(client: AsyncClient, *rows: str) -> dict[str, Any]:
    header = b"transaction_ref,posted_on,department_code,category,merchant,amount_usd\n"
    csv = header + "\n".join(rows).encode() + b"\n"
    imported = await client.post(
        "/api/spend/statements", files={"file": ("statement.csv", csv, "text/csv")}
    )
    assert imported.status_code == 201, imported.text
    body: dict[str, Any] = imported.json()
    return body


async def _variance(client: AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(
        "/api/budgets/variance", params={"from": VARIANCE_MONTH, "to": VARIANCE_MONTH}
    )
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()
    return rows


def _actual_total(rows: list[dict[str, Any]]) -> Decimal:
    return sum((Decimal(row["actual"]) for row in rows), Decimal("0.00"))


@pytest.mark.asyncio
async def test_variance_counts_only_approved_claims_and_unvoided_rows(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, LENA)
    design = await _department_id(client, "design")
    await client.post(
        "/api/budgets/lines",
        json={
            "department_id": design,
            "year_month": VARIANCE_MONTH,
            "category": "meals",
            "amount": "1000",
        },
    )
    kept = await _import_statement(client, f"tx-6001,{VARIANCE_MONTH}-07,design,meals,Cafe,20")
    assert kept["id"]
    voided = await _import_statement(client, f"tx-6002,{VARIANCE_MONTH}-08,design,meals,Cafe,50")
    voiding = await client.post(
        f"/api/spend/statements/{voided['id']}/void", json={"reason": "Wrong file"}
    )
    assert voiding.status_code == 200, voiding.text
    await _import_statement(client, f"tx-6003,{VARIANCE_MONTH}-09,design,meals,Cafe,5")

    await sign_in(client, identity, MARCUS)
    approved = await _add_meal(client, await _claim_with_meal(client, "80.00"), "25.00")
    duplicate_id = next(line["id"] for line in approved["lines"] if line["amount"] == "25.00")
    voiding_line = await client.post(
        f"/api/claims/{approved['claim']['id']}/lines/{duplicate_id}/void",
        json={"expected_version": approved["claim"]["version"], "reason": "Duplicate"},
    )
    assert voiding_line.status_code == 200, voiding_line.text
    approved = await _submit(client, voiding_line.json())
    pending = await _submit(client, await _claim_with_meal(client, "500.00"))
    await _claim_with_meal(client, "300.00")
    rejected = await _submit(client, await _claim_with_meal(client, "40.00"))
    withdrawn = await _submit(client, await _claim_with_meal(client, "60.00"))
    withdraw = await client.post(
        f"/api/claims/{withdrawn['claim']['id']}/withdraw",
        json={"expected_version": withdrawn["claim"]["version"]},
    )
    assert withdraw.status_code == 200, withdraw.text

    await sign_in(client, identity, PRIYA)
    await _decide(client, approved, "approve")
    await _decide(client, rejected, "reject", "Not needed")
    assert pending["claim"]["status"] == "submitted"

    rows = await _variance(client)
    meals = [
        row for row in rows if row["department_code"] == "design" and row["category"] == "meals"
    ]
    assert len(meals) == 1
    assert meals[0]["budget"] == "1000.00"
    # 20 and 5 from the statements, and 80 from the approved claim. The voided statement, the voided
    # claim line, and the pending, rejected, withdrawn and draft claims all stay out.
    assert meals[0]["actual"] == "105.00"
    assert meals[0]["variance"] == "895.00"


@pytest.mark.asyncio
async def test_variance_scope_and_totals_reconcile_with_the_source_rows(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, LENA)
    await _import_statement(
        client,
        f"tx-7001,{VARIANCE_MONTH}-02,design,meals,Cafe,20",
        f"tx-7002,{VARIANCE_MONTH}-03,engineering,software,Tooling,15.25",
    )

    await sign_in(client, identity, MARCUS)
    design_claim = await _submit(client, await _claim_with_meal(client, "80.10"))
    await sign_in(client, identity, PRIYA)
    await _decide(client, design_claim, "approve")

    await sign_in(client, identity, TOMAS)
    engineering_claim = await _submit(client, await _claim_with_meal(client, "60.00"))
    await sign_in(client, identity, ANA)
    await _decide(client, engineering_claim, "approve")

    # Finance sees every department, and the rows add up to the statement rows plus approved claims.
    await sign_in(client, identity, LENA)
    everything = await _variance(client)
    assert {row["department_code"] for row in everything} == {"design", "engineering"}
    assert _actual_total(everything) == Decimal("175.35")
    engineering_meals = [
        row
        for row in everything
        if row["department_code"] == "engineering" and row["category"] == "meals"
    ]
    assert engineering_meals[0]["actual"] == "60.00"

    # A manager sees only their own department, and that department's rows reconcile on their own.
    await sign_in(client, identity, PRIYA)
    design_only = await _variance(client)
    assert {row["department_code"] for row in design_only} == {"design"}
    assert _actual_total(design_only) == Decimal("100.10")

    await sign_in(client, identity, ANA)
    engineering_only = await _variance(client)
    assert {row["department_code"] for row in engineering_only} == {"engineering"}
    assert _actual_total(engineering_only) == Decimal("75.25")
