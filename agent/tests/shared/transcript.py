"""The transcript a test run writes, and the two exceptions that stop a run."""

from __future__ import annotations


class Run:
    """Collects the transcript and the checks. Every secret goes through `redact` before it is
    kept."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []
        self.secrets: list[str] = []

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def heading(self, title: str) -> None:
        self.say(f"\n## {title}\n")

    def say(self, text: str) -> None:
        text = self.redact(text)
        self.lines.append(text)
        print(text, flush=True)

    def record(self, command: str, output: str, code: int | None = None) -> None:
        shown = self.redact(output.strip())
        if len(shown) > 1500:
            shown = shown[:1500] + "\n[... truncated ...]"
        status = "" if code is None else f" (exit {code})"
        self.say(f"$ {self.redact(command)}{status}\n```\n{shown}\n```")

    def check(self, ok: bool, what: str) -> None:
        self.say(f"- {'PASS' if ok else 'FAIL'}: {what}")
        if not ok:
            self.failures.append(what)


class StepFailed(Exception):
    """A step cannot go on, so the run stops here and the transcript says why."""


class Interrupted(Exception):
    """A stop signal arrived while the stack was up, so the run tears down and ends."""
