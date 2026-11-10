"""The tools the person may use, read from the portal's OpenAPI contract.

A route becomes a tool when it carries `x-agent` and names an `x-permission` the person holds.
A route without `x-agent` is never a tool, and neither is a route whose permission is missing, so a
new route is denied until it is declared. `x-agent.confirm: true` marks a critical route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from garden.shared import as_list, as_object, as_text, is_list, is_object

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_MAX_REF_DEPTH = 32
_FILE_HELP = "Path to a local file to upload."


@dataclass(frozen=True)
class Param:
    name: str
    location: str
    required: bool
    schema: dict[str, Any]
    description: str = ""


@dataclass(frozen=True)
class FormField:
    """One field of a multipart body. A binary field takes a local file path, read at call time."""

    name: str
    binary: bool
    # How the field appears as a tool argument. For a binary field, the argument is the path.
    param: Param


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
    form_fields: tuple[FormField, ...] = ()

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

    @property
    def params(self) -> tuple[Param, ...]:
        """Every argument except the JSON body: path, query and form fields, in that order."""
        return (*self.path_params, *self.query_params, *(field.param for field in self.form_fields))

    def input_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in self.params:
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
    form_fields = _form_fields(openapi, operation)
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
        form_fields=form_fields,
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


def _form_fields(openapi: dict[str, Any], operation: dict[str, Any]) -> tuple[FormField, ...]:
    """The fields of a multipart/form-data body. Each binary field becomes a path argument.

    A single binary field is `path`. With several, each is named after its field, as `<field>_path`.
    """
    request_body = as_object(operation.get("requestBody"))
    multipart = as_object(as_object(request_body.get("content")).get("multipart/form-data"))
    if "schema" not in multipart:
        return ()
    schema = as_object(resolve(openapi, multipart["schema"], 0))
    properties = {name: as_object(raw) for name, raw in as_object(schema.get("properties")).items()}
    required = {as_text(name) for name in as_list(schema.get("required"))}
    binary = [name for name, prop in properties.items() if _is_binary(prop)]
    fields: list[FormField] = []
    for name, prop in properties.items():
        is_required = name in required
        if name in binary:
            argument = "path" if len(binary) == 1 else f"{name}_path"
            param = Param(
                name=argument,
                location="form",
                required=is_required,
                schema={"type": "string"},
                description=_FILE_HELP,
            )
            fields.append(FormField(name=name, binary=True, param=param))
        else:
            param = Param(
                name=name,
                location="form",
                required=is_required,
                schema=prop,
                description=as_text(prop.get("description")),
            )
            fields.append(FormField(name=name, binary=False, param=param))
    return tuple(fields)


def _is_binary(schema: dict[str, Any]) -> bool:
    """The portal's contract marks a file with `format: binary`, or with a content media type."""
    return schema.get("format") == "binary" or "contentMediaType" in schema


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
