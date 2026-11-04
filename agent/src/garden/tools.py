"""The tools the person may use, read from the portal's OpenAPI contract.

A route becomes a tool when it carries `x-agent` and names an `x-permission` the person holds.
A route without `x-agent` is never a tool, and neither is a route whose permission is missing, so a
new route is denied until it is declared. `x-agent.confirm: true` marks a critical route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from garden.jsonio import as_list, as_object, as_text, is_list, is_object

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_MAX_REF_DEPTH = 8


@dataclass(frozen=True)
class Param:
    name: str
    location: str
    required: bool
    schema: dict[str, Any]
    description: str = ""


@dataclass(frozen=True)
class Tool:
    op_id: str
    method: str
    path: str
    summary: str
    permission: str
    critical: bool
    path_params: tuple[Param, ...] = ()
    query_params: tuple[Param, ...] = ()
    body_schema: dict[str, Any] | None = None
    body_required: bool = False

    @property
    def name(self) -> str:
        """The MCP tool name: the operation id with dots turned into underscores."""
        return self.op_id.replace(".", "_")

    @property
    def read_only(self) -> bool:
        return self.method == "get"

    def hints(self) -> dict[str, bool]:
        """MCP annotations. Every hint is set, since an unset destructive hint reads as unsafe."""
        if self.read_only:
            return {"readOnlyHint": True}
        return {"readOnlyHint": False, "destructiveHint": self.critical}

    def input_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in (*self.path_params, *self.query_params):
            properties[param.name] = {**param.schema, "description": param.description}
            if param.required:
                required.append(param.name)
        if self.body_schema is not None:
            properties["body"] = self.body_schema
            if self.body_required:
                required.append("body")
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema


def build_tools(openapi: dict[str, Any], permissions: frozenset[str]) -> list[Tool]:
    tools: list[Tool] = []
    paths = as_object(openapi.get("paths"))
    for path in sorted(paths):
        item = as_object(paths[path])
        for method in HTTP_METHODS:
            operation = as_object(item.get(method))
            if not operation:
                continue
            tool = _tool_for(openapi, path, method, operation, permissions)
            if tool is not None:
                tools.append(tool)
    return tools


def _tool_for(
    openapi: dict[str, Any],
    path: str,
    method: str,
    operation: dict[str, Any],
    permissions: frozenset[str],
) -> Tool | None:
    if not is_object(operation.get("x-agent")):
        return None
    agent = as_object(operation.get("x-agent"))
    if agent.get("tool") is False:
        # `tool: false` marks a route for programs, such as the model gateway, not for people.
        return None
    permission = operation.get("x-permission")
    if not isinstance(permission, str) or permission not in permissions:
        return None
    op_id = as_text(operation.get("operationId"))
    if not op_id:
        return None
    path_params: list[Param] = []
    query_params: list[Param] = []
    for raw in as_list(operation.get("parameters")):
        param = _param(openapi, as_object(raw))
        if param is None:
            continue
        if param.location == "path":
            path_params.append(param)
        else:
            query_params.append(param)
    body_schema, body_required = _body(openapi, operation)
    return Tool(
        op_id=op_id,
        method=method,
        path=path,
        summary=as_text(operation.get("description") or operation.get("summary"), op_id),
        permission=permission,
        critical=agent.get("confirm") is True,
        path_params=tuple(path_params),
        query_params=tuple(query_params),
        body_schema=body_schema,
        body_required=body_required,
    )


def _param(openapi: dict[str, Any], raw: dict[str, Any]) -> Param | None:
    location = raw.get("in")
    if location not in ("path", "query"):
        return None
    schema = as_object(resolve(openapi, raw.get("schema", {}), 0))
    return Param(
        name=as_text(raw.get("name")),
        location=as_text(location),
        required=bool(raw.get("required", location == "path")),
        schema=schema,
        description=as_text(raw.get("description")),
    )


def _body(openapi: dict[str, Any], operation: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    request_body = as_object(operation.get("requestBody"))
    json_body = as_object(as_object(request_body.get("content")).get("application/json"))
    if "schema" not in json_body:
        return None, False
    schema = as_object(resolve(openapi, json_body["schema"], 0))
    return schema, bool(request_body.get("required", False))


def resolve(openapi: dict[str, Any], node: object, depth: int) -> object:
    """Inline local `$ref`s so each schema stands alone. Depth-limited, so recursion stops."""
    if depth > _MAX_REF_DEPTH:
        return {}
    if is_list(node):
        return [resolve(openapi, item, depth + 1) for item in node]
    fields = as_object(node)
    if not fields:
        return node
    ref = fields.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: object = openapi
        for part in ref[2:].split("/"):
            target = as_object(target).get(part)
        others = {key: value for key, value in fields.items() if key != "$ref"}
        return {
            **as_object(resolve(openapi, target, depth + 1)),
            **as_object(resolve(openapi, others, depth + 1)),
        }
    return {key: resolve(openapi, value, depth + 1) for key, value in fields.items()}
