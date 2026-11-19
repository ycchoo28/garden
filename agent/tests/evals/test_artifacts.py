"""Tests for capturing generated eval workspace files and Garden claims in traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from garden.trace_contract import GARDEN_EVAL_ARTIFACT_COUNT, GARDEN_EVAL_ARTIFACTS
from tests.evals import artifacts
from tests.evals.telemetry import EvalTelemetry


def test_workspace_artifact_collection_finds_new_and_modified_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "receipt.pdf").write_bytes(b"%PDF-1.4\nseed\n")
    before = artifacts.snapshot_workspace(workspace)

    (workspace / "draft-summary.md").write_text("# Draft claim\nAmount: $42.30\n", encoding="utf-8")
    (workspace / "output.bin").write_bytes(b"\xff\xfe\x00\x01")

    collected = artifacts.collect_workspace_artifacts(workspace, before)

    by_path = {str(item["path"]): item for item in collected}
    assert set(by_path) == {"draft-summary.md", "output.bin"}
    assert by_path["draft-summary.md"]["kind"] == "workspace_file"
    assert by_path["draft-summary.md"]["content"] == "# Draft claim\nAmount: $42.30\n"
    assert by_path["output.bin"]["binary"] is True


def test_claim_artifact_collection_fetches_created_draft_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_api_json(_base_url: str, path: str, _token: str, *, side_call: str = "") -> object:
        assert side_call == "artifacts"
        if path == "/api/claims":
            return [
                {"id": "claim-existing", "title": "Old", "status": "draft", "version": 1},
                {"id": "claim-new", "title": "Dinner", "status": "draft", "version": 2},
            ]
        if path == "/api/claims/claim-new":
            return {
                "claim": {"id": "claim-new", "title": "Dinner", "status": "draft", "version": 2},
                "lines": [{"category": "meals", "amount": "42.30"}],
            }
        raise AssertionError(path)

    monkeypatch.setattr(artifacts, "api_json", fake_api_json)
    collected = artifacts.collect_claim_artifacts(
        "http://127.0.0.1:1", "tok", before={"claim-existing": 1}
    )

    assert len(collected) == 1
    assert collected[0]["kind"] == "claim"
    assert collected[0]["title"] == "Dinner"
    assert collected[0]["status"] == "draft"
    assert collected[0]["lines"] == [{"category": "meals", "amount": "42.30"}]


def test_eval_telemetry_records_artifacts_on_trial_root_and_child_span() -> None:
    telemetry = EvalTelemetry({})
    exporter = InMemorySpanExporter()
    telemetry._tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))  # pyright: ignore[reportPrivateUsage]

    sample: list[dict[str, object]] = [
        {"kind": "claim", "id": "c1", "title": "Draft", "status": "draft", "lines": []}
    ]
    with telemetry.trial_span(
        run_id="run-1",
        trial_id="draft-one-receipt/m/1",
        task_id="draft-one-receipt",
        suite="capability",
        area="claims",
        model="m",
        repeat=1,
    ) as span:
        telemetry.record_artifacts(span, sample, run_id="run-1", trial_id="draft-one-receipt/m/1")

    spans = exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    root = by_name["draft-one-receipt / m / 1"]
    child = by_name["eval.artifacts"]
    assert root.attributes is not None
    assert child.attributes is not None
    assert root.attributes[GARDEN_EVAL_ARTIFACT_COUNT] == 1
    assert json.loads(str(root.attributes[GARDEN_EVAL_ARTIFACTS])) == sample
    root_ctx = root.get_span_context()
    assert child.parent is not None and root_ctx is not None
    assert child.parent.span_id == root_ctx.span_id
    assert json.loads(str(child.attributes[GARDEN_EVAL_ARTIFACTS])) == sample
    assert cast(list[object], json.loads(str(child.attributes[GARDEN_EVAL_ARTIFACTS]))) == sample
    telemetry.flush()


def test_workspace_capture_ignores_symlinks_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("private data")
    (workspace / "linked.txt").symlink_to(private)
    assert artifacts.snapshot_workspace(workspace) == {}
    assert artifacts.collect_workspace_artifacts(workspace, {}) == []


def test_workspace_capture_ignores_credential_files(tmp_path: Path) -> None:
    for name in [".env", ".env.local", "secrets.json", "cliproxy.env", "openobserve.env"]:
        (tmp_path / name).write_text("credential")
    assert artifacts.snapshot_workspace(tmp_path) == {}
    assert artifacts.collect_workspace_artifacts(tmp_path, {}) == []
