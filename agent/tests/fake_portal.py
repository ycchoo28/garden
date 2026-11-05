"""A fake portal for the CLI tests: the contract, the signed-in person, the confirmation gate,
and the CLI login PKCE exchange. It answers the same headers and problems as the real portal."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

CONFIRMATION_HEADER = "x-garden-confirmation"
GOOD_TOKEN = "good-token"
EXPIRES_AT = "2099-01-01T00:00:00+00:00"
BATCH_UPLOAD = re.compile(r"/api/knowledge/batches/([^/]+)/files")


@dataclass
class Upload:
    """One multipart upload the portal received, and the status it answered with."""

    route: str
    status: int
    field: str
    filename: str
    content_type: str
    data: bytes
    fields: dict[str, str]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass
class FakePortal:
    version: str = "v1"
    min_cli: str = "0.1.0"
    permissions: list[str] = field(
        default_factory=lambda: [
            "files.view",
            "files.delete",
            "files.upload",
            "knowledge.batch.prepare",
        ]
    )
    # Makes the file upload gated, as a critical route is. The real contract does not gate uploads.
    gate_uploads: bool = False
    uploads: list[Upload] = field(default_factory=lambda: list[Upload]())
    name: str = "Marcus Lee"
    confirmations: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    retries: list[str] = field(default_factory=lambda: list[str]())
    deleted: list[str] = field(default_factory=lambda: list[str]())
    authorizations: dict[str, tuple[str, str]] = field(
        default_factory=lambda: dict[str, tuple[str, str]]()
    )
    logins: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    # The files the installer fetches, by name. Each is served from the manifest below.
    downloads: dict[str, bytes] = field(default_factory=lambda: dict[str, bytes]())
    # The model the gateway names as `default`. None means the portal names no model.
    model_default: str | None = "deepseek-v4-flash"
    url: str = ""
    _server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        portal = self

        class Handler(_Handler):
            fake = portal

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address[:2]
        self.url = f"http://{host}:{port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def decide(self, confirmation_id: str, status: str) -> None:
        self.confirmations[confirmation_id] = status

    def contract(self) -> dict[str, Any]:
        item_params = [
            {"name": "sha256", "in": "path", "required": True, "schema": {"type": "string"}}
        ]
        file_upload = {
            "operationId": "files.file.upload",
            "description": "Upload one file. Accepts PDF, PNG, JPEG or plain text.",
            "x-agent": {"confirm": self.gate_uploads, "tool": True, "save": True},
            "x-permission": "files.upload",
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "file": {
                                    "type": "string",
                                    "contentMediaType": "application/octet-stream",
                                    "title": "File",
                                }
                            },
                            "required": ["file"],
                        }
                    }
                },
            },
        }
        batch_upload = {
            "operationId": "knowledge.batch.upload",
            "description": "Add one Markdown, text or PDF file to a batch that is still uploading.",
            "x-agent": {"confirm": False, "tool": True, "save": True},
            "x-permission": "knowledge.batch.prepare",
            "parameters": [
                {
                    "name": "batch_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "file": {
                                    "type": "string",
                                    "contentMediaType": "application/octet-stream",
                                    "title": "File",
                                },
                                "translation_group": {
                                    "anyOf": [
                                        {"maxLength": 200, "type": "string"},
                                        {"type": "null"},
                                    ],
                                    "title": "Translation Group",
                                },
                            },
                            "required": ["file"],
                        }
                    }
                },
            },
        }
        files_path: dict[str, Any] = {"post": file_upload}
        paths: dict[str, Any] = {
            "/api/knowledge/batches/{batch_id}/files": {"post": batch_upload},
            "/api/files": files_path,
            "/api/files/{sha256}": {
                "get": {
                    "operationId": "files.file.read",
                    "description": "Download a file by its SHA-256.",
                    "x-agent": {"confirm": False, "tool": True, "save": True},
                    "x-permission": "files.view",
                    "parameters": item_params,
                },
                "delete": {
                    "operationId": "files.file.delete",
                    "description": "Delete a file you first uploaded.",
                    "x-agent": {"confirm": True, "tool": True, "save": True},
                    "x-permission": "files.delete",
                    "parameters": item_params,
                },
            },
            "/api/agent/model/v1/chat/completions": {
                "post": {
                    "operationId": "agent.model.chat_completion",
                    "description": "Chat with a model.",
                    "x-agent": {"confirm": False, "tool": False, "save": True},
                    "x-permission": "agent.model.use",
                }
            },
        }
        if self.version == "v2":
            files_path["get"] = {
                "operationId": "files.file.list",
                "description": "List files.",
                "x-agent": {"confirm": False, "tool": True, "save": True},
                "x-permission": "files.view",
            }
        return {"openapi": "3.1.0", "info": {"title": "garden", "version": "1"}, "paths": paths}


def _boundary(content_type: str) -> bytes | None:
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    return match.group(1).encode() if match else None


def _header_param(headers: str, key: str) -> str | None:
    match = re.search(rf'\b{key}="([^"]*)"', headers)
    return match.group(1) if match else None


class _Handler(BaseHTTPRequestHandler):
    fake: FakePortal
    # The status of the last answer, so an upload records the gate's answer (428, 403 or 200).
    sent_status: int = 0

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(
        self,
        status: int,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        problem: bool = False,
    ) -> None:
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        self.sent_status = status
        self.send_response(status)
        self.send_header(
            "Content-Type", "application/problem+json" if problem else "application/json"
        )
        self.send_header("X-Garden-Contract-Version", self.fake.version)
        self.send_header("X-Garden-Min-CLI-Version", self.fake.min_cli)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _problem(self, status: int, title: str, **extra: Any) -> None:
        self._send(status, {"title": title, "status": status, **extra}, problem=True)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def _bearer_ok(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {GOOD_TOKEN}"

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/api/openapi.json":
            self._send(200, self.fake.contract())
            return
        if parts.path == "/api/agent/manifest":
            self._send(200, self._manifest())
            return
        if parts.path.startswith("/api/agent/downloads/"):
            self._download(parts.path.rsplit("/", 1)[1])
            return
        if parts.path == "/api/agent/cli/authorize":
            self._authorize(parse_qs(parts.query))
            return
        if not self._bearer_ok():
            self._problem(401, "Unauthorized")
            return
        if parts.path == "/api/agent/model/v1/models":
            self._send(
                200,
                {
                    "object": "list",
                    "data": [{"id": "deepseek-v4-flash", "object": "model", "owned_by": "garden"}],
                    "default": self.fake.model_default,
                },
            )
            return
        if parts.path == "/api/users/me":
            self._send(
                200,
                {
                    "name": self.fake.name,
                    "email": "marcus.lee@fernhill.example",
                    "permissions": sorted(self.fake.permissions),
                },
            )
            return
        if parts.path.startswith("/api/agent/my-confirmations/"):
            confirmation_id = parts.path.rsplit("/", 1)[1]
            status = self.fake.confirmations.get(confirmation_id, "pending")
            self._send(200, {"id": confirmation_id, "status": status})
            return
        if parts.path.startswith("/api/files/"):
            sha = parts.path.rsplit("/", 1)[1]
            if "files.view" not in self.fake.permissions:
                self._problem(403, "Forbidden")
                return
            self._send(200, {"sha256": sha})
            return
        self._problem(404, "Not Found")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/agent/cli/approvals":
            self._create_login(self._body())
            return
        if path == "/api/files":
            self._upload(path, "files.upload", 200)
            return
        batch = BATCH_UPLOAD.fullmatch(path)
        if batch is not None:
            self._upload(path, "knowledge.batch.prepare", 201)
            return
        self._problem(404, "Not Found")

    def _upload(self, route: str, permission: str, status: int) -> None:
        """Accepts a multipart upload the way the portal does: the same gate, and the file kept."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw: bytes = self.rfile.read(length) if length else b""
        if not self._bearer_ok():
            self._problem(401, "Unauthorized")
            return
        if permission not in self.fake.permissions:
            self._problem(403, "Forbidden")
            return
        boundary = _boundary(self.headers.get("Content-Type", ""))
        if boundary is None:
            self._problem(422, "Validation failed", detail="The body is not multipart.")
            return
        file_part: tuple[str, str, str, bytes] | None = None
        texts: dict[str, str] = {}
        for chunk in raw.split(b"--" + boundary):
            if not chunk.strip() or chunk.startswith(b"--"):
                continue
            part_head, _, part_body = chunk.removeprefix(b"\r\n").partition(b"\r\n\r\n")
            part_body = part_body.removesuffix(b"\r\n")
            headers = part_head.decode("utf-8")
            name = _header_param(headers, "name") or ""
            filename = _header_param(headers, "filename")
            type_match = re.search(r"Content-Type: (\S+)", headers)
            content_type = type_match.group(1) if type_match else "text/plain"
            if filename is not None:
                file_part = (name, filename, content_type, part_body)
            else:
                texts[name] = part_body.decode("utf-8")
        if file_part is None:
            self._problem(
                422, "Validation failed", errors=[{"field": "file", "message": "Missing"}]
            )
            return
        field_name, filename, content_type, data = file_part
        upload = Upload(
            route=route,
            status=0,
            field=field_name,
            filename=filename,
            content_type=content_type,
            data=data,
            fields=texts,
        )
        self.fake.uploads.append(upload)
        if self.fake.gate_uploads and not self._confirmed():
            upload.status = self.sent_status
            return
        digest = hashlib.sha256(data).hexdigest()
        upload.status = status
        if route == "/api/files":
            body: dict[str, Any] = {
                "sha256": digest,
                "size_bytes": len(data),
                "content_type": content_type,
                "url": f"/api/files/{digest}",
            }
        else:
            body = {"batch_id": route.split("/")[4], "name": filename, "sha256": digest}
        self._send(status, body)

    def do_DELETE(self) -> None:
        if not self._bearer_ok():
            self._problem(401, "Unauthorized")
            return
        path = urlsplit(self.path).path
        if not path.startswith("/api/files/"):
            self._problem(404, "Not Found")
            return
        if "files.delete" not in self.fake.permissions:
            self._problem(403, "Forbidden")
            return
        if not self._confirmed():
            return
        self.fake.deleted.append(path.rsplit("/", 1)[1])
        self._send(204)

    def _confirmed(self) -> bool:
        """The gate for a critical action. Without an approved confirmation it answers the problem
        (428, or 403 for a retry that is not approved) and returns False, as the portal does."""
        presented = self.headers.get(CONFIRMATION_HEADER)
        if presented is None:
            confirmation_id = f"conf-{len(self.fake.confirmations) + 1}"
            self.fake.confirmations[confirmation_id] = "pending"
            self._problem(
                428,
                "Confirmation Required",
                detail="The person must approve this action in the browser first.",
                confirmation_id=confirmation_id,
                confirmation_url=f"{self.fake.url}/agent/confirmations/{confirmation_id}",
                expires_at=EXPIRES_AT,
            )
            return False
        self.fake.retries.append(presented)
        if self.fake.confirmations.get(presented) != "approved":
            self._problem(403, "Forbidden", detail="This confirmation is not approved.")
            return False
        self.fake.confirmations[presented] = "used"
        return True

    def _manifest(self) -> dict[str, Any]:
        return {
            "artifacts": {
                name: {
                    "url": f"/api/agent/downloads/{name}",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                }
                for name, raw in self.fake.downloads.items()
            }
        }

    def _download(self, name: str) -> None:
        raw = self.fake.downloads.get(name)
        if raw is None:
            self._problem(404, "Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorize(self, query: dict[str, list[str]]) -> None:
        redirect_uri = query.get("redirect_uri", [""])[0]
        if not redirect_uri.startswith("http://127.0.0.1:"):
            self._problem(403, "Forbidden")
            return
        code = f"code-{len(self.fake.authorizations) + 1}"
        challenge = query["code_challenge"][0]
        nonce = query["nonce"][0]
        self.fake.authorizations[code] = (challenge, nonce)
        state = query["state"][0]
        # The identity provider approves at once here, so the browser lands on the listener.
        location = f"{redirect_uri}?code={code}&state={state}"
        self._send(302, headers={"Location": location})

    def _create_login(self, body: dict[str, Any]) -> None:
        code = str(body.get("code", ""))
        known = self.fake.authorizations.get(code)
        if known is None:
            self._problem(401, "Unauthorized", detail="The sign-in code is not valid.")
            return
        challenge, nonce = known
        verifier = str(body.get("code_verifier", ""))
        computed = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        if computed != challenge or body.get("nonce") != nonce:
            self._problem(401, "Unauthorized", detail="The PKCE verifier or nonce does not match.")
            return
        del self.fake.authorizations[code]
        self.fake.logins.append(body)
        self._send(201, {"id": f"login-{len(self.fake.logins)}", "token": GOOD_TOKEN})
