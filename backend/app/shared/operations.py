"""Route metadata for agents: a stable operation id, a description, and whether it is critical.

Every API route names itself with `op(...)`, so the contract gives agents the same names and
descriptions the screens never show them.
"""

import re
from typing import Any, TypedDict

# module.resource.verb, in lower case, with underscores inside a part.
OPERATION_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


class OperationMeta(TypedDict):
    operation_id: str
    description: str
    openapi_extra: dict[str, Any]


def op(
    operation_id: str,
    description: str,
    *,
    confirm: bool = False,
    tool: bool = True,
    save: bool = True,
) -> OperationMeta:
    """Name a route for the contract. Pass the result to the route decorator as keyword arguments.

    `confirm=True` marks a critical route: an agent tool asks the person before calling it. It is
    written as `x-agent.confirm` in the contract, and every route states it, so it is never unset.

    `tool=False` keeps the route out of the CLI's tools and MCP server, for a route an agent has no
    use for, such as a model gateway that only the portal itself calls.

    `save=False` marks a request that changes data but is limited by its own rules, so it does not
    count against an agent's save limit. Every other save counts.
    """
    if OPERATION_ID_PATTERN.fullmatch(operation_id) is None:
        raise ValueError(f"Operation id {operation_id!r} must be module.resource.verb.")
    if not description.strip():
        raise ValueError(f"Operation {operation_id} needs a description for agents.")
    return {
        "operation_id": operation_id,
        "description": description,
        "openapi_extra": {"x-agent": {"confirm": confirm, "tool": tool, "save": save}},
    }
