"""Identifier helpers shared by every module."""

import uuid

import uuid6


def new_id() -> uuid.UUID:
    """Return a time-ordered UUIDv7 primary key."""
    return uuid6.uuid7()
