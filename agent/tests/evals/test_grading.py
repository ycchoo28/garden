from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from urllib import request as urllib_request

import pytest
from opentelemetry.sdk.trace import TracerProvider

from garden.trace_contract import GARDEN_EVAL_SIDE_CALL_HEADER
from tests.evals.grading import DraftClaimGrader, GraderResult, api_json, trial_passed
from tests.evals.tasks import DRAFT_ONE_RECEIPT


def test_grader_api_calls_keep_the_trial_context_and_are_tagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: object, *, timeout: float) -> FakeResponse:
        del timeout
        headers = cast(urllib_request.Request, request).headers
        captured.update({key.lower(): value for key, value in headers.items()})
        return FakeResponse()

    monkeypatch.setattr("tests.evals.grading.urllib.request.urlopen", fake_urlopen)
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("trial"):
        api_json("http://127.0.0.1", "/api/claims", "grader-token")
    provider.shutdown()

    assert captured[GARDEN_EVAL_SIDE_CALL_HEADER] == "grader"
    assert "traceparent" in captured


def test_draft_claim_grader_passes_one_draft_line_with_a_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary: dict[str, object] = {"id": "claim-1", "title": DRAFT_ONE_RECEIPT.claim_title}
    detail: dict[str, object] = {
        "claim": {"id": "claim-1", "title": DRAFT_ONE_RECEIPT.claim_title, "status": "draft"},
        "lines": [
            {
                "category": DRAFT_ONE_RECEIPT.category,
                "spent_on": DRAFT_ONE_RECEIPT.spent_on.isoformat(),
                "amount": DRAFT_ONE_RECEIPT.amount,
                "description": DRAFT_ONE_RECEIPT.description,
                "receipt_sha256": "a" * 64,
            }
        ],
    }
    responses: Iterator[object] = iter([[summary], detail])

    def fake_api_json(_base_url: str, _path: str, _token: str) -> object:
        return next(responses)

    monkeypatch.setattr("tests.evals.grading.api_json", fake_api_json)

    result = DraftClaimGrader().grade("http://127.0.0.1:1", "grader-token", DRAFT_ONE_RECEIPT)

    assert result.verdict == "pass"
    assert result.reason


def test_draft_claim_grader_fails_when_the_receipt_line_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary: dict[str, object] = {"id": "claim-1", "title": DRAFT_ONE_RECEIPT.claim_title}
    detail: dict[str, object] = {
        "claim": {
            "id": "claim-1",
            "title": DRAFT_ONE_RECEIPT.claim_title,
            "status": "draft",
        },
        "lines": [],
    }
    responses: Iterator[object] = iter([[summary], detail])

    def fake_api_json(_base_url: str, _path: str, _token: str) -> object:
        return next(responses)

    monkeypatch.setattr("tests.evals.grading.api_json", fake_api_json)

    result = DraftClaimGrader().grade("http://127.0.0.1:1", "grader-token", DRAFT_ONE_RECEIPT)

    assert result.verdict == "fail"
    assert "line" in result.reason.lower()


def test_trial_passes_only_when_every_grader_passes() -> None:
    passing = GraderResult(grader="one", verdict="pass", reason="state matched")
    failing = GraderResult(grader="two", verdict="fail", reason="state differed")

    assert trial_passed([passing]) is True
    assert trial_passed([passing, failing]) is False
    assert trial_passed([]) is False
