"""Demo seed: a second run changes no row, and the claims cover every state."""

import pytest

from app.demo_seed import DemoIdentity, seed_demo, table_counts
from app.main import create_app
from tests.fakes import FakeEmbedder

EVERY_CLAIM_STATE = {"draft", "submitted", "approved", "rejected", "withdrawn"}


@pytest.mark.asyncio
async def test_the_demo_seed_twice_changes_no_row(clean_database: None) -> None:
    first = await seed_demo(create_app(), identity=DemoIdentity(), embedder=FakeEmbedder())
    counts_after_first = await table_counts()

    second = await seed_demo(create_app(), identity=DemoIdentity(), embedder=FakeEmbedder())
    counts_after_second = await table_counts()

    assert counts_after_second == counts_after_first
    assert second.claim_statuses == first.claim_statuses
    assert counts_after_first["spend.statement"] == 12
    assert counts_after_first["budgets.line"] >= 4 * 8 * 12
    assert counts_after_first["claims.claim"] == len(first.claim_statuses)
    assert counts_after_first["claims.claim"] >= 7


@pytest.mark.asyncio
async def test_claims_exist_in_every_state_including_one_awaiting_finance(
    clean_database: None,
) -> None:
    summary = await seed_demo(create_app(), identity=DemoIdentity(), embedder=FakeEmbedder())

    assert set(summary.claim_statuses.values()) == EVERY_CLAIM_STATE
    assert summary.awaiting_second_approval == frozenset({"Build farm hardware"})
    assert summary.collections == ("Expense policy", "Finance procedures")
