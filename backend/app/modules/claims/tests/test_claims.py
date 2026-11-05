"""Claims, through the API: drafting, submission, decisions, threshold, scope and agent limits."""

from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import AsyncClient

from app.core.agent import CONFIRMATION_HEADER
from app.core.users import CurrentPerson, current_person
from app.core.users.access import SESSION_COOKIE
from app.core.users.service import UserService, get_user_service
from app.modules.claims.ports import get_closed_months
from app.shared.request_login import VIA_AGENT, RequestLogin, current_request_login
from tests.fakes import FakeIdentity
from tests.support import sign_in

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
MARCUS = "marcus.lee@fernhill.example"  # design employee
PRIYA = "priya.nair@fernhill.example"  # design manager
LENA = "lena.fischer@fernhill.example"  # operations finance
ADMIN = "dev.admin@fernhill.example"  # operations manager and admin


class ClosedMonthsFake:
    def __init__(self, months: frozenset[str]) -> None:
        self.months = months

    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        return months & self.months


def act_as_agent(app: FastAPI) -> None:
    """Marks the next requests as coming from a CLI login of the signed-in person."""

    async def as_agent(
        request: Request,
        service: Annotated[UserService, Depends(get_user_service)],
    ) -> CurrentPerson:
        person = await service.current_person(
            request.cookies.get(SESSION_COOKIE), request.app.state.roles
        )
        _ = current_request_login.set(RequestLogin(via=VIA_AGENT, device="laptop"))
        return person

    app.dependency_overrides[current_person] = as_agent


def act_as_browser(app: FastAPI) -> None:
    _ = app.dependency_overrides.pop(current_person, None)
    _ = current_request_login.set(None)


async def receipt(client: AsyncClient, data: bytes = PNG) -> str:
    uploaded = await client.post("/api/files", files={"file": ("receipt.png", data, "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    return str(uploaded.json()["sha256"])


async def new_claim(client: AsyncClient, title: str = "Client visit") -> dict[str, Any]:
    created = await client.post("/api/claims", json={"title": title})
    assert created.status_code == 201, created.text
    return created.json()


async def add_line(
    client: AsyncClient,
    claim: dict[str, Any],
    *,
    amount: str = "120.00",
    category: str = "travel",
    spent_on: str = "2026-10-12",
    expected: int | None = None,
) -> dict[str, Any]:
    version = claim["claim"]["version"] if expected is None else expected
    added = await client.post(
        f"/api/claims/{claim['claim']['id']}/lines",
        json={
            "expected_version": version,
            "category": category,
            "spent_on": spent_on,
            "amount": amount,
            "description": "Train to the client site",
            "receipt_sha256": await receipt(client),
        },
    )
    assert added.status_code == 201, added.text
    return added.json()


async def submit(client: AsyncClient, claim: dict[str, Any]) -> Any:
    return await client.post(
        f"/api/claims/{claim['claim']['id']}/submit",
        json={"expected_version": claim["claim"]["version"]},
    )


async def decide(
    client: AsyncClient,
    claim: dict[str, Any],
    decision: str,
    reason: str | None = None,
) -> Any:
    return await client.post(
        f"/api/claims/{claim['claim']['id']}/decision",
        json={
            "expected_version": claim["claim"]["version"],
            "decision": decision,
            "reason": reason,
        },
    )


async def reload(client: AsyncClient, claim: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(f"/api/claims/{claim['claim']['id']}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_claimant_drafts_submits_and_manager_approves(
    client: AsyncClient, identity: FakeIdentity, app: FastAPI
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await new_claim(client)
    assert claim["claim"]["status"] == "draft"
    claim = await add_line(client, claim)
    assert claim["claim"]["total"] == "120.00"
    assert claim["lines"][0]["receipt_url"].startswith("/api/files/")

    submitted = await submit(client, claim)
    assert submitted.status_code == 200, submitted.text
    claim = submitted.json()
    assert claim["claim"]["status"] == "submitted"
    assert claim["actions"]["can_withdraw"] is True
    assert claim["actions"]["can_decide"] is False

    await sign_in(client, identity, PRIYA)
    queue = await client.get("/api/claims/queue")
    assert [item["id"] for item in queue.json()] == [claim["claim"]["id"]]
    approved = await decide(client, claim, "approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["claim"]["status"] == "approved"
    history = approved.json()["history"]
    assert history[0]["steps"][0]["decision"]["decided_by_name"] == "Priya Nair"


@pytest.mark.asyncio
async def test_a_line_needs_a_known_receipt_a_fixed_category_and_a_positive_amount(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await new_claim(client)
    base = {
        "expected_version": claim["claim"]["version"],
        "category": "travel",
        "spent_on": "2026-10-12",
        "amount": "10.00",
        "description": "Taxi",
        "receipt_sha256": "a" * 64,
    }
    missing_receipt = await client.post(f"/api/claims/{claim['claim']['id']}/lines", json=base)
    assert missing_receipt.status_code == 422
    assert missing_receipt.headers["content-type"].startswith("application/problem+json")
    bad_category = await client.post(
        f"/api/claims/{claim['claim']['id']}/lines",
        json={**base, "category": "yachts", "receipt_sha256": await receipt(client)},
    )
    assert bad_category.status_code == 422
    zero = await client.post(
        f"/api/claims/{claim['claim']['id']}/lines",
        json={**base, "amount": "0", "receipt_sha256": await receipt(client)},
    )
    assert zero.status_code == 422


@pytest.mark.asyncio
async def test_only_the_claimant_may_change_a_claim(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await new_claim(client)
    await sign_in(client, identity, PRIYA)
    response = await add_line_as_other(client, claim)
    assert response.status_code == 403


async def add_line_as_other(client: AsyncClient, claim: dict[str, Any]) -> Any:
    return await client.post(
        f"/api/claims/{claim['claim']['id']}/lines",
        json={
            "expected_version": claim["claim"]["version"],
            "category": "meals",
            "spent_on": "2026-10-12",
            "amount": "20.00",
            "description": "Lunch",
            "receipt_sha256": await receipt(client),
        },
    )


@pytest.mark.asyncio
async def test_nobody_approves_their_own_claim_whatever_their_roles(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, PRIYA)
    claim = await add_line(client, await new_claim(client))
    assert (await submit(client, claim)).status_code == 200
    claim = await reload(client, claim)
    refused = await decide(client, claim, "approve")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Nobody can approve or reject their own claim."


@pytest.mark.asyncio
async def test_rejection_needs_a_reason_and_a_rejected_claim_can_be_corrected_and_resubmitted(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await add_line(client, await new_claim(client))
    assert (await submit(client, claim)).status_code == 200
    claim = await reload(client, claim)

    await sign_in(client, identity, PRIYA)
    no_reason = await decide(client, claim, "reject")
    assert no_reason.status_code == 422
    rejected = await decide(client, claim, "reject", "Receipt is unreadable.")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["claim"]["status"] == "rejected"

    await sign_in(client, identity, MARCUS)
    claim = rejected.json()
    fixed = await add_line(client, claim, amount="45.00", category="meals")
    resubmitted = await submit(client, fixed)
    assert resubmitted.status_code == 200, resubmitted.text
    claim = resubmitted.json()
    assert claim["claim"]["status"] == "submitted"
    states = [run["state"] for run in claim["history"]]
    assert states == ["pending", "rejected"]
    earlier = claim["history"][1]["steps"][0]["decision"]
    assert earlier["reason"] == "Receipt is unreadable."


@pytest.mark.asyncio
async def test_a_claim_above_the_threshold_needs_finance_after_the_manager(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await add_line(client, await new_claim(client), amount="600.00")
    assert (await submit(client, claim)).status_code == 200
    claim = await reload(client, claim)

    await sign_in(client, identity, PRIYA)
    after_manager = await decide(client, claim, "approve")
    assert after_manager.status_code == 200
    claim = after_manager.json()
    assert claim["claim"]["status"] == "submitted"
    assert [step["role"] for step in claim["history"][0]["steps"]] == ["manager", "finance"]

    refused = await decide(client, claim, "approve")
    assert refused.status_code == 403, refused.text

    await sign_in(client, identity, LENA)
    final = await decide(client, claim, "approve")
    assert final.status_code == 200, final.text
    assert final.json()["claim"]["status"] == "approved"


@pytest.mark.asyncio
async def test_a_claim_at_the_threshold_needs_only_the_manager(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await add_line(client, await new_claim(client), amount="500.00")
    assert (await submit(client, claim)).status_code == 200
    claim = await reload(client, claim)
    await sign_in(client, identity, PRIYA)
    approved = await decide(client, claim, "approve")
    assert approved.json()["claim"]["status"] == "approved"


@pytest.mark.asyncio
async def test_a_manager_sees_only_their_department_and_finance_sees_all(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    design = await add_line(client, await new_claim(client, "Design brief"))
    assert (await submit(client, design)).status_code == 200

    await sign_in(client, identity, LENA)
    operations = await add_line(client, await new_claim(client, "Operations brief"))
    assert (await submit(client, operations)).status_code == 200

    await sign_in(client, identity, PRIYA)
    priya = (await client.get("/api/claims")).json()
    assert "Design brief" in [item["title"] for item in priya]
    assert "Operations brief" not in [item["title"] for item in priya]
    assert (await client.get(f"/api/claims/{design['claim']['id']}")).status_code == 200
    hidden = await client.get(f"/api/claims/{operations['claim']['id']}")
    assert hidden.status_code == 404

    await sign_in(client, identity, LENA)
    finance = (await client.get("/api/claims")).json()
    assert {"Design brief", "Operations brief"} <= {item["title"] for item in finance}


@pytest.mark.asyncio
async def test_an_employee_sees_only_their_own_claims_and_cannot_decide(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, LENA)
    claim = await add_line(client, await new_claim(client, "Lena claim"))
    assert (await submit(client, claim)).status_code == 200

    await sign_in(client, identity, MARCUS)
    assert (await client.get("/api/claims")).json() == []
    assert (await client.get(f"/api/claims/{claim['claim']['id']}")).status_code == 404
    assert (await client.get("/api/claims/queue")).status_code == 403


@pytest.mark.asyncio
async def test_an_agent_drafts_but_cannot_submit(
    client: AsyncClient, identity: FakeIdentity, app: FastAPI
) -> None:
    await sign_in(client, identity, MARCUS)
    act_as_agent(app)
    claim = await add_line(client, await new_claim(client, "Agent draft"))
    assert claim["claim"]["status"] == "draft"
    refused_submit = await submit(client, claim)
    assert refused_submit.status_code == 403
    assert "Only a person" in refused_submit.json()["detail"]
    act_as_browser(app)


@pytest.mark.asyncio
async def test_an_agent_withdraw_waits_for_the_persons_confirmation(
    client: AsyncClient, identity: FakeIdentity, app: FastAPI
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await add_line(client, await new_claim(client))
    assert (await submit(client, claim)).status_code == 200
    claim = await reload(client, claim)
    claim_id = claim["claim"]["id"]
    body = {"expected_version": claim["claim"]["version"]}

    act_as_agent(app)
    challenged = await client.post(f"/api/claims/{claim_id}/withdraw", json=body)
    assert challenged.status_code == 428, challenged.text
    confirmation_id = challenged.json()["confirmation_id"]

    act_as_browser(app)
    approved = await client.post(
        f"/api/agent/my-confirmations/{confirmation_id}/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text

    act_as_agent(app)
    withdrawn = await client.post(
        f"/api/claims/{claim_id}/withdraw",
        json=body,
        headers={CONFIRMATION_HEADER: confirmation_id},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["claim"]["status"] == "withdrawn"
    _ = app.dependency_overrides.pop(current_person, None)


@pytest.mark.asyncio
async def test_a_person_can_withdraw_before_a_decision_but_not_after(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await add_line(client, await new_claim(client))
    assert (await submit(client, claim)).status_code == 200
    claim = await reload(client, claim)
    withdrawn = await client.post(
        f"/api/claims/{claim['claim']['id']}/withdraw",
        json={"expected_version": claim["claim"]["version"]},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["claim"]["status"] == "withdrawn"

    second = await client.post(
        f"/api/claims/{claim['claim']['id']}/withdraw",
        json={"expected_version": withdrawn.json()["claim"]["version"]},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_a_stale_version_answers_409_and_changes_nothing(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await add_line(client, await new_claim(client))
    stale = claim["claim"]["version"]
    await add_line(client, claim, expected=stale)
    late = await add_line_stale(client, claim, stale)
    assert late.status_code == 409
    current = await reload(client, claim)
    assert current["claim"]["total"] == "240.00"
    assert len(current["lines"]) == 2


async def add_line_stale(client: AsyncClient, claim: dict[str, Any], version: int) -> Any:
    return await client.post(
        f"/api/claims/{claim['claim']['id']}/lines",
        json={
            "expected_version": version,
            "category": "other",
            "spent_on": "2026-10-12",
            "amount": "5.00",
            "description": "Stale write",
            "receipt_sha256": await receipt(client),
        },
    )


@pytest.mark.asyncio
async def test_a_voided_line_leaves_the_total_and_stays_out_of_the_claim(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, MARCUS)
    claim = await add_line(client, await new_claim(client), amount="30.00")
    claim = await add_line(client, claim, amount="70.00")
    line_id = claim["lines"][0]["id"]
    voided = await client.post(
        f"/api/claims/{claim['claim']['id']}/lines/{line_id}/void",
        json={"expected_version": claim["claim"]["version"], "reason": "Duplicate"},
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["claim"]["total"] == "70.00"
    assert len(voided.json()["lines"]) == 1


@pytest.mark.asyncio
async def test_a_closed_month_refuses_new_lines_and_submission(
    client: AsyncClient, identity: FakeIdentity, app: FastAPI
) -> None:
    app.dependency_overrides[get_closed_months] = lambda: ClosedMonthsFake(frozenset({"2026-10"}))
    try:
        await sign_in(client, identity, MARCUS)
        claim = await new_claim(client)
        closed_line = await client.post(
            f"/api/claims/{claim['claim']['id']}/lines",
            json={
                "expected_version": claim["claim"]["version"],
                "category": "travel",
                "spent_on": "2026-10-02",
                "amount": "10.00",
                "description": "Closed month",
                "receipt_sha256": await receipt(client),
            },
        )
        assert closed_line.status_code == 422
        open_line = await add_line(client, claim, spent_on="2026-11-02")
        assert (await submit(client, open_line)).status_code == 200
    finally:
        _ = app.dependency_overrides.pop(get_closed_months, None)


@pytest.mark.asyncio
async def test_a_month_closed_in_budgets_refuses_claim_lines_for_it(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, LENA)
    closed = await client.post("/api/budgets/months/2026-10/close", json={"reason": "Books sent"})
    assert closed.status_code == 201, closed.text
    await sign_in(client, identity, MARCUS)
    claim = await new_claim(client)
    refused = await client.post(
        f"/api/claims/{claim['claim']['id']}/lines",
        json={
            "expected_version": claim["claim"]["version"],
            "category": "travel",
            "spent_on": "2026-10-02",
            "amount": "10.00",
            "description": "Closed month",
            "receipt_sha256": await receipt(client),
        },
    )
    assert refused.status_code == 422
    assert "2026-10" in refused.text
    _ = await add_line(client, claim, spent_on="2026-11-02")


@pytest.mark.asyncio
async def test_approved_spend_counts_only_approved_claims_by_department_month_and_category(
    client: AsyncClient, identity: FakeIdentity, app: FastAPI
) -> None:
    from app.config import get_settings
    from app.core.approvals import ApprovalService
    from app.core.approvals.repository import ApprovalRepository
    from app.core.audit import AuditService
    from app.core.audit.repository import AuditRepository
    from app.core.files import FileService
    from app.core.files.repository import FileRepository
    from app.modules.claims.drafting.repository import ClaimRepository
    from app.modules.claims.drafting.service import ClaimsService
    from app.shared.clock import get_clock
    from app.shared.db import session_factory

    await sign_in(client, identity, MARCUS)
    approved = await add_line(client, await new_claim(client), amount="80.00", category="meals")
    assert (await submit(client, approved)).status_code == 200
    await sign_in(client, identity, PRIYA)
    approved = await reload(client, approved)
    assert (await decide(client, approved, "approve")).status_code == 200
    await sign_in(client, identity, MARCUS)
    pending = await add_line(client, await new_claim(client), amount="999.00", category="meals")
    assert (await submit(client, pending)).status_code == 200

    async with session_factory(get_settings().database_url)() as session:
        clock = app.dependency_overrides[get_clock]()
        audit = AuditService(AuditRepository(session), clock)
        files = FileService(FileRepository(session), audit, clock, get_settings())
        approvals = ApprovalService(ApprovalRepository(session), clock)
        service = ClaimsService(
            ClaimRepository(session), approvals, audit, files, ClosedMonthsFake(frozenset()), clock
        )
        rows = await service.approved_spend(from_month="2026-01", to_month="2026-12")
    assert len(rows) == 1
    assert rows[0].month == "2026-10"
    assert rows[0].category == "meals"
    assert str(rows[0].amount) == "80.00"
