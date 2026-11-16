from __future__ import annotations

import os
import subprocess
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import finish_login, open_sign_in
from fake_portal import FakePortal

from garden.auth import open_link
from garden.shared import browser as browser_module

# The line gio printed on the captain's terminal when no handler was set up.
GIO_NOISE = "gio: https://portal.example.test/cli/login?port=34053: Operation not supported"
LINK = "https://portal.example.test/cli/login?port=34053&challenge=abc&device=laptop&state=xyz"


def _noisy(result: bool) -> Callable[[str], bool]:
    """An opener that writes what gio writes, and reports whether it opened the link."""

    def opener(url: str) -> bool:
        os.write(2, (GIO_NOISE + "\n").encode())
        os.write(1, b"opener chatter\n")
        return result

    return opener


def test_the_opener_output_never_reaches_the_terminal(capfd: pytest.CaptureFixture[str]) -> None:
    assert open_link(LINK, _noisy(True)) is True
    captured = capfd.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_no_browser_is_reported_as_false_and_the_noise_is_still_silenced(
    capfd: pytest.CaptureFixture[str],
) -> None:
    assert open_link(LINK, _noisy(False)) is False
    captured = capfd.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_an_opener_that_raises_a_browser_error_is_a_no_browser(
    capfd: pytest.CaptureFixture[str],
) -> None:
    def broken(url: str) -> bool:
        os.write(2, b"Error: no handler\n")
        raise webbrowser.Error("no handler")

    assert open_link(LINK, broken) is False
    assert capfd.readouterr().err == ""


def test_the_terminal_gets_its_output_back_after_the_opener_runs(
    capfd: pytest.CaptureFixture[str],
) -> None:
    open_link(LINK, _noisy(True))
    print("printed after the opener", flush=True)
    os.write(2, b"stderr after the opener\n")
    captured = capfd.readouterr()
    assert "printed after the opener" in captured.out
    assert "stderr after the opener" in captured.err


def test_an_opener_that_crashes_still_gives_the_terminal_back(
    capfd: pytest.CaptureFixture[str],
) -> None:
    def crashing(url: str) -> bool:
        raise RuntimeError("opener crashed")

    with pytest.raises(RuntimeError):
        open_link(LINK, crashing)
    os.write(2, b"still here\n")
    assert "still here" in capfd.readouterr().err


def test_wsl_is_detected_from_environment_or_kernel_release() -> None:
    assert browser_module.is_wsl({"WSL_DISTRO_NAME": "Ubuntu"}, "generic")
    assert browser_module.is_wsl({"WSL_INTEROP": "/run/WSL/42_interop"}, "generic")
    assert browser_module.is_wsl({}, "5.15.90.1-microsoft-standard-WSL2")
    assert not browser_module.is_wsl({}, "6.8.0-generic")


def test_wsl_prefers_wslview_and_passes_the_url_as_one_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setattr(browser_module, "is_wsl", lambda: True)
    available = {
        "wslview": "/usr/bin/wslview",
        "explorer.exe": "/mnt/c/Windows/explorer.exe",
    }

    def which(name: str) -> str | None:
        return available.get(name)

    monkeypatch.setattr(browser_module.shutil, "which", which)
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(browser_module.subprocess, "run", run)

    assert open_link(LINK) is True
    assert calls == [["/usr/bin/wslview", LINK]]


def test_wsl_uses_explorer_when_wslview_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setattr(browser_module, "is_wsl", lambda: True)
    available = {"explorer.exe": "/mnt/c/Windows/explorer.exe"}

    def which(name: str) -> str | None:
        return available.get(name)

    monkeypatch.setattr(browser_module.shutil, "which", which)
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(browser_module.subprocess, "run", run)

    assert open_link(LINK) is True
    assert calls == [["/mnt/c/Windows/explorer.exe", LINK]]


def test_a_user_browser_setting_skips_the_wsl_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER", "preferred-browser %s")
    monkeypatch.setattr(browser_module, "is_wsl", lambda: True)
    opened: list[str] = []

    def standard_browser(url: str) -> bool:
        opened.append(url)
        return True

    def unexpected_windows_launcher(_url: str) -> bool:
        raise AssertionError("BROWSER must take precedence over the WSL launcher")

    monkeypatch.setattr(browser_module.webbrowser, "open", standard_browser)
    monkeypatch.setattr(browser_module, "_open_in_windows", unexpected_windows_launcher)

    assert open_link(LINK) is True
    assert opened == [LINK]


def test_non_wsl_keeps_the_standard_browser_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setattr(browser_module, "is_wsl", lambda: False)
    opened: list[str] = []

    def standard_browser(url: str) -> bool:
        opened.append(url)
        return True

    def unexpected_windows_launcher(_url: str) -> bool:
        raise AssertionError("The Windows launcher must only run in WSL")

    monkeypatch.setattr(browser_module.webbrowser, "open", standard_browser)
    monkeypatch.setattr(browser_module, "_open_in_windows", unexpected_windows_launcher)

    assert open_link(LINK) is True
    assert opened == [LINK]


def _browser_script(directory: Path, body: str) -> Path:
    script = directory / "fake-browser"
    script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _login_with_browser(
    portal: FakePortal, env: dict[str, str], browser: Path
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run `garden login` with the browser the test names, then play the person who clicks the link.

    webbrowser reads the BROWSER variable, so the real opener path runs. PATH is limited to the
    script's folder, so no other opener is found on this machine and the outcome is the script's.
    """
    run_env = dict(env, BROWSER=str(browser), PATH=str(browser.parent))
    process, head, url = open_sign_in(run_env, "laptop-3")
    urllib.request.urlopen(url, timeout=30).read()
    return finish_login(process, head), url


def test_a_browser_that_fails_prints_one_plain_line_and_the_noise_stays_hidden(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    browser = _browser_script(tmp_path, f'echo "{GIO_NOISE}" >&2\nexit 1')
    result, url = _login_with_browser(portal, env, browser)
    assert result.returncode == 0, result.stderr
    assert url.startswith(portal.url + "/api/agent/cli/authorize?")
    assert result.stdout.count("No browser could be opened here.") == 1
    assert "Open the link above yourself to approve the login." in result.stdout
    assert "Operation not supported" not in result.stdout + result.stderr
    assert "gio" not in result.stdout + result.stderr


def test_a_browser_that_opens_the_link_prints_no_opener_text(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    browser = _browser_script(tmp_path, f'echo "{GIO_NOISE}" >&2\necho "chatter"\nexit 0')
    result, _url = _login_with_browser(portal, env, browser)
    assert result.returncode == 0, result.stderr
    assert "No browser could be opened" not in result.stdout
    assert "chatter" not in result.stdout + result.stderr
    assert "Operation not supported" not in result.stdout + result.stderr


def test_the_printed_link_is_alone_on_its_line(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    browser = _browser_script(tmp_path, "exit 1")
    result, url = _login_with_browser(portal, env, browser)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "Approve this login for laptop-3 in your browser:"
    assert lines[1] == f"  {url}"


def test_a_failed_wsl_launcher_falls_back_and_leaves_the_login_url_visible(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    capture = tmp_path / "launcher-args"
    launcher = tmp_path / "explorer.exe"
    launcher.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$#" "$1" > "$LAUNCHER_CAPTURE"\nexit 1\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    run_env = dict(env, WSL_DISTRO_NAME="Ubuntu", PATH=str(tmp_path))
    run_env.pop("BROWSER", None)
    run_env["LAUNCHER_CAPTURE"] = str(capture)
    process, head, url = open_sign_in(run_env, "laptop-3")
    urllib.request.urlopen(url, timeout=30).read()
    result = finish_login(process, head)

    assert result.returncode == 0, result.stderr
    assert url in result.stdout
    assert "No browser could be opened here." in result.stdout
    assert capture.read_text(encoding="utf-8").splitlines() == ["1", url]
