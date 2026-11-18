"""Capture workspace files and Garden claim artifacts created during an eval trial."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from tests.evals.grading import api_json

_IGNORED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv"})
_IGNORED_FILES = frozenset({"secrets.json", ".env", "cliproxy.env", "openobserve.env"})
_MAX_FILES = 20
_MAX_TEXT_BYTES = 16_384
_MAX_ARTIFACTS_JSON_BYTES = 65_536


def _as_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_workspace(workspace: Path) -> dict[str, str]:
    """Return relative file paths mapped to their SHA-256 digests before a trial runs."""
    snapshot: dict[str, str] = {}
    if not workspace.is_dir():
        return snapshot
    for path in sorted(workspace.rglob("*")):
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(workspace.resolve())
        ):
            continue
        rel = path.relative_to(workspace)
        if path.name in _IGNORED_FILES or path.name.startswith(".env."):
            continue
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        snapshot[rel.as_posix()] = _sha256_file(path)
    return snapshot


def collect_workspace_artifacts(
    workspace: Path, before: Mapping[str, str]
) -> list[dict[str, object]]:
    """Collect workspace files created or modified during a trial."""
    artifacts: list[dict[str, object]] = []
    if not workspace.is_dir():
        return artifacts
    for path in sorted(workspace.rglob("*")):
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(workspace.resolve())
        ):
            continue
        rel = path.relative_to(workspace)
        if path.name in _IGNORED_FILES or path.name.startswith(".env."):
            continue
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        rel_posix = rel.as_posix()
        digest = _sha256_file(path)
        if before.get(rel_posix) == digest:
            continue
        with path.open("rb") as handle:
            raw = handle.read(_MAX_TEXT_BYTES + 1)
        size_bytes = path.stat().st_size
        entry: dict[str, object] = {
            "kind": "workspace_file",
            "path": rel_posix,
            "size_bytes": size_bytes,
            "sha256": digest,
        }
        try:
            text = raw[:_MAX_TEXT_BYTES].decode("utf-8")
            if "\x00" not in text:
                entry["content"] = text
                if size_bytes > _MAX_TEXT_BYTES:
                    entry["truncated"] = True
        except UnicodeDecodeError:
            entry["binary"] = True
        artifacts.append(entry)
        if len(artifacts) >= _MAX_FILES:
            break
    return artifacts


def snapshot_claims(api_url: str, token: str) -> dict[str, object]:
    """Snapshot visible claim versions before a trial starts."""
    try:
        raw = api_json(api_url, "/api/claims", token, side_call="artifacts")
    except Exception:
        return {}
    if not isinstance(raw, list):
        return {}
    versions: dict[str, object] = {}
    for item in cast(list[object], raw):
        summary = _as_object(item)
        if summary is None:
            continue
        claim_id = summary.get("id")
        if isinstance(claim_id, str):
            versions[claim_id] = summary.get("version")
    return versions


def collect_claim_artifacts(
    api_url: str, token: str, before: Mapping[str, object]
) -> list[dict[str, object]]:
    """Collect full claim details for claims created or modified during a trial."""
    try:
        raw = api_json(api_url, "/api/claims", token, side_call="artifacts")
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    artifacts: list[dict[str, object]] = []
    for item in cast(list[object], raw):
        summary = _as_object(item)
        if summary is None:
            continue
        claim_id = summary.get("id")
        if not isinstance(claim_id, str):
            continue
        if claim_id in before and before[claim_id] == summary.get("version"):
            continue
        try:
            detail_raw = api_json(api_url, f"/api/claims/{claim_id}", token, side_call="artifacts")
        except Exception:
            continue
        detail = _as_object(detail_raw)
        if detail is None:
            continue
        claim = _as_object(detail.get("claim"))
        artifacts.append(
            {
                "kind": "claim",
                "id": claim_id,
                "title": claim.get("title") if claim is not None else summary.get("title"),
                "status": claim.get("status") if claim is not None else summary.get("status"),
                "claim": claim if claim is not None else summary,
                "lines": detail.get("lines", []),
            }
        )
    return artifacts


def collect_trial_artifacts(
    *,
    workspace: Path,
    before_workspace: Mapping[str, str],
    api_url: str,
    token: str,
    before_claims: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return both generated workspace files and generated/updated Garden claims."""
    return [
        *collect_claim_artifacts(api_url, token, before_claims),
        *collect_workspace_artifacts(workspace, before_workspace),
    ]


def bounded_artifacts_json(artifacts: list[dict[str, object]]) -> str:
    """Serialize artifacts within a bounded span attribute size."""
    encoded = json.dumps(artifacts, ensure_ascii=False, default=str)
    if len(encoded) <= _MAX_ARTIFACTS_JSON_BYTES:
        return encoded
    trimmed = [
        {k: v for k, v in item.items() if k != "content"}
        if item.get("kind") == "workspace_file"
        else item
        for item in artifacts
    ]
    encoded = json.dumps(trimmed, ensure_ascii=False, default=str)
    if len(encoded) <= _MAX_ARTIFACTS_JSON_BYTES:
        return encoded
    return json.dumps(
        {
            "truncated": True,
            "count": len(artifacts),
            "preview": encoded[: _MAX_ARTIFACTS_JSON_BYTES - 128],
        },
        ensure_ascii=False,
    )
