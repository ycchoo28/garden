from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import cast
from urllib.request import Request

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "apply_openobserve.py"
_SPEC = importlib.util.spec_from_file_location("garden_apply_openobserve", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
apply_openobserve = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(apply_openobserve)


def test_checked_in_definitions_cover_eval_and_portal_workflows() -> None:
    dashboards, alerts = apply_openobserve.load_definitions()

    by_title = {str(definition["title"]): definition for definition in dashboards}
    evals = by_title["Garden agent evals"]
    panels = cast(
        list[dict[str, object]],
        cast(dict[str, object], cast(list[object], evals["tabs"])[0])["panels"],
    )
    by_id = {str(panel["id"]): panel for panel in panels}

    assert {"run", "task", "model"} == {
        str(variable["name"])
        for variable in cast(
            list[dict[str, object]],
            cast(dict[str, object], evals["variables"])["list"],
        )
    }
    assert "heatmap_hide_below" in cast(dict[str, object], by_id["eval-pass-rate"]["config"])
    assert cast(dict[str, object], by_id["eval-pass-rate"]["config"])["heatmap_hide_below"] == 0
    assert "ORDER BY model ASC, task_id ASC" in str(
        cast(dict[str, object], cast(list[object], by_id["eval-pass-rate"]["queries"])[0])["query"]
    )
    assert "ORDER BY model ASC, task_id ASC" in str(
        cast(dict[str, object], cast(list[object], by_id["eval-pass-k"]["queries"])[0])["query"]
    )
    pass_rate_query = str(
        cast(dict[str, object], cast(list[object], by_id["eval-pass-rate"]["queries"])[0])["query"]
    )
    assert "SUM(CASE WHEN passed THEN 1 ELSE 0 END)" in pass_rate_query
    pass_k_query = str(
        cast(dict[str, object], cast(list[object], by_id["eval-pass-k"]["queries"])[0])["query"]
    )
    assert "requested_repeats" in pass_k_query
    grid_query = str(
        cast(dict[str, object], cast(list[object], by_id["eval-task-model-grid"]["queries"])[0])[
            "query"
        ]
    )
    assert "THEN 'PASS' ELSE 'FAIL'" in grid_query
    for panel in panels:
        for query in cast(list[dict[str, object]], panel["queries"]):
            sql = str(query["query"])
            fields = cast(dict[str, object], query["fields"])
            assert fields["stream"] == "garden_eval_trials"
            assert "{{run:singlequote}}" in sql
            assert "{{task:singlequote}}" in sql
            assert "{{model:singlequote}}" in sql
            assert "model <> 'reference'" in sql
    trial_query = str(
        cast(dict[str, object], cast(list[object], by_id["eval-trial-drilldown"]["queries"])[0])[
            "query"
        ]
    )
    assert "trace_id" in trial_query
    assert "trace_link" in trial_query
    assert "stream=garden_eval_traces" in trial_query
    assert "tool_calls" in trial_query
    assert "graders" in trial_query
    assert "Garden agent monitoring" in by_title
    assert len(alerts) == 1
    assert alerts[0]["name"] == "garden_regression_task_failed"
    assert alerts[0]["stream_name"] == "garden_eval_trials"
    alert_query = cast(dict[str, object], alerts[0]["query_condition"])["sql"]
    assert "suite = 'regression'" in str(alert_query)
    assert "model <> 'reference'" in str(alert_query)
    assert "GROUP BY task_id" in str(alert_query)
    assert alerts[0]["destinations"] == []


def test_apply_creates_missing_definitions_and_updates_existing_ones(tmp_path: Path) -> None:
    dashboards_dir = tmp_path / "dashboards"
    alerts_dir = tmp_path / "alerts"
    dashboards_dir.mkdir()
    alerts_dir.mkdir()
    (dashboards_dir / "example.json").write_text(
        json.dumps({"title": "Garden evals", "dashboardId": ""}), encoding="utf-8"
    )
    (alerts_dir / "example.json").write_text(
        json.dumps({"name": "Regression failed"}), encoding="utf-8"
    )
    dashboards, alerts = apply_openobserve.load_definitions(tmp_path)

    class FakeClient:
        org = "default"

        def __init__(self, *, existing: bool) -> None:
            self.existing = existing
            self.calls: list[tuple[str, str, dict[str, object] | None]] = []

        def dashboards(self) -> list[dict[str, object]]:
            if self.existing:
                return [
                    {"dashboard_id": "dashboard-id", "title": "Garden evals", "hash": "old-hash"}
                ]
            return []

        def alerts(self) -> list[dict[str, object]]:
            if self.existing:
                return [{"id": "alert-id", "name": "Regression failed"}]
            return []

        def request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
            self.calls.append((method, path, body))
            return {}

    client = FakeClient(existing=False)
    with pytest.raises(RuntimeError, match="GARDEN_OPENOBSERVE_ALERT_DESTINATION"):
        apply_openobserve.apply_definitions(client, dashboards, alerts)
    assert [(method, path) for method, path, _body in client.calls] == [
        ("POST", "/api/default/dashboards")
    ]

    client = FakeClient(existing=False)
    assert apply_openobserve.apply_definitions(client, dashboards, alerts, "garden-alerts") == (
        1,
        1,
    )
    assert [(method, path) for method, path, _body in client.calls] == [
        ("POST", "/api/default/dashboards"),
        ("POST", "/api/v2/default/alerts"),
    ]
    assert cast(dict[str, object], client.calls[1][2])["destinations"] == ["garden-alerts"]

    existing_client = FakeClient(existing=True)
    assert apply_openobserve.apply_definitions(
        existing_client, dashboards, alerts, "garden-alerts"
    ) == (1, 1)
    assert [(method, path) for method, path, _body in existing_client.calls] == [
        ("PUT", "/api/default/dashboards/dashboard-id?hash=old-hash"),
        ("PUT", "/api/v2/default/alerts/alert-id"),
    ]
    assert cast(dict[str, object], existing_client.calls[0][2])["dashboardId"] == "dashboard-id"


def test_client_sends_json_with_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Request] = []

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok":true}'

    def fake_urlopen(request: Request, *, timeout: int) -> FakeResponse:
        assert timeout == apply_openobserve.TIMEOUT_SECONDS
        captured.append(request)
        return FakeResponse()

    monkeypatch.setattr(apply_openobserve, "urlopen", fake_urlopen)
    client = apply_openobserve.OpenObserveClient(
        "http://127.0.0.1:55086/", "default", "root@example.com", "password"
    )
    assert client.request("POST", "/api/default/dashboards", {"title": "Example"}) == {"ok": True}

    request = captured[0]
    assert request.full_url == "http://127.0.0.1:55086/api/default/dashboards"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Basic cm9vdEBleGFtcGxlLmNvbTpwYXNzd29yZA=="
    assert json.loads(request.data) == {"title": "Example"}
