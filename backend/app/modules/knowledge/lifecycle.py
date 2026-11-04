"""The import batch state machine.

Pure: no database and no clock, so every transition is tested directly.
"""

from enum import StrEnum


class BatchState(StrEnum):
    UPLOADED = "uploaded"
    PREPARING = "preparing"
    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLIED = "applied"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNDONE = "undone"


class BatchEvent(StrEnum):
    PREPARE = "prepare"
    PREPARED = "prepared"
    PREPARE_FAILED = "prepare_failed"
    REQUIRE_APPROVAL = "require_approval"
    APPLY = "apply"
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"
    UNDO = "undo"


class InvalidTransition(Exception):
    """The event does not apply in the batch's current state."""


TRANSITIONS: dict[tuple[BatchState, BatchEvent], BatchState] = {
    (BatchState.UPLOADED, BatchEvent.PREPARE): BatchState.PREPARING,
    (BatchState.PREPARING, BatchEvent.PREPARED): BatchState.PREPARED,
    (BatchState.PREPARING, BatchEvent.PREPARE_FAILED): BatchState.FAILED,
    (BatchState.PREPARED, BatchEvent.REQUIRE_APPROVAL): BatchState.AWAITING_APPROVAL,
    (BatchState.PREPARED, BatchEvent.APPLY): BatchState.APPLIED,
    (BatchState.AWAITING_APPROVAL, BatchEvent.APPROVE): BatchState.APPLIED,
    (BatchState.AWAITING_APPROVAL, BatchEvent.REJECT): BatchState.REJECTED,
    (BatchState.UPLOADED, BatchEvent.CANCEL): BatchState.CANCELLED,
    (BatchState.PREPARED, BatchEvent.CANCEL): BatchState.CANCELLED,
    (BatchState.AWAITING_APPROVAL, BatchEvent.CANCEL): BatchState.CANCELLED,
    (BatchState.APPLIED, BatchEvent.UNDO): BatchState.UNDONE,
}

# A batch that is still open. Only one open batch may exist per collection.
OPEN_STATES: frozenset[BatchState] = frozenset(
    {
        BatchState.UPLOADED,
        BatchState.PREPARING,
        BatchState.PREPARED,
        BatchState.AWAITING_APPROVAL,
    }
)


def transition(state: BatchState, event: BatchEvent) -> BatchState:
    """Return the next state, or raise InvalidTransition when the event does not apply."""
    try:
        return TRANSITIONS[(state, event)]
    except KeyError as error:
        raise InvalidTransition(f"A batch that is {state} cannot {event}.") from error
