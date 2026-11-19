"""Launching dsh for a run: its critical-action approvals and its patch with the test-only store."""

from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import yaml

from tests.shared.browser import approve
from tests.shared.transcript import Run

LINK = re.compile(r"https?://\S+/agent/confirmations/[0-9a-f-]{36}")


def run_dsh(
    command: list[str], env: dict[str, str], media: Path, run: Run
) -> tuple[int, str, str, list[subprocess.CompletedProcess[str]]]:
    """Runs dsh, and approves each critical action it announces, as the person would in the browser.
    The approval link arrives while dsh's call waits, so the approval runs alongside dsh."""
    proc = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None and proc.stderr is not None
    out: list[str] = []
    err: list[str] = []
    approvals: list[subprocess.CompletedProcess[str]] = []
    seen: set[str] = set()
    lock = threading.Lock()

    def watch(stream: Any, sink: list[str]) -> None:
        for line in stream:
            with lock:
                sink.append(line)
            for match in LINK.finditer(line):
                link = match.group(0)
                if link in seen:
                    continue
                seen.add(link)
                run.secrets.append(link)
                approvals.append(approve("approve-confirmation.cjs", link, media))

    readers = [
        threading.Thread(target=watch, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=watch, args=(proc.stderr, err), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        code = proc.wait(timeout=600)
    finally:
        for reader in readers:
            reader.join(timeout=300)
    return code, "".join(out), "".join(err), approvals


def dsh_overlay(launcher: Path, secrets_file: Path, installed_patch: Path, out: Path) -> Path:
    """dsh's patch for this run: the installed one, with the garden server given the test-only
    file store.

    dsh passes only some environment variables to an MCP server, and the secret store variable
    is one of those it drops. On a laptop the server reads the keychain and needs no overlay.
    The model and the login stay as the installer and `garden dsh` set them: the token is in
    dsh's environment, never here.
    """
    document = yaml.safe_load(installed_patch.read_text(encoding="utf-8"))
    rows = document[0]["insert"]
    for row in rows:
        if row.get("id") == "mcp-garden":
            row["config"]["command"] = str(launcher)
            row["config"]["env"] = {"GARDEN_SECRET_STORE": f"file:{secrets_file}"}
    path = out / "dsh-e2e-overlay.yml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path
