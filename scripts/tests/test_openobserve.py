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

    variables_by_name = {
        str(variable["name"]): variable
        for variable in cast(
            list[dict[str, object]],
            cast(dict[str, object], evals["variables"])["list"],
        )
    }
    assert {"run", "task", "model"} == set(variables_by_name)
    for var_name, field_name in [("task", "task_id"), ("model", "model")]:
        var = variables_by_name[var_name]
        assert var["type"] == "query_values"
        assert var["multiSelect"] is True
        assert var["selectAllValueForMultiSelect"] == "all"
        query_data = cast(dict[str, object], var["query_data"])
        assert query_data["stream"] == "garden_eval_trials"
        assert query_data["field"] == field_name
    for panel_id in (
        "eval-task-model-grid",
        "eval-grader-verdicts",
        "eval-trial-drilldown",
        "eval-artifacts",
    ):
        drilldowns = cast(
            list[dict[str, object]],
            cast(dict[str, object], by_id[panel_id]["config"])["drilldown"],
        )
        catalog_drill = next(item for item in drilldowns if item["name"] == "Open task in catalog")
        assert catalog_drill["type"] == "byDashboard"
        drill_data = cast(dict[str, object], catalog_drill["data"])
        assert drill_data["dashboard"] == "Garden agent evals"
        assert drill_data["tab"] == "Task catalog"
        assert drill_data["variables"] == [{"name": "task", "value": '${row.field["task_id"]}'}]
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
            assert "NULLIF('{{run:singlequote}}'," in sql
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
        base_url = "http://127.0.0.1:55086"

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


def test_catalog_tab_displays_questions_without_a_telemetry_query() -> None:
    tab = apply_openobserve.task_catalog_tab(
        [
            {
                "id": "example",
                "prompt": "Draft a claim. <script>unsafe</script>",
                "suite": "capability",
                "area": "claims",
                "confirmation": "none",
                "budget_tool_calls": 10,
                "budget_seconds": None,
                "budget_tokens": 1000,
                "graders": [{"id": "draft", "description": "Checks draft state."}],
            }
        ]
    )
    panel = tab["panels"][0]
    assert panel["type"] == "html"
    assert "Draft a claim." in panel["markdownContent"]
    assert "Checks draft state." in panel["markdownContent"]
    assert "<script>" not in panel["markdownContent"]
    assert "Draft a claim." in panel["htmlContent"]
    assert "<script>" not in panel["htmlContent"]
    assert 'data-selected="|{{task:pipe}}|"' in panel["htmlContent"]
    assert 'data-task="example"' in panel["htmlContent"]
    assert panel["queries"] == []


def test_apply_definitions_with_task_catalog_does_not_mutate_alerts() -> None:
    class FakeClient:
        org = "default"
        base_url = "http://127.0.0.1:55086"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object] | None]] = []

        def dashboards(self) -> list[dict[str, object]]:
            return []

        def alerts(self) -> list[dict[str, object]]:
            return []

        def request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
            self.calls.append((method, path, body))
            return {}

    client = FakeClient()
    dashboards = [
        {
            "title": "Garden agent evals",
            "dashboardId": "",
            "tabs": [{"tabId": "default", "name": "Evals", "panels": []}],
        }
    ]
    alerts = [{"name": "garden_regression_task_failed", "destinations": []}]
    catalog = [
        {
            "id": "example",
            "prompt": "Draft a claim.",
            "suite": "capability",
            "area": "claims",
            "confirmation": "none",
            "budget_tool_calls": 10,
            "budget_seconds": None,
            "budget_tokens": 1000,
            "graders": [{"id": "draft", "description": "Checks draft state."}],
        }
    ]
    assert apply_openobserve.apply_definitions(client, dashboards, alerts, "dest", catalog) == (
        1,
        1,
    )
    assert len(cast(list[object], client.calls[0][2]["tabs"])) == 2
    assert "tabs" not in cast(dict[str, object], client.calls[1][2])


def test_schema_initialization_creates_streams_and_only_adds_missing_fields() -> None:
    class SchemaClient:
        org = "default"

        def __init__(self, exists: bool) -> None:
            self.exists = exists
            self.writes: list[tuple[str, str, dict[str, object]]] = []

        def request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
            if method == "GET":
                if "/schema" in path:
                    return {"schema": [{"name": "_timestamp", "type": "Int64"}]}
                return {
                    "list": [{"name": "garden_eval_traces"}, {"name": "garden_eval_trials"}]
                    if self.exists
                    else []
                }
            assert body is not None
            self.writes.append((method, path, body))
            return {}

    for exists in [False, True]:
        client = SchemaClient(exists)
        apply_openobserve.ensure_stream_schemas(client)
        assert len(client.writes) == 2
        for method, path, body in client.writes:
            assert method == ("PUT" if exists else "POST")
            assert "type=" in path
            assert body["fields"]
            if exists:
                assert all(field["name"] != "_timestamp" for field in body["fields"])
