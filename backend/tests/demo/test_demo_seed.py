"""Demo seed: a second run changes no row, and the claims cover every state."""

from collections.abc import Sequence

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
    # Every month but the last two is closed.
    assert counts_after_first["budgets.month_close"] == 10
    assert counts_after_first["budgets.line"] >= 2 * 8 * 12
    assert counts_after_first["claims.claim"] == len(first.claim_statuses)
    assert counts_after_first["claims.claim"] >= 8


@pytest.mark.asyncio
async def test_claims_exist_in_every_state_including_one_awaiting_finance(
    clean_database: None,
) -> None:
    summary = await seed_demo(create_app(), identity=DemoIdentity(), embedder=FakeEmbedder())

    assert set(summary.claim_statuses.values()) == EVERY_CLAIM_STATE
    assert summary.awaiting_second_approval == frozenset({"Render farm hardware"})
    assert summary.collections == ("Expense policy", "Finance procedures")


class _StoppedWhilePreparingEmbedder:
    """Fails its first embedding call, as a run stopped in the middle of preparing a batch does."""

    def __init__(self) -> None:
        self._stopped = False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._stopped:
            self._stopped = True
            raise RuntimeError("stopped while preparing a batch")
        return await FakeEmbedder().embed(texts)


@pytest.mark.asyncio
async def test_a_run_after_one_stopped_while_preparing_finishes(clean_database: None) -> None:
    with pytest.raises(RuntimeError, match="stopped while preparing"):
        _ = await seed_demo(
            create_app(), identity=DemoIdentity(), embedder=_StoppedWhilePreparingEmbedder()
        )

    summary = await seed_demo(create_app(), identity=DemoIdentity(), embedder=FakeEmbedder())

    assert summary.collections == ("Expense policy", "Finance procedures")
    assert set(summary.claim_statuses.values()) == EVERY_CLAIM_STATE
