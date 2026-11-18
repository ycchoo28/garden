"""Portal task evals for issue 20."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast

from tests.evals.grading import DraftClaimGrader, GraderResult, api_json
from tests.evals.tasks import ClaimDraftTask, ReferenceContext
from tests.shared.stack import psql

LENA = "lena.fischer@fernhill.example"
MARCUS = "marcus.lee@fernhill.example"
PRIYA = "priya.nair@fernhill.example"
VARIANCE_MONTH = "2026-10"

JsonObject = dict[str, object]


@dataclass(frozen=True)
class ExpectedLine:
    category: str
    spent_on: date
    amount: str
    description: str
    receipt_file: str


@dataclass(frozen=True)
class PortalTask(ClaimDraftTask):
    expected_lines: tuple[ExpectedLine, ...] = ()


def _as_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


def _as_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return cast(list[object], value)


def _portal_task(task: ClaimDraftTask) -> PortalTask | None:
    if isinstance(task, PortalTask):
        return task
    return None


def _task_claim(base_url: str, token: str, task: PortalTask) -> tuple[JsonObject, list[object]]:
    summaries = _as_list(api_json(base_url, "/api/claims", token))
    if summaries is None:
        raise RuntimeError("The claims list was not a JSON list.")
    matches = [
        summary
        for summary_value in summaries
        if (summary := _as_object(summary_value)) is not None
        and summary.get("title") == task.claim_title
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one claim titled {task.claim_title!r}, found {len(matches)}.")
    claim_id = matches[0].get("id")
    if not isinstance(claim_id, str):
        raise RuntimeError("The matching claim has no id.")
    detail = _as_object(api_json(base_url, f"/api/claims/{claim_id}", token))
    if detail is None:
        raise RuntimeError("The claim detail was not a JSON object.")
    claim = _as_object(detail.get("claim"))
    lines = _as_list(detail.get("lines"))
    if claim is None or lines is None:
        raise RuntimeError("The claim detail is missing its claim or lines.")
    return claim, lines


def _matches_line(value: object, expected: ExpectedLine) -> bool:
    line = _as_object(value)
    if line is None:
        return False
    try:
        amount_matches = Decimal(str(line.get("amount"))) == Decimal(expected.amount)
    except InvalidOperation:
        amount_matches = False
    return (
        line.get("category") == expected.category
        and line.get("spent_on") == expected.spent_on.isoformat()
        and amount_matches
        and line.get("description") == expected.description
    )


class ClaimLinesGrader(DraftClaimGrader):
    """Checks draft claim lines and receipts against the requested expenses."""

    def __init__(self, grader_id: str) -> None:
        self.id = grader_id

    def grade(
        self,
        base_url: str,
        token: str,
        task: ClaimDraftTask,
        *,
        answer: str | None = None,
        reference: bool = False,
    ) -> GraderResult:
        del reference
        portal_task = _portal_task(task)
        if portal_task is None:
            return GraderResult(self.id, "fail", "The task has no portal claim expectations.")
        try:
            claim, lines = _task_claim(base_url, token, portal_task)
            if claim.get("status") != "draft":
                return GraderResult(self.id, "fail", "The claim did not stay a draft.")
            if len(lines) != len(portal_task.expected_lines):
                return GraderResult(
                    self.id,
                    "fail",
                    f"Expected {len(portal_task.expected_lines)} claim lines, found {len(lines)}.",
                )
            remaining = list(portal_task.expected_lines)
            receipt_hashes: list[str] = []
            for line_value in lines:
                match = next(
                    (expected for expected in remaining if _matches_line(line_value, expected)),
                    None,
                )
                if match is None:
                    return GraderResult(self.id, "fail", "A claim line did not match the task.")
                remaining.remove(match)
                line = _as_object(line_value)
                receipt_sha256 = line.get("receipt_sha256") if line is not None else None
                if (
                    not isinstance(receipt_sha256, str)
                    or re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is None
                ):
                    return GraderResult(self.id, "fail", "A claim line has no uploaded receipt.")
                receipt_hashes.append(receipt_sha256)
            if len(receipt_hashes) != len(set(receipt_hashes)):
                return GraderResult(
                    self.id, "fail", "The claim lines do not have distinct receipts."
                )
            return GraderResult(
                self.id,
                "pass",
                "The draft contains every requested line with a distinct uploaded receipt.",
            )
        except Exception as error:
            return GraderResult(self.id, "fail", f"Could not verify the draft claim: {error}")


class HotelCapAnswerGrader(DraftClaimGrader):
    """Checks that the answer explains the hotel policy cap and over-cap amount."""

    id = "hotel-cap-explained"

    def grade(
        self,
        base_url: str,
        token: str,
        task: ClaimDraftTask,
        *,
        answer: str | None = None,
        reference: bool = False,
    ) -> GraderResult:
        del reference
        text = (answer or "").lower()
        checks = (
            "hotel" in text,
            "220" in text,
            "245" in text,
            "over" in text,
            "draft" in text,
        )
        if all(checks):
            return GraderResult(self.id, "pass", "The answer flags the hotel cap and draft status.")
        return GraderResult(
            self.id,
            "fail",
            "The answer must say the USD 245 hotel is over the usual USD 220 cap "
            "and remains a draft.",
        )


class VarianceStateGrader(DraftClaimGrader):
    """Checks the seeded budget variance state and the absence of unauthorized writes."""

    id = "design-travel-variance-is-over"

    def grade(
        self,
        base_url: str,
        token: str,
        task: ClaimDraftTask,
        *,
        answer: str | None = None,
        reference: bool = False,
    ) -> GraderResult:
        del reference
        try:
            rows = _as_list(
                api_json(
                    base_url,
                    f"/api/budgets/variance?from={VARIANCE_MONTH}&to={VARIANCE_MONTH}",
                    token,
                )
            )
            if rows is None:
                return GraderResult(self.id, "fail", "The variance report was not a JSON list.")
            matches = [
                row
                for row_value in rows
                if (row := _as_object(row_value)) is not None
                and row.get("department_code") == "design"
                and row.get("year_month") == VARIANCE_MONTH
                and row.get("category") == "travel"
            ]
            if len(matches) != 1:
                return GraderResult(self.id, "fail", "The expected Design travel line was missing.")
            row = matches[0]
            if (
                row.get("budget") == "5.00"
                and row.get("actual") == "11.00"
                and row.get("variance") == "-6.00"
            ):
                return GraderResult(
                    self.id,
                    "pass",
                    "The Variance API shows Design travel over budget by USD 6.00.",
                )
            return GraderResult(
                self.id, "fail", "The Design travel variance was not the expected value."
            )
        except Exception as error:
            return GraderResult(self.id, "fail", f"Could not verify the variance report: {error}")


class VarianceAnswerGrader(DraftClaimGrader):
    """Checks that the answer identifies the over-budget line and amounts."""

    id = "over-budget-line-named"

    def grade(
        self,
        base_url: str,
        token: str,
        task: ClaimDraftTask,
        *,
        answer: str | None = None,
        reference: bool = False,
    ) -> GraderResult:
        del reference
        text = (answer or "").lower()
        checks = (
            "design" in text,
            "travel" in text,
            VARIANCE_MONTH in text,
            "over" in text,
            "6" in text,
        )
        if all(checks):
            return GraderResult(
                self.id, "pass", "The answer names the over-budget Design travel line and amount."
            )
        return GraderResult(
            self.id,
            "fail",
            "The answer must name Design travel in 2026-10 and identify USD 6.00 over budget.",
        )


class ConflictRecoveryAnswerGrader(DraftClaimGrader):
    """Checks that the answer acknowledges and recovers from the version conflict."""

    id = "version-conflict-recovered"

    def grade(
        self,
        base_url: str,
        token: str,
        task: ClaimDraftTask,
        *,
        answer: str | None = None,
        reference: bool = False,
    ) -> GraderResult:
        del reference
        text = (answer or "").lower()
        reread = any(word in text for word in ("re-read", "reread", "reloaded", "reload"))
        if "conflict" in text and reread and "draft" in text:
            return GraderResult(
                self.id, "pass", "The answer reports recovery after reloading the changed draft."
            )
        return GraderResult(
            self.id,
            "fail",
            "The answer must report the version conflict, a reload, and that the claim "
            "is still a draft.",
        )


def _tool_object(context: ReferenceContext, name: str, arguments: dict[str, object]) -> JsonObject:
    value = _as_object(context.call(name, arguments))
    if value is None:
        raise RuntimeError(f"The {name} tool did not return an object.")
    return value


def _receipt_sha256(context: ReferenceContext, filename: str) -> str:
    response = _tool_object(
        context, "files_file_upload", {"path": str(context.workspace / filename)}
    )
    uploaded = _as_object(response.get("uploaded")) or response
    sha256 = uploaded.get("sha256")
    if not isinstance(sha256, str):
        raise RuntimeError("The file upload tool did not return a receipt SHA-256.")
    return sha256


def _claim_id_and_version(response: JsonObject) -> tuple[str, int]:
    claim = _as_object(response.get("claim"))
    claim_id = claim.get("id") if claim is not None else None
    version = claim.get("version") if claim is not None else None
    if not isinstance(claim_id, str) or not isinstance(version, int):
        raise RuntimeError("The claim response did not include an id and version.")
    return claim_id, version


def _add_line(
    context: ReferenceContext,
    claim_id: str,
    version: int,
    expected: ExpectedLine,
    receipt_sha256: str,
) -> int:
    response = _tool_object(
        context,
        "claims_line_add",
        {
            "claim_id": claim_id,
            "body": {
                "expected_version": version,
                "category": expected.category,
                "spent_on": expected.spent_on.isoformat(),
                "amount": expected.amount,
                "description": expected.description,
                "receipt_sha256": receipt_sha256,
            },
        },
    )
    _, next_version = _claim_id_and_version(response)
    return next_version


def _perfect_two_receipts(context: ReferenceContext, task: ClaimDraftTask) -> str:
    portal_task = _portal_task(task)
    if portal_task is None:
        raise RuntimeError("The task has no receipt expectations.")
    receipts = [
        (expected, _receipt_sha256(context, expected.receipt_file))
        for expected in portal_task.expected_lines
    ]
    claim = _tool_object(context, "claims_claim_create", {"body": {"title": task.claim_title}})
    claim_id, version = _claim_id_and_version(claim)
    for expected, receipt_sha256 in receipts:
        version = _add_line(context, claim_id, version, expected, receipt_sha256)
    return (
        "I drafted both receipt lines. The USD 245 hotel is over the usual "
        "USD 220 per night cap, and the claim remains a draft for you to review."
    )


def _wrong_two_receipts(context: ReferenceContext, task: ClaimDraftTask) -> str:
    portal_task = _portal_task(task)
    if portal_task is None or not portal_task.expected_lines:
        raise RuntimeError("The task has no receipt expectations.")
    claim = _tool_object(context, "claims_claim_create", {"body": {"title": task.claim_title}})
    claim_id, version = _claim_id_and_version(claim)
    expected = portal_task.expected_lines[0]
    receipt_sha256 = _receipt_sha256(context, expected.receipt_file)
    _add_line(context, claim_id, version, expected, receipt_sha256)
    return f"I drafted {task.claim_title} and everything is within policy."


def _person_session(context: ReferenceContext, subject: str) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    escaped_subject = subject.replace("'", "''")
    psql(
        context.database,
        "insert into users.web_session (token_hash, person_id, created_at, last_seen_at) "
        f"select '{token_hash}', id, now(), now() from users.person "
        f"where subject = '{escaped_subject}'",
    )
    count = psql(
        context.database,
        f"select count(*) from users.web_session where token_hash = '{token_hash}'",
    )
    if count != "1":
        raise RuntimeError(f"Could not create a portal session for {subject!r}.")
    return token


def _portal_post(
    context: ReferenceContext,
    path: str,
    body: dict[str, object],
    session: str,
    *,
    expected_status: int,
) -> JsonObject:
    request = urllib.request.Request(
        f"{context.portal_url.rstrip('/')}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cookie": f"garden_session={session}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            result = _as_object(cast(object, json.loads(response.read())))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"The portal returned HTTP {error.code} for {path}.") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not call {path} through the trial API: {error}.") from error
    if status != expected_status or result is None:
        raise RuntimeError(f"The portal returned an unexpected response for {path}.")
    return result


def _setup_variance(context: ReferenceContext) -> None:
    departments = _as_list(
        api_json(context.portal_url, "/api/users/departments", context.acting_token)
    )
    if departments is None:
        raise RuntimeError("The departments response was not a JSON list.")
    design = next(
        (
            department
            for department_value in departments
            if (department := _as_object(department_value)) is not None
            and department.get("code") == "design"
        ),
        None,
    )
    department_id = design.get("id") if design is not None else None
    if not isinstance(department_id, str):
        raise RuntimeError("The Design department id was not found.")
    _tool_object(
        context,
        "budgets_line_create",
        {
            "body": {
                "department_id": department_id,
                "year_month": VARIANCE_MONTH,
                "category": "travel",
                "amount": "5.00",
            }
        },
    )

    receipt_sha256 = _receipt_sha256(context, "receipt.pdf")
    claimant_session = _person_session(context, MARCUS)
    manager_session = _person_session(context, PRIYA)
    created = _portal_post(
        context,
        "/api/claims",
        {"title": "October travel spend"},
        claimant_session,
        expected_status=201,
    )
    claim_id, version = _claim_id_and_version(created)
    added = _portal_post(
        context,
        f"/api/claims/{claim_id}/lines",
        {
            "expected_version": version,
            "category": "travel",
            "spent_on": f"{VARIANCE_MONTH}-15",
            "amount": "11.00",
            "description": "October travel approved-spend fixture",
            "receipt_sha256": receipt_sha256,
        },
        claimant_session,
        expected_status=201,
    )
    _, version = _claim_id_and_version(added)
    submitted = _portal_post(
        context,
        f"/api/claims/{claim_id}/submit",
        {"expected_version": version},
        claimant_session,
        expected_status=200,
    )
    _, version = _claim_id_and_version(submitted)
    approved = _portal_post(
        context,
        f"/api/claims/{claim_id}/decision",
        {"expected_version": version, "decision": "approve", "reason": None},
        manager_session,
        expected_status=200,
    )
    approved_claim = _as_object(approved.get("claim"))
    if approved_claim is None or approved_claim.get("status") != "approved":
        raise RuntimeError("The over-budget claim fixture was not approved.")


def _variance_rows(context: ReferenceContext) -> list[JsonObject]:
    rows = _as_list(
        context.call(
            "budgets_variance_read",
            {"from": VARIANCE_MONTH, "to": VARIANCE_MONTH},
        )
    )
    if rows is None:
        raise RuntimeError("The variance tool did not return a list.")
    return [row for row_value in rows if (row := _as_object(row_value)) is not None]


def _perfect_variance(context: ReferenceContext, task: ClaimDraftTask) -> str:
    _setup_variance(context)
    rows = _variance_rows(context)
    match = next(
        (
            row
            for row in rows
            if row.get("department_code") == "design"
            and row.get("year_month") == VARIANCE_MONTH
            and row.get("category") == "travel"
        ),
        None,
    )
    if match is None or match.get("variance") != "-6.00":
        raise RuntimeError("The variance tool did not show the expected over-budget line.")
    return (
        "In 2026-10, Design's travel budget line is over by USD 6.00: "
        "the budget is USD 5.00, actual spend is USD 11.00, and variance is -USD 6.00."
    )


def _wrong_variance(context: ReferenceContext, task: ClaimDraftTask) -> str:
    _setup_variance(context)
    _variance_rows(context)
    return "In 2026-10, Design's meals budget line is over by USD 6.00."


def _start_version_conflict(
    context: ReferenceContext, task: PortalTask
) -> tuple[str, int, ExpectedLine, str]:
    if len(task.expected_lines) != 2:
        raise RuntimeError("The version conflict task needs exactly two expected lines.")
    human_line, agent_line = task.expected_lines
    claim = _tool_object(context, "claims_claim_create", {"body": {"title": task.claim_title}})
    claim_id, created_version = _claim_id_and_version(claim)
    snapshot = _tool_object(context, "claims_claim_read", {"claim_id": claim_id})
    _, observed_version = _claim_id_and_version(snapshot)
    if observed_version != created_version:
        raise RuntimeError("The claim changed before the simulated portal edit.")
    human_receipt = _receipt_sha256(context, human_line.receipt_file)
    agent_receipt = _receipt_sha256(context, agent_line.receipt_file)
    person_session = _person_session(context, task.asking_person)
    human_change = _portal_post(
        context,
        f"/api/claims/{claim_id}/lines",
        {
            "expected_version": observed_version,
            "category": human_line.category,
            "spent_on": human_line.spent_on.isoformat(),
            "amount": human_line.amount,
            "description": human_line.description,
            "receipt_sha256": human_receipt,
        },
        person_session,
        expected_status=201,
    )
    _, changed_version = _claim_id_and_version(human_change)
    if changed_version <= observed_version:
        raise RuntimeError("The portal edit did not move the claim version.")
    try:
        _add_line(context, claim_id, observed_version, agent_line, agent_receipt)
    except RuntimeError as error:
        if re.search(r"\b409\b", str(error)) is None:
            raise RuntimeError("The stale agent write did not return a 409 conflict.") from error
    else:
        raise RuntimeError("The stale agent write unexpectedly succeeded.")
    return claim_id, changed_version, agent_line, agent_receipt


def _perfect_version_conflict(context: ReferenceContext, task: ClaimDraftTask) -> str:
    portal_task = _portal_task(task)
    if portal_task is None:
        raise RuntimeError("The task has no portal claim expectations.")
    claim_id, changed_version, agent_line, receipt_sha256 = _start_version_conflict(
        context, portal_task
    )
    refreshed = _tool_object(context, "claims_claim_read", {"claim_id": claim_id})
    _, current_version = _claim_id_and_version(refreshed)
    lines = _as_list(refreshed.get("lines"))
    if current_version != changed_version or lines is None or len(lines) != 1:
        raise RuntimeError("The agent did not reread the claim after the portal edit.")
    _add_line(context, claim_id, current_version, agent_line, receipt_sha256)
    return (
        "I reloaded the claim after a version conflict, kept the portal's meals line, "
        "and added the travel line. The claim remains a draft."
    )


def _wrong_version_conflict(context: ReferenceContext, task: ClaimDraftTask) -> str:
    portal_task = _portal_task(task)
    if portal_task is None:
        raise RuntimeError("The task has no portal claim expectations.")
    _start_version_conflict(context, portal_task)
    return "The claim changed while I was working, so I stopped without finishing the draft."


TWO_RECEIPTS = PortalTask(
    id="draft-two-receipts-hotel-over-cap",
    prompt=(
        'Draft a claim titled "Client travel" using receipt.pdf and second-receipt.pdf. '
        "The hotel receipt is USD 245.00 for one night in a city not on the finance major-city "
        "exception list, above the usual USD 220.00 per night cap. The second receipt is a "
        "USD 34.50 taxi to the client site. Leave the claim as a draft, do not submit it, and "
        "tell me whether the hotel is within policy."
    ),
    asking_person=LENA,
    claim_title="Client travel",
    expected_lines=(
        ExpectedLine(
            "travel",
            date(2026, 11, 10),
            "245.00",
            "One night at the hotel in a non-listed city",
            "receipt.pdf",
        ),
        ExpectedLine(
            "travel",
            date(2026, 11, 10),
            "34.50",
            "Taxi to the client site",
            "second-receipt.pdf",
        ),
    ),
    graders=(ClaimLinesGrader("draft-claim-with-two-receipts"), HotelCapAnswerGrader()),
    perfect=_perfect_two_receipts,
    wrong=_wrong_two_receipts,
)

OVER_BUDGET_VARIANCE = ClaimDraftTask(
    id="which-budget-line-is-over",
    prompt=(
        "Using the variance report for 2026-10, which Design department budget line is over "
        "budget? Give me the category and the variance."
    ),
    asking_person=LENA,
    area="budgets",
    graders=(VarianceStateGrader(), VarianceAnswerGrader()),
    perfect=_perfect_variance,
    wrong=_wrong_variance,
)

VERSION_CONFLICT = PortalTask(
    id="recover-claim-version-conflict",
    prompt=(
        'Draft a claim titled "Client visit" with the USD 65.00 travel line in '
        "second-receipt.pdf. While you work, I am adding the USD 28.00 meals line from "
        "receipt.pdf in the portal. If the claim version changes, reload it, keep both lines, "
        "and leave the claim as a draft. Tell me when the requested travel line is added."
    ),
    asking_person=LENA,
    claim_title="Client visit",
    expected_lines=(
        ExpectedLine(
            "meals",
            date(2026, 11, 11),
            "28.00",
            "Client lunch added in the portal",
            "receipt.pdf",
        ),
        ExpectedLine(
            "travel",
            date(2026, 11, 12),
            "65.00",
            "Taxi to the client site",
            "second-receipt.pdf",
        ),
    ),
    graders=(
        ClaimLinesGrader("draft-claim-keeps-portal-change"),
        ConflictRecoveryAnswerGrader(),
    ),
    perfect=_perfect_version_conflict,
    wrong=_wrong_version_conflict,
)

TASKS: dict[str, ClaimDraftTask] = {
    task.id: task for task in (TWO_RECEIPTS, OVER_BUDGET_VARIANCE, VERSION_CONFLICT)
}
