"""`garden login`, `whoami` and `logout` as a person runs them, with the browser step played."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import as_json, finish_login, open_sign_in, run_garden, sign_in, store_token
from fake_portal import GOOD_TOKEN, FakePortal


def test_login_then_whoami_then_logout(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    signed = sign_in(env, device="laptop-1")
    assert signed.returncode == 0, signed.stderr
    assert "Signed in as Marcus Lee (marcus.lee@fernhill.example) on laptop-1." in signed.stdout

    stored = json.loads(json.loads((tmp_path / "secrets.json").read_text())[portal.url])
    assert stored == {"token": GOOD_TOKEN, "device": "laptop-1"}

    who = run_garden(env, "whoami", "--json")
    assert who.returncode == 0, who.stderr
    record = as_json(who.stdout)
    assert record["name"] == "Marcus Lee"
    assert record["device"] == "laptop-1"
    assert "files.view" in record["permissions"]

    out = run_garden(env, "logout", "--json")
    assert out.returncode == 0, out.stderr
    assert as_json(out.stdout) == {"signed_out": True, "device": "laptop-1"}
    # The portal ends the login, so the token stops working everywhere, and this device says so.
    assert portal.logouts == [GOOD_TOKEN]
    assert "did not confirm" not in out.stderr

    after = run_garden(env, "whoami", "--json")
    assert after.returncode == 1
    error = as_json(after.stdout)["error"]
    assert error["title"] == "Not signed in"
    assert "garden login" in error["fix"]


def test_a_wrong_state_is_refused_and_the_real_redirect_still_works(
    portal: FakePortal, env: dict[str, str]
) -> None:
    process, head, url = open_sign_in(env, "laptop-2", "--no-open")
    # A redirect that carries a different state must not be accepted, and gets a clear failure page.
    forged = url.replace("state=", "state=forged-")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(forged, timeout=30).read()
    assert refused.value.code == 400
    refusal = refused.value.read().decode("utf-8")
    assert "Sign-in did not complete" in refusal
    assert "This sign-in link is not valid. Run garden login again." in refusal
    # The real link, from the first line the CLI printed, still completes the login.
    landed = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    assert "Sign-in succeeded" in landed
    assert "Garden is signed in on laptop-2." in landed
    assert "You can close this tab and return to the terminal." in landed
    result = finish_login(process, head)
    assert result.returncode == 0, result.stderr
    # The success is one line: who signed in, and on which device.
    success = result.stdout.splitlines()[-1]
    assert success.startswith("Signed in as Marcus Lee (")
    assert success.endswith(") on laptop-2.")
    assert sum(1 for line in result.stdout.splitlines() if line.startswith("Signed in")) == 1


def test_the_callback_page_is_self_contained_and_styled(
    portal: FakePortal, env: dict[str, str]
) -> None:
    process, head, url = open_sign_in(env, "laptop-5", "--no-open")
    page = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    finish_login(process, head)

    assert page.startswith("<!doctype html>")
    assert '<meta name="viewport"' in page
    assert "<style>" in page
    # Nothing is fetched from outside: no stylesheet, script, image or font URL.
    assert "<link" not in page
    assert "<script" not in page
    assert "src=" not in page
    assert "http://" not in page and "https://" not in page
    assert "@import" not in page and "url(" not in page
    assert "prefers-color-scheme: dark" in page


def test_no_portal_address_is_reported_as_a_fix(tmp_path: Path, env: dict[str, str]) -> None:
    bare = {key: value for key, value in env.items() if key != "GARDEN_API_URL"}
    bare["GARDEN_HOME"] = str(tmp_path / "empty-home")
    result = run_garden(bare, "whoami", "--json")
    assert result.returncode == 1
    error = as_json(result.stdout)["error"]
    assert error["title"] == "No portal address"
    assert "GARDEN_API_URL" in error["fix"]


def test_switching_portals_does_not_reuse_another_portals_login(
    portal: FakePortal, env: dict[str, str]
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    other = FakePortal()
    other.start()
    try:
        result = run_garden({**env, "GARDEN_API_URL": other.url}, "whoami", "--json")
        assert result.returncode == 1
        assert as_json(result.stdout)["error"]["title"] == "Not signed in"
    finally:
        other.stop()


def test_logout_keeps_other_portals_logins(portal: FakePortal, env: dict[str, str]) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    other = FakePortal()
    other.start()
    try:
        store_token(env, other.url, GOOD_TOKEN)
        result = run_garden(env, "logout", "--json")
        assert result.returncode == 0, result.stderr
        assert portal.logouts == [GOOD_TOKEN]

        who = run_garden({**env, "GARDEN_API_URL": other.url}, "whoami", "--json")
        assert who.returncode == 0, who.stderr
        assert as_json(who.stdout)["name"] == other.name
        assert other.logouts == []
    finally:
        other.stop()
