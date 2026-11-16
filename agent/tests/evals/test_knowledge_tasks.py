from pathlib import Path

from tests.evals.knowledge_tasks import MONTH_CLOSE_PROCEDURE, _perfect_month_close


class ExpensePolicySearchContext:
    workspace = Path(".")

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        assert name == "knowledge_search_run"
        assert arguments == {"body": {"query": "month close procedure"}}
        return {
            "results": [
                {
                    "text": "Expense policy overview. Finance closes each month in Budgets, "
                    "but it does not describe the month close steps."
                }
            ]
        }


def test_month_close_reference_does_not_treat_general_policy_as_procedure() -> None:
    answer = _perfect_month_close(ExpensePolicySearchContext(), MONTH_CLOSE_PROCEDURE)

    assert "couldn't find month close steps" in answer
    assert "ask Finance" in answer
