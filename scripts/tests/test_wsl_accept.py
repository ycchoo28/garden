"""Unit tests for scripts/oidc_approve.py and scripts/wsl_accept.py."""

from __future__ import annotations

import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import oidc_approve  # noqa: E402
import wsl_accept  # noqa: E402


class _FakeOidcHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.startswith("/api/agent/cli/authorize?"):
            self.send_response(302)
            self.send_header("Location", "/oidc/login")
            self.end_headers()
            return
        if self.path == "/oidc/login":
            body = (
                b"<html><body>"
                b'<form method="post" action="/oidc/submit">'
                b'<input type="hidden" name="username" value="dev.admin@fernhill.example">'
                b'<input type="hidden" name="state" value="abc123">'
                b"</form>"
                b'<form method="post" action="/oidc/submit">'
                b'<input type="hidden" name="username" value="marcus.lee@fernhill.example">'
                b'<input type="hidden" name="state" value="abc123">'
                b"</form>"
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/callback":
            body = b"<html><body>Sign-in succeeded. You may close this tab.</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        fields = parse_qs(raw)
        if self.path == "/oidc/submit" and fields.get("username") == ["dev.admin@fernhill.example"]:
            self.send_response(302)
            self.send_header("Location", "/callback")
            self.end_headers()
            return
        self.send_response(400)
        self.end_headers()


@pytest.fixture()
def fake_oidc_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeOidcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_resolve_persona_email() -> None:
    assert oidc_approve.resolve_persona_email("Dev Admin") == "dev.admin@fernhill.example"
    assert oidc_approve.resolve_persona_email("custom@example.com") == "custom@example.com"
    with pytest.raises(ValueError, match="Unknown persona"):
        oidc_approve.resolve_persona_email("Nobody")


def test_validate_authorize_url_rejects_non_loopback_and_wrong_path() -> None:
    validate = oidc_approve.validate_authorize_url
    assert validate("https://127.0.0.1:55000/api/agent/cli/authorize?x=1") is not None
    assert validate("http://example.com:55000/api/agent/cli/authorize?x=1") is not None
    assert validate("http://127.0.0.1:55000/wrong?x=1") is not None
    assert validate("http://127.0.0.1:55000/api/agent/cli/authorize") is not None
    assert validate("http://127.0.0.1:55000/api/agent/cli/authorize?state=1") is None


def test_approve_login_url_completes_fake_oidc_flow(fake_oidc_server: str) -> None:
    url = f"{fake_oidc_server}/api/agent/cli/authorize?state=abc123"
    result = oidc_approve.approve_login_url(url, persona="Dev Admin")
    assert result.ok is True
    assert result.persona_email == "dev.admin@fernhill.example"
    assert result.callback_url == f"{fake_oidc_server}/callback"


def test_approve_login_url_fails_when_persona_not_offered(fake_oidc_server: str) -> None:
    url = f"{fake_oidc_server}/api/agent/cli/authorize?state=abc123"
    result = oidc_approve.approve_login_url(url, persona="Priya Nair")
    assert result.ok is False
    assert "did not offer persona" in result.message


def test_wait_for_login_url_watches_glob(tmp_path: Path) -> None:
    start = time.time() - 1.0
    sub = tmp_path / "garden-demo-123"
    sub.mkdir()
    link_file = sub / "login-link"

    def _writer() -> None:
        time.sleep(0.1)
        url = "http://127.0.0.1:55000/api/agent/cli/authorize?state=ok\n"
        link_file.write_text(url, encoding="utf-8")

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    found = oidc_approve.wait_for_login_url(
        watch_glob=str(tmp_path / "garden-demo-*" / "login-link"),
        created_after=start,
        timeout_seconds=2.0,
        poll_interval=0.05,
    )
    t.join()
    assert found == "http://127.0.0.1:55000/api/agent/cli/authorize?state=ok"


def test_preflight_passes_when_all_checks_ok(tmp_path: Path) -> None:
    report = wsl_accept.run_preflight(
        repo_root=tmp_path,
        min_free_ram_mb=512,
        min_free_disk_mb=1,
        ports=(55000, 55173),
        check_docker_fn=lambda: wsl_accept.CheckResult(ok=True, detail="Docker ok"),
        check_containers_fn=lambda: wsl_accept.CheckResult(ok=True, detail="No containers"),
        check_port_fn=lambda _p: True,
        read_ram_fn=lambda: 4096,
    )
    assert report.ok is True
    assert report.to_dict()["status"] == "ok"


def test_preflight_fails_fast_on_low_ram_or_busy_port(tmp_path: Path) -> None:
    report = wsl_accept.run_preflight(
        repo_root=tmp_path,
        min_free_ram_mb=2048,
        min_free_disk_mb=1,
        ports=(55000, 55173),
        check_docker_fn=lambda: wsl_accept.CheckResult(ok=True, detail="Docker ok"),
        check_containers_fn=lambda: wsl_accept.CheckResult(ok=True, detail="No containers"),
        check_port_fn=lambda port: port != 55000,
        read_ram_fn=lambda: 1024,
    )
    assert report.ok is False
    assert report.to_dict()["status"] == "infra_error"
    assert report.checks["ram"].ok is False
    assert report.checks["ports"].ok is False
    assert report.checks["ports"].value == [55000]


def test_build_isolated_env_separates_home_and_shares_caches(tmp_path: Path) -> None:
    orig_home = tmp_path / "orig-home"
    tools_dir = orig_home / ".local" / "share" / "garden-tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "paths").write_text("/opt/garden-just/bin\n", encoding="utf-8")

    iso_home = tmp_path / "pass1" / "home"
    cache_dir = tmp_path / "shared-cache"
    env = wsl_accept.build_isolated_env(
        base_env={"HOME": str(orig_home), "PATH": "/usr/bin:/bin"},
        isolated_home=iso_home,
        cache_dir=cache_dir,
    )
    assert env["HOME"] == str(iso_home)
    assert env["GARDEN_HOME"] == str(iso_home / ".garden")
    assert env["GARDEN_DSH_HOME"] == str(iso_home / ".dsh")
    assert env["GARDEN_BIN_DIR"] == str(iso_home / ".local" / "bin")
    assert env["UV_CACHE_DIR"] == str(cache_dir / "uv")
    assert env["npm_config_cache"] == str(cache_dir / "npm")
    assert env["BUN_INSTALL_CACHE_DIR"] == str(cache_dir / "bun")
    assert env["PATH"].startswith(str(iso_home / ".local" / "bin"))
    assert str(orig_home / ".local" / "bin") in env["PATH"]
    assert "/opt/garden-just/bin" in env["PATH"]


def test_resolve_writable_cache_dir_falls_back_when_preferred_unwritable(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked-file"
    blocked.write_text("not a dir", encoding="utf-8")
    fallback = tmp_path / "fallback-cache"
    chosen = wsl_accept.resolve_writable_cache_dir(blocked / "sub", fallback=fallback)
    assert chosen == fallback
    assert fallback.is_dir()


def test_classify_failure_distinguishes_infra_from_product() -> None:
    assert wsl_accept.classify_failure(137, "Killed") == "infra"
    assert wsl_accept.classify_failure(-9, "") == "infra"
    assert wsl_accept.classify_failure(1, "Cannot connect to the Docker daemon") == "infra"
    assert wsl_accept.classify_failure(1, "No space left on device") == "infra"
    assert wsl_accept.classify_failure(1, "Installer failed before sign-in") == "product"
