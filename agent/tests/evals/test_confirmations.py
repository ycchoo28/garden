"""Confirmation policy behavior that does not need a Garden stack or browser."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.evals import runner
from tests.evals.grading import GraderResult
from tests.evals.harness import ConfirmationPolicy
from tests.evals.tasks import TASKS
from tests.shared.mcp import Mcp
from tests.shared.transcript import StepFailed


def _notice(link: str) -> dict[str, object]:
    return {
        "method": "notifications/message",
        "params": {"data": f"Confirmation required:\n  {link}"},
    }


def test_expected_confirmation_uses_the_task_decision() -> None:
    calls: list[tuple[str, str]] = []
    link = "http://localhost:56173/agent/confirmations/00000000-0000-0000-0000-000000000001"
    policy = ConfirmationPolicy(
        task_id="withdraw-denied",
        expected_decision="deny",
        public_url="http://localhost:56173",
        respond=lambda approval_link, decision: calls.append((approval_link, decision)),
    )

    policy.on_notification(_notice(link))
    policy.verify()

    assert calls == [(link, "deny")]
    assert policy.decisions == ["deny"]


def test_unexpected_confirmation_is_denied_and_fails_clearly() -> None:
    calls: list[tuple[str, str]] = []
    link = "http://localhost:56173/agent/confirmations/00000000-0000-0000-0000-000000000001"
    policy = ConfirmationPolicy(
        task_id="draft-one-receipt",
        expected_decision="none",
        public_url="http://localhost:56173",
        respond=lambda approval_link, decision: calls.append((approval_link, decision)),
    )

    policy.on_notification(_notice(link))

    with pytest.raises(StepFailed, match="Unexpected confirmation for task 'draft-one-receipt'"):
        policy.verify()
    assert calls == [(link, "deny")]


def test_missing_expected_confirmation_fails_clearly() -> None:
    policy = ConfirmationPolicy(
        task_id="withdraw-approved",
        expected_decision="approve",
        public_url="http://localhost:56173",
        respond=lambda _link, _decision: None,
    )

    with pytest.raises(StepFailed, match="Expected one approve confirmation"):
        policy.verify()


def test_a_second_confirmation_is_denied_and_fails_clearly() -> None:
    calls: list[tuple[str, str]] = []
    first = "http://localhost:56173/agent/confirmations/00000000-0000-0000-0000-000000000001"
    second = "http://localhost:56173/agent/confirmations/00000000-0000-0000-0000-000000000002"
    policy = ConfirmationPolicy(
        task_id="withdraw-denied",
        expected_decision="deny",
        public_url="http://localhost:56173",
        respond=lambda link, decision: calls.append((link, decision)),
    )

    policy.on_notification(_notice(first))
    policy.on_notification(_notice(second))

    with pytest.raises(StepFailed, match="Unexpected additional confirmation"):
        policy.verify()
    assert calls == [(first, "deny"), (second, "deny")]


def test_mcp_reply_delivers_confirmation_notifications_to_the_request_handler() -> None:
    link = "http://localhost:56173/agent/confirmations/00000000-0000-0000-0000-000000000001"
    notification = _notice(link)
    messages = iter([notification, {"id": 7, "result": {"isError": False}}])
    client = Mcp.__new__(Mcp)
    client.notices = []
    client._read = lambda: next(messages)  # type: ignore[method-assign]
    handled: list[dict[str, object]] = []

    reply = client.reply(7, on_notification=handled.append)

    assert reply == {"id": 7, "result": {"isError": False}}
    assert handled == [notification]
    assert client.notices == []


def test_confirmation_tasks_are_registered_with_their_expected_decisions() -> None:
    assert TASKS["submit-my-claim"].confirmation == "none"
    assert TASKS["submit-my-claim"].perfect is not None
    assert TASKS["submit-my-claim"].wrong is not None
    assert TASKS["withdraw-claim-approved"].confirmation == "approve"
    assert TASKS["withdraw-claim-approved"].perfect is not None
    assert TASKS["withdraw-claim-denied"].confirmation == "deny"
    assert TASKS["withdraw-claim-denied"].perfect is not None


def test_unexpected_confirmation_is_recorded_as_a_clear_trial_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_new_database(_name: str, *, template: str | None = None) -> None:
        _ = template
        return None

    def no_drop_database(_name: str) -> None:
        return None

    def fake_start_api(
        _database: str,
        _files_root: Path,
        _log: Path,
        _env: dict[str, str],
        _services: runner.Services,
        *,
        public_url: str | None = None,
        port: int | None = None,
        run_id: str | None = None,
        trial_id: str | None = None,
    ) -> str:
        _ = public_url, port, run_id, trial_id
        return "http://127.0.0.1:50000"

    def no_start_web(
        _api_url: str,
        _web_url: str,
        _env: dict[str, str],
        _log: Path,
        _services: runner.Services,
    ) -> None:
        return None

    def fake_grade(
        _task: object,
        _api_url: str,
        _token: str,
        *,
        answer: str,
        reference: bool,
        judge: object | None = None,
    ) -> list[GraderResult]:
        _ = answer, reference, judge
        return [GraderResult("confirmation", "fail", "An unexpected confirmation was requested.")]

    class UnexpectedConfirmationAdapter:
        tool_calls = 2

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run_reference(self, _solution: object) -> str:
            raise StepFailed(
                "Unexpected confirmation for task 'draft-one-receipt'; the runner declined it."
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(runner, "new_database", no_new_database)
    monkeypatch.setattr(runner, "drop_owned_database", no_drop_database)
    monkeypatch.setattr(runner, "start_api", fake_start_api)
    monkeypatch.setattr(runner, "_start_web", no_start_web)
    ports = iter((50001, 50002))
    monkeypatch.setattr(runner, "free_port", lambda: next(ports))
    monkeypatch.setattr(runner, "DshHarnessAdapter", UnexpectedConfirmationAdapter)
    monkeypatch.setattr(runner, "grade_task", fake_grade)
    solution = TASKS["draft-one-receipt"].perfect
    assert solution is not None

    record = runner.run_trial(
        run_id="00000000-0000-0000-0000-000000000000",
        task=TASKS["draft-one-receipt"],
        embedder="none",
        variant="perfect",
        expected_pass=True,
        solution=solution,
        logins={"acting": "token", "person_session": "session", "grader": "token"},
        env={},
        output=tmp_path,
        keep_failed=False,
    )

    assert record["passed"] is False
    assert record["failure_reason"] == (
        "Reference solution failed: Unexpected confirmation for task "
        "'draft-one-receipt'; the runner declined it."
    )
