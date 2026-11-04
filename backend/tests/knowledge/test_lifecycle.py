"""The batch state machine: every allowed transition, and the ones that must be refused."""

import pytest

from app.modules.knowledge.lifecycle import (
    OPEN_STATES,
    TRANSITIONS,
    BatchEvent,
    BatchState,
    InvalidTransition,
    transition,
)


def test_a_batch_moves_from_upload_to_applied_only_through_approval() -> None:
    state = BatchState.UPLOADED
    for event in (BatchEvent.PREPARE, BatchEvent.PREPARED, BatchEvent.REQUIRE_APPROVAL):
        state = transition(state, event)
    assert state is BatchState.AWAITING_APPROVAL
    assert transition(state, BatchEvent.APPROVE) is BatchState.APPLIED
    with pytest.raises(InvalidTransition):
        _ = transition(BatchState.PREPARED, BatchEvent.APPROVE)


def test_a_batch_waits_for_approval_then_applies_or_is_rejected() -> None:
    awaiting = transition(BatchState.PREPARED, BatchEvent.REQUIRE_APPROVAL)
    assert awaiting is BatchState.AWAITING_APPROVAL
    assert transition(awaiting, BatchEvent.APPROVE) is BatchState.APPLIED
    assert transition(awaiting, BatchEvent.REJECT) is BatchState.REJECTED


def test_a_failed_preparation_ends_in_failed() -> None:
    assert transition(BatchState.PREPARING, BatchEvent.PREPARE_FAILED) is BatchState.FAILED


def test_an_applied_batch_can_be_undone_once() -> None:
    assert transition(BatchState.APPLIED, BatchEvent.UNDO) is BatchState.UNDONE
    with pytest.raises(InvalidTransition):
        _ = transition(BatchState.UNDONE, BatchEvent.UNDO)


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (BatchState.UPLOADED, BatchEvent.APPROVE),
        (BatchState.PREPARING, BatchEvent.PREPARE),
        (BatchState.PREPARED, BatchEvent.APPROVE),
        (BatchState.APPLIED, BatchEvent.CANCEL),
        (BatchState.REJECTED, BatchEvent.APPROVE),
        (BatchState.FAILED, BatchEvent.PREPARE),
        (BatchState.CANCELLED, BatchEvent.PREPARE),
    ],
)
def test_events_that_do_not_apply_are_refused(state: BatchState, event: BatchEvent) -> None:
    with pytest.raises(InvalidTransition):
        _ = transition(state, event)


def test_end_states_have_no_way_out() -> None:
    ends = {BatchState.REJECTED, BatchState.CANCELLED, BatchState.FAILED, BatchState.UNDONE}
    assert not any(source in ends for source, _ in TRANSITIONS)


def test_open_states_are_the_ones_before_apply_or_an_end() -> None:
    assert BatchState.UPLOADED in OPEN_STATES
    assert BatchState.AWAITING_APPROVAL in OPEN_STATES
    assert BatchState.APPLIED not in OPEN_STATES
    assert BatchState.FAILED not in OPEN_STATES
