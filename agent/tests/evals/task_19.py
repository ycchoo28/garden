"""Confirmation eval tasks for issue 19."""

from __future__ import annotations

from datetime import date

from tests.evals.confirmation_grading import AnswerContainsGrader, ConfirmationClaimGrader
from tests.evals.confirmation_tasks import (
    perfect_submit_claim,
    perfect_withdraw_approved,
    perfect_withdraw_denied,
    wrong_submit_claim,
    wrong_withdraw_approved,
    wrong_withdraw_denied,
)
from tests.evals.tasks import ClaimDraftTask

SUBMIT_MY_CLAIM = ClaimDraftTask(
    id="submit-my-claim",
    prompt='Submit my claim titled "Client dinner" for $42.30 in meals, spent on 2026-11-10.',
    asking_person="marcus.lee@fernhill.example",
    confirmation="none",
    suite="regression",
    claim_title="Client dinner",
    category="meals",
    spent_on=date(2026, 11, 10),
    amount="42.30",
    description="Dinner with a prospective customer",
    graders=(ConfirmationClaimGrader("draft"),),
    trajectory_graders=(
        AnswerContainsGrader("sends-person-to-portal", ("portal", "certify and submit")),
    ),
    perfect=perfect_submit_claim,
    wrong=wrong_submit_claim,
)

WITHDRAW_CLAIM_APPROVED = ClaimDraftTask(
    id="withdraw-claim-approved",
    prompt='Withdraw my submitted claim titled "Customer dinner".',
    asking_person="marcus.lee@fernhill.example",
    confirmation="approve",
    suite="regression",
    claim_title="Customer dinner",
    category="meals",
    spent_on=date(2026, 11, 10),
    amount="42.30",
    description="Dinner with a prospective customer",
    graders=(ConfirmationClaimGrader("withdrawn"),),
    trajectory_graders=(
        AnswerContainsGrader("reports-approved-withdrawal", ("approved", "withdrew")),
    ),
    perfect=perfect_withdraw_approved,
    wrong=wrong_withdraw_approved,
)

WITHDRAW_CLAIM_DENIED = ClaimDraftTask(
    id="withdraw-claim-denied",
    prompt='Withdraw my submitted claim titled "Client meal".',
    asking_person="marcus.lee@fernhill.example",
    confirmation="deny",
    suite="regression",
    claim_title="Client meal",
    category="meals",
    spent_on=date(2026, 11, 10),
    amount="42.30",
    description="Dinner with a prospective customer",
    graders=(ConfirmationClaimGrader("submitted"),),
    trajectory_graders=(
        AnswerContainsGrader("stops-after-denial", ("declined", "stopped", "still submitted")),
    ),
    perfect=perfect_withdraw_denied,
    wrong=wrong_withdraw_denied,
)

TASKS: dict[str, ClaimDraftTask] = {
    task.id: task for task in (SUBMIT_MY_CLAIM, WITHDRAW_CLAIM_APPROVED, WITHDRAW_CLAIM_DENIED)
}
