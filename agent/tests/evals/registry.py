"""All registered eval tasks."""

from typing import cast

from tests.evals.knowledge_tasks import TASKS as KNOWLEDGE_TASKS
from tests.evals.task_types import EvalTask
from tests.evals.tasks import TASKS as CLAIM_TASKS

TASKS: dict[str, EvalTask] = cast(dict[str, EvalTask], {**CLAIM_TASKS, **KNOWLEDGE_TASKS})
