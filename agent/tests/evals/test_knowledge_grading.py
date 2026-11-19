from __future__ import annotations

import pytest

from tests.evals.grading import trial_passed
from tests.evals.knowledge_grading import JudgeModel, KnowledgeAnswerGrader
from tests.evals.knowledge_tasks import HOTEL_LIMIT


class RecordedJudge(JudgeModel):
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        assert "JSON" in system_prompt
        assert HOTEL_LIMIT.judge_criteria in user_prompt
        assert "Candidate answer:\nThe policy says 220 dollars." in user_prompt
        return self.reply


@pytest.mark.parametrize(
    ("reply", "verdict", "expected_pass"),
    [
        ('{"outcome":"pass","reason":"It matches the policy."}', "pass", True),
        ('{"outcome":"fail","reason":"It states the wrong limit."}', "fail", False),
        ('{"outcome":"unknown","reason":"The evidence is unclear."}', "unknown", False),
    ],
)
def test_knowledge_answer_grader_uses_recorded_judge_replies(
    reply: str, verdict: str, expected_pass: bool
) -> None:
    grader = KnowledgeAnswerGrader()
    result = grader.grade(
        "http://127.0.0.1:1",
        "grader-token",
        HOTEL_LIMIT,
        answer="The policy says 220 dollars.",
        judge=RecordedJudge(reply),
    )

    assert result.verdict == verdict
    assert result.reason
    assert trial_passed([result]) is expected_pass
