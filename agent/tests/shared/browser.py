"""Headless approval: a login or a critical action is approved in a browser, as a person would."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.shared.stack import PERSON_NAME, PERSON_SUBJECT
from tests.shared.transcript import Run, StepFailed

HERE = Path(__file__).resolve().parent


def approve(script: str, link: str, media: Path) -> subprocess.CompletedProcess[str]:
    """Approves a login or an action in a headless browser, through its own script."""
    args = ["node", str(HERE / script), link, str(media), PERSON_NAME, PERSON_SUBJECT]
    return subprocess.run(args, capture_output=True, text=True, timeout=240, check=False)


def sign_in_headless(
    command: list[str], env: dict[str, str], media: Path, run: Run, label: str
) -> tuple[int, str]:
    """Runs `garden login` with its link printed, approves the link in a headless browser, and waits
    for the CLI to finish. Returns the exit code and the CLI's output."""
    proc = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert proc.stdout is not None
    link = ""
    collected: list[str] = []
    for line in proc.stdout:
        collected.append(line)
        if line.startswith("  http") and not link:
            link = line.strip()
            run.secrets.append(link)
            approval = approve("approve-login.cjs", link, media)
            run.record(
                f"{label}: approve-login.cjs <login link> (headless)",
                approval.stdout + approval.stderr,
                approval.returncode,
            )
            run.check(
                approval.returncode == 0, f"{label}: the login was approved in the headless browser"
            )
    code = proc.wait(timeout=300)
    output = "".join(collected)
    if not link:
        raise StepFailed(f"{label}: the CLI printed no login link:\n{output}")
    return code, output
