"""Apply the checked-in Garden dashboards and alert to an OpenObserve instance."""

from __future__ import annotations

import argparse
import base64
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = ROOT / "observability" / "openobserve"
DEFAULT_URL = "http://127.0.0.1:55086"
DEFAULT_USERNAME = "root@example.com"
TIMEOUT_SECONDS = 30


class OpenObserveClient:
    def __init__(self, base_url: str, org: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.org = org
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self.authorization = f"Basic {token}"

    def request(self, method: str, path: str, body: Mapping[str, object] | None = None) -> object:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": self.authorization, "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                content = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"OpenObserve {method} {path} returned HTTP {error.code}: {detail}"
            ) from error
        except URLError as error:
            raise RuntimeError(
                f"Could not reach OpenObserve at {self.base_url}: {error.reason}"
            ) from error
        if not content:
            return {}
        return json.loads(content)

    def dashboards(self) -> list[dict[str, object]]:
        response = self.request("GET", f"/api/{self.org}/dashboards")
        return _items(response, "dashboards")

    def alerts(self) -> list[dict[str, object]]:
        response = self.request("GET", f"/api/v2/{self.org}/alerts")
        return _items(response, "list")


def _items(response: object, key: str) -> list[dict[str, object]]:
    if not isinstance(response, dict):
        raise RuntimeError(f"OpenObserve response did not contain a {key} list.")
    values = cast(dict[str, object], response).get(key)
    if not isinstance(values, list):
        raise RuntimeError(f"OpenObserve response did not contain a {key} list.")
    return [cast(dict[str, object], item) for item in values if isinstance(item, dict)]


def load_definitions(
    root: Path = DEFINITIONS,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    dashboards = _load_directory(root / "dashboards")
    alerts = _load_directory(root / "alerts")
    if not dashboards:
        raise RuntimeError(f"No dashboard definitions found in {root / 'dashboards'}.")
    return dashboards, alerts


def _load_directory(directory: Path) -> list[dict[str, object]]:
    definitions: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"Definition must be a JSON object: {path}")
        definitions.append(cast(dict[str, object], value))
    return definitions


def _find_by_name(
    values: Sequence[dict[str, object]], name: str, id_keys: tuple[str, ...]
) -> str | None:
    matches = [value for value in values if value.get("title", value.get("name")) == name]
    if not matches:
        return None
    for key in id_keys:
        value = matches[0].get(key)
        if isinstance(value, str) and value:
            return value
    raise RuntimeError(f"OpenObserve returned {name!r} without an id.")


def apply_definitions(
    client: OpenObserveClient,
    dashboards: Sequence[dict[str, object]],
    alerts: Sequence[dict[str, object]],
    alert_destination: str | None = None,
) -> tuple[int, int]:
    if any(not definition.get("destinations") for definition in alerts) and not alert_destination:
        raise RuntimeError(
            "Set GARDEN_OPENOBSERVE_ALERT_DESTINATION to an existing alert destination."
        )

    existing_dashboards = client.dashboards()
    for definition in dashboards:
        title = definition.get("title")
        if not isinstance(title, str):
            raise RuntimeError("Each dashboard definition must have a title.")
        dashboard_id = _find_by_name(
            existing_dashboards, title, ("dashboard_id", "dashboardId", "id")
        )
        path = f"/api/{client.org}/dashboards"
        method = "POST"
        body = dict(definition)
        if dashboard_id is not None:
            method = "PUT"
            path += f"/{dashboard_id}"
            body["dashboardId"] = dashboard_id
        client.request(method, path, body)

    existing_alerts = client.alerts() if alerts else []
    for definition in alerts:
        name = definition.get("name")
        if not isinstance(name, str):
            raise RuntimeError("Each alert definition must have a name.")
        alert_id = _find_by_name(existing_alerts, name, ("alert_id", "id"))
        path = f"/api/v2/{client.org}/alerts"
        method = "POST"
        body = dict(definition)
        if alert_destination:
            body["destinations"] = [alert_destination]
        if alert_id is not None:
            method = "PUT"
            path += f"/{alert_id}"
        client.request(method, path, body)
    return len(dashboards), len(alerts)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("GARDEN_OPENOBSERVE_URL", DEFAULT_URL),
        help="OpenObserve base URL",
    )
    parser.add_argument("--org", default=os.environ.get("GARDEN_OPENOBSERVE_ORG", "default"))
    parser.add_argument(
        "--username",
        default=os.environ.get("GARDEN_OPENOBSERVE_USERNAME", DEFAULT_USERNAME),
    )
    parser.add_argument(
        "--alert-destination",
        default=os.environ.get("GARDEN_OPENOBSERVE_ALERT_DESTINATION"),
        help="Name of an existing OpenObserve alert destination",
    )
    parser.add_argument("--password", default=os.environ.get("GARDEN_OPENOBSERVE_PASSWORD"))
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if not args.password:
        raise SystemExit(
            "Set GARDEN_OPENOBSERVE_PASSWORD or pass --password to apply the dashboards."
        )
    client = OpenObserveClient(args.url, args.org, args.username, args.password)
    dashboards, alerts = load_definitions()
    dashboard_count, alert_count = apply_definitions(
        client, dashboards, alerts, args.alert_destination
    )
    print(f"Applied {dashboard_count} dashboards and {alert_count} alerts to {args.url}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
