"""Shared structural types for eval tasks and their reference solutions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol


class EvalTask(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def prompt(self) -> str: ...

    @property
    def asking_person(self) -> str: ...

    @property
    def confirmation(self) -> Literal["approve", "deny", "none"]: ...

    @property
    def suite(self) -> Literal["capability", "regression"]: ...

    @property
    def area(self) -> str: ...

    @property
    def budget_tool_calls(self) -> int | None: ...

    @property
    def budget_seconds(self) -> int | None: ...

    @property
    def budget_tokens(self) -> int | None: ...

    @property
    def web_tools_off(self) -> bool: ...

    @property
    def requires_embedding(self) -> bool: ...

    @property
    def graders(self) -> tuple[Any, ...]: ...

    @property
    def perfect(self) -> Any: ...

    @property
    def wrong(self) -> Any: ...


ReferenceSolution = Callable[..., str]
