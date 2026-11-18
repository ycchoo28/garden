from tests.evals.catalog import task_catalog
from tests.evals.registry import TASKS


def test_catalog_covers_registered_questions_without_running_trials() -> None:
    catalog = task_catalog()
    assert {row["id"] for row in catalog} == set(TASKS)
    for row in catalog:
        task = TASKS[str(row["id"])]
        assert row["prompt"] == task.prompt
        assert row["budget_tokens"] == task.budget_tokens
        assert row["graders"]
        if row["requires_embedding"]:
            assert row["area"] == "knowledge"
