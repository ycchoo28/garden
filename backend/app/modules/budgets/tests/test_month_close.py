"""Month close in budgets, through the HTTP seam: a month closes once and never reopens."""

import pytest
from httpx import AsyncClient

from tests.fakes import FakeIdentity
from tests.support import sign_in

LENA = "lena.fischer@fernhill.example"  # finance
PRIYA = "priya.nair@fernhill.example"  # design manager
# A month far past the seeded year, so the test owns it.
MONTH = "2033-06"


@pytest.mark.asyncio
async def test_finance_closes_a_month_once_and_it_is_listed(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, LENA)
    closed = await client.post(f"/api/budgets/months/{MONTH}/close", json={"reason": "Books sent"})
    assert closed.status_code == 201, closed.text
    assert closed.json()["year_month"] == MONTH
    assert closed.json()["reason"] == "Books sent"

    again = await client.post(f"/api/budgets/months/{MONTH}/close", json={"reason": "Again"})
    assert again.status_code == 409

    listed = (await client.get("/api/budgets/months")).json()
    assert MONTH in [row["year_month"] for row in listed]


@pytest.mark.asyncio
async def test_a_manager_cannot_close_or_list_closed_months(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, PRIYA)
    assert (await client.get("/api/budgets/months")).status_code == 403
    closed = await client.post(f"/api/budgets/months/{MONTH}/close", json={"reason": "Mine"})
    assert closed.status_code == 403


@pytest.mark.asyncio
async def test_a_month_must_be_year_month_and_a_reason_is_required(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, LENA)
    bad_month = await client.post("/api/budgets/months/2033-13/close", json={"reason": "Why"})
    assert bad_month.status_code == 422
    no_reason = await client.post(f"/api/budgets/months/{MONTH}/close", json={"reason": "  "})
    assert no_reason.status_code == 422
