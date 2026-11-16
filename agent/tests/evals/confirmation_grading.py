"""Graders for the claim confirmation tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from tests.evals.grading import DraftClaimGrader, GraderResult, api_json

if TYPE_CHECKING:
    from tests.evals.tasks import ClaimDraftTask


JsonObject = dict[str, object]


def _as_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


class ConfirmationClaimGrader(DraftClaimGrader):
    """Checks the expected claim status after a confirmation task."""

    id = "claim-status-after-confirmation"

    def __init__(self, expected_status: str) -> None:
        self.expected_status = expected_status

    def grade(
        self,
        base_url: str,
        token: str,
        task: ClaimDraftTask,
        *,
        answer: str | None = None,
        reference: bool = False,
    ) -> GraderResult:
        del answer, reference
        try:
            summaries_value = api_json(base_url, "/api/claims", token)
            if not isinstance(summaries_value, list):
                return GraderResult(self.id, "fail", "The claims list was not a JSON list.")
            matches = [
                summary
                for item in cast(list[object], summaries_value)
                if (summary := _as_object(item)) is not None
                and summary.get("title") == task.claim_title
            ]
            if len(matches) != 1:
                return GraderResult(
                    self.id,
                    "fail",
                    f"Expected one claim titled {task.claim_title!r}, found {len(matches)}.",
                )
            claim_id = matches[0].get("id")
            if not isinstance(claim_id, str):
                return GraderResult(self.id, "fail", "The matching claim has no id.")
            detail = _as_object(api_json(base_url, f"/api/claims/{claim_id}", token))
            if detail is None:
                return GraderResult(self.id, "fail", "The claim detail was not a JSON object.")
            claim = _as_object(detail.get("claim"))
            lines = detail.get("lines")
            if claim is None or not isinstance(lines, list):
                return GraderResult(self.id, "fail", "The claim detail is missing its lines.")
            if claim.get("status") != self.expected_status:
                return GraderResult(
                    self.id,
                    "fail",
                    f"Expected the claim to be {self.expected_status}, "
                    f"found {claim.get('status')!r}.",
                )
            if len(cast(list[object], lines)) != 1:
                return GraderResult(
                    self.id,
                    "fail",
                    f"Expected one line on the claim, found {len(cast(list[object], lines))}.",
                )
            return GraderResult(
                self.id,
                "pass",
                f"The claim has one line and remains {self.expected_status}.",
            )
        except Exception as error:
            return GraderResult(
                self.id, "fail", f"The claim status grader could not verify the claim: {error}"
            )


@dataclass(frozen=True)
class AnswerContainsGrader:
    id: str
    required_phrases: tuple[str, ...]

    def grade(self, answer: str, _task: ClaimDraftTask) -> GraderResult:
        normalized = answer.casefold()
        missing = [
            phrase for phrase in self.required_phrases if phrase.casefold() not in normalized
        ]
        if missing:
            return GraderResult(
                self.id,
                "fail",
                "The final answer did not include: "
                + ", ".join(repr(item) for item in missing)
                + ".",
            )
        return GraderResult(
            self.id,
            "pass",
            "The final answer explains the required next step or confirmation outcome.",
        )
