"""State graders for the agent eval tasks."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal, cast

from opentelemetry import propagate

from garden.trace_contract import GARDEN_EVAL_SIDE_CALL_HEADER

if TYPE_CHECKING:
    from tests.evals.tasks import ClaimDraftTask


JsonObject = dict[str, object]


def _as_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


@dataclass(frozen=True)
class GraderResult:
    grader: str
    verdict: Literal["pass", "fail", "unknown"]
    reason: str


def api_json(base_url: str, path: str, token: str, *, side_call: str = "grader") -> object:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    propagate.inject(headers)
    headers[GARDEN_EVAL_SIDE_CALL_HEADER] = side_call
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return cast(object, json.loads(response.read()))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"API returned HTTP {error.code} for {path}.") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read {path} through the trial API: {error}.") from error


class DraftClaimGrader:
    """Checks one matching draft claim line, uploaded receipt, expected fields and audit events."""

    id = "draft-claim-with-one-receipt"

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
            summary_value = api_json(base_url, "/api/claims", token)
            if not isinstance(summary_value, list):
                return GraderResult(self.id, "fail", "The claims list was not a JSON list.")
            summaries = cast(list[object], summary_value)
            matches: list[JsonObject] = []
            for item in summaries:
                summary = _as_object(item)
                if summary is not None and summary.get("title") == task.claim_title:
                    matches.append(summary)
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
            line_value = detail.get("lines")
            if claim is None or not isinstance(line_value, list):
                return GraderResult(
                    self.id, "fail", "The claim detail is missing its claim or lines."
                )
            lines = cast(list[object], line_value)
            if claim.get("status") != "draft":
                return GraderResult(
                    self.id,
                    "fail",
                    f"Expected the claim to stay a draft, found {claim.get('status')!r}.",
                )
            if len(lines) != 1:
                return GraderResult(
                    self.id,
                    "fail",
                    f"Expected one claim line with a receipt, found {len(lines)} lines.",
                )
            line = _as_object(lines[0])
            if line is None:
                return GraderResult(self.id, "fail", "The claim line was not a JSON object.")
            try:
                amount_matches = Decimal(str(line.get("amount"))) == Decimal(task.amount)
            except InvalidOperation:
                amount_matches = False
            receipt_sha256 = line.get("receipt_sha256")
            receipt_matches = (
                isinstance(receipt_sha256, str)
                and re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is not None
            )
            checks: tuple[tuple[bool, str], ...] = (
                (line.get("category") == task.category, "category"),
                (line.get("spent_on") == task.spent_on.isoformat(), "spend date"),
                (amount_matches, "amount"),
                (line.get("description") == task.description, "description"),
                (receipt_matches, "receipt"),
            )
            failed = [label for matched, label in checks if not matched]
            if failed:
                return GraderResult(
                    self.id,
                    "fail",
                    "The claim line did not match the task: " + ", ".join(failed) + ".",
                )
            return GraderResult(
                self.id,
                "pass",
                "One draft claim line matches the requested category, date, amount and "
                "description, and has an uploaded receipt.",
            )
        except Exception as error:
            return GraderResult(
                self.id, "fail", f"The state grader could not verify the claim: {error}"
            )


def trial_passed(results: list[GraderResult]) -> bool:
    return bool(results) and all(
        result.verdict == "pass" and bool(result.reason) for result in results
    )
