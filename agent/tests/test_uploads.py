"""Uploads through both ways in: `garden <tool>` and `garden mcp`. The file is read from the
caller's disk and sent as multipart/form-data, and a gated retry sends it again."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from garden.engine import Engine
from garden.mcp import handle
from garden.shared import GardenError
from tests.shared.fake_portal import GOOD_TOKEN, FakePortal
from tests.shared.garden_cli import run_garden, store_token

RECEIPT = b"%PDF-1.4 receipt"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(folder: Path, name: str, data: bytes = RECEIPT) -> Path:
    path = folder / name
    path.write_bytes(data)
    return path


def _approve_pending(portal: FakePortal) -> Callable[[float], None]:
    def sleep(_seconds: float) -> None:
        for confirmation_id, status in list(portal.confirmations.items()):
            if status == "pending":
                portal.decide(confirmation_id, "approved")

    return sleep


def test_cli_uploads_a_file_as_multipart(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    receipt = _write(tmp_path, "receipt.pdf")

    result = run_garden(env, "files", "file", "upload", "--path", str(receipt), "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["sha256"] == _sha(RECEIPT)
    [upload] = portal.uploads
    assert upload.route == "/api/files"
    assert upload.status == 200
    assert upload.field == "file"
    assert upload.filename == "receipt.pdf"
    assert upload.content_type == "application/pdf"
    assert upload.data == RECEIPT


def test_the_cli_reports_a_missing_file_as_json(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)

    result = run_garden(
        env, "files", "file", "upload", "--path", str(tmp_path / "gone.pdf"), "--json"
    )

    assert result.returncode == 1
    error = json.loads(result.stdout)["error"]
    assert error["title"] == "The file does not exist"
    assert "gone.pdf" in error["detail"]
    assert error["fix"]
    assert portal.uploads == []


def test_mcp_lists_the_upload_tools_with_a_path_argument(engine: Engine) -> None:
    reply = handle(engine, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert reply is not None
    tools = {tool["name"]: tool for tool in reply["result"]["tools"]}

    upload = tools["files_file_upload"]
    assert upload["inputSchema"] == {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to a local file to upload."}
        },
        "required": ["path"],
    }
    assert upload["annotations"] == {
        "title": "files.file.upload",
        "readOnlyHint": False,
        "destructiveHint": False,
    }
    batch = tools["knowledge_batch_upload"]["inputSchema"]
    assert set(batch["properties"]) == {"batch_id", "path", "translation_group"}
    assert batch["required"] == ["batch_id", "path"]


def test_mcp_uploads_a_file_and_guesses_no_type_for_an_unknown_extension(
    engine: Engine, portal: FakePortal, tmp_path: Path
) -> None:
    notes = _write(tmp_path, "notes.zzqx", b"plain")

    reply = handle(
        engine,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "files_file_upload", "arguments": {"path": str(notes)}},
        },
    )

    assert reply is not None
    assert reply["result"]["isError"] is False
    assert reply["result"]["structuredContent"]["sha256"] == _sha(b"plain")
    [upload] = portal.uploads
    assert upload.filename == "notes.zzqx"
    assert upload.content_type == "application/octet-stream"
    assert upload.data == b"plain"


def test_knowledge_batch_upload_sends_the_text_fields_with_the_file(
    engine: Engine, portal: FakePortal, tmp_path: Path
) -> None:
    batch_id = str(uuid.uuid4())
    policy = _write(tmp_path, "policy.txt", b"Expenses policy")
    tool = engine.tool("knowledge.batch.upload")
    assert tool is not None

    result = engine.call(
        tool, {"batch_id": batch_id, "path": str(policy), "translation_group": "policies"}
    )

    assert result == {
        "batch_id": batch_id,
        "name": "policy.txt",
        "sha256": _sha(b"Expenses policy"),
    }
    [upload] = portal.uploads
    assert upload.route == f"/api/knowledge/batches/{batch_id}/files"
    assert upload.status == 201
    assert upload.content_type == "text/plain"
    assert upload.fields == {"translation_group": "policies"}


def test_a_missing_file_or_a_folder_is_refused_before_anything_is_sent(
    engine: Engine, portal: FakePortal, tmp_path: Path
) -> None:
    folder = tmp_path / "receipts"
    folder.mkdir()
    tool = engine.tool("files.file.upload")
    assert tool is not None

    with pytest.raises(GardenError) as missing:
        engine.call(tool, {"path": str(tmp_path / "missing.pdf")})
    assert missing.value.title == "The file does not exist"
    assert missing.value.fix

    with pytest.raises(GardenError) as folder_error:
        engine.call(tool, {"path": str(folder)})
    assert folder_error.value.title == "The path is a folder"
    assert folder_error.value.fix
    assert portal.uploads == []


def test_a_gated_upload_sends_the_file_again_on_the_retry(
    portal: FakePortal, make_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The real contract does not gate uploads, so the fake gates this one to test the retry path.
    portal.gate_uploads = True
    engine = make_engine(sleep=_approve_pending(portal))
    receipt = _write(tmp_path, "receipt.pdf")
    tool = engine.tool("files.file.upload")
    assert tool is not None
    messages: list[str] = []

    result = engine.call(tool, {"path": str(receipt)}, notify=messages.append)

    assert result["sha256"] == _sha(RECEIPT)
    assert [upload.status for upload in portal.uploads] == [428, 200]
    assert [upload.data for upload in portal.uploads] == [RECEIPT, RECEIPT]
    assert portal.retries == ["conf-1"]
    assert messages and f"{portal.url}/agent/confirmations/conf-1" in messages[0]
