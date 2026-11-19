"""Scaffold a new Garden agent eval task and its unit tests."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = ROOT / "agent" / "tests" / "evals"
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


def _render_task_module(task_id: str, module_slug: str, const_name: str, area: str, suite: str) -> str:
    return f'''"""Eval task for {task_id}."""

from __future__ import annotations

from tests.evals.grading import DraftClaimGrader, GraderResult
from tests.evals.knowledge_grading import AnswerJudgeGrader, JudgeDecision, ScriptedJudgeModel
from tests.evals.tasks import ClaimDraftTask, ReferenceContext


class {const_name.title().replace("_", "")}StateGrader(DraftClaimGrader):
    """Checks the deterministic backend/database state for {task_id}."""

    id = "{task_id}-state"

    def grade(
        self,
        base_url: str,
        token: str,
        task: ClaimDraftTask,
        *,
        answer: str | None = None,
        reference: bool = False,
    ) -> GraderResult:
        del base_url, token, task, answer, reference
        # TODO: Replace with real API/database verification (never inspect `answer` here).
        return GraderResult(self.id, "fail", "TODO: implement deterministic state check.")


def _judge_answer(answer: str) -> JudgeDecision:
    """Deterministic reference judge used only in CI reference mode."""
    text = answer.casefold().replace("\\u2019", "'")
    if "todo-expected-signal" in text:
        return "pass", "The reference answer satisfies the criteria."
    return "fail", "The reference answer did not satisfy the criteria."


def _perfect(context: ReferenceContext, task: ClaimDraftTask) -> str:
    del context, task
    return "TODO: implement perfect reference actions and reply (todo-expected-signal)."


def _wrong(context: ReferenceContext, task: ClaimDraftTask) -> str:
    del context, task
    return "TODO: implement wrong reference reply that must fail grading."


{const_name} = ClaimDraftTask(
    id="{task_id}",
    prompt="TODO: write a natural first-person user request with all required inputs.",
    asking_person="marcus.lee@fernhill.example",
    suite="{suite}",
    area="{area}",
    graders=(
        {const_name.title().replace("_", "")}StateGrader(),
        AnswerJudgeGrader(
            "{task_id}-answer",
            criteria=(
                "TODO: describe semantic pass/fail criteria for the agent's final answer "
                "(judge meaning, not exact wording)."
            ),
            reference_judge=ScriptedJudgeModel(_judge_answer),
        ),
    ),
    perfect=_perfect,
    wrong=_wrong,
)

TASKS: dict[str, ClaimDraftTask] = {{{const_name}.id: {const_name}}}
'''


def _render_test_module(task_id: str, module_slug: str, const_name: str) -> str:
    return f'''"""Unit tests for the {task_id} eval task."""

from __future__ import annotations

from tests.evals.knowledge_grading import AnswerJudgeGrader
from tests.evals.task_{module_slug} import {const_name}


def test_{module_slug}_answer_judge_accepts_paraphrases_and_rejects_wrong() -> None:
    grader = next(g for g in {const_name}.graders if isinstance(g, AnswerJudgeGrader))
    good = grader.grade("", "", {const_name}, answer="todo-expected-signal", reference=True)
    bad = grader.grade("", "", {const_name}, answer="unrelated wrong answer", reference=True)
    assert good.verdict == "pass"
    assert bad.verdict == "fail"
'''


def scaffold_eval(
    task_id: str,
    *,
    area: str = "claims",
    suite: str = "capability",
    evals_dir: Path = EVALS_DIR,
) -> tuple[Path, Path]:
    if not TASK_ID_RE.match(task_id):
        raise ValueError(
            "Task id must be lowercase kebab-case (3-64 chars, starting with a letter), "
            f"got {task_id!r}."
        )
    module_slug = task_id.replace("-", "_")
    const_name = module_slug.upper()
    task_path = evals_dir / f"task_{module_slug}.py"
    test_path = evals_dir / f"test_task_{module_slug}.py"
    for path in (task_path, test_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    evals_dir.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        _render_task_module(task_id, module_slug, const_name, area, suite),
        encoding="utf-8",
    )
    test_path.write_text(
        _render_test_module(task_id, module_slug, const_name),
        encoding="utf-8",
    )
    return task_path, test_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="Lowercase kebab-case task id, e.g. split-hotel-receipt")
    parser.add_argument("--area", default="claims", help="Domain area (claims, budgets, knowledge)")
    parser.add_argument(
        "--suite",
        default="capability",
        choices=("capability", "regression"),
        help="Eval suite classification",
    )
    args = parser.parse_args(argv)
    try:
        task_path, test_path = scaffold_eval(args.task_id, area=args.area, suite=args.suite)
    except (ValueError, FileExistsError) as error:
        raise SystemExit(str(error)) from None
    module_slug = args.task_id.replace("-", "_")
    print(f"Created {task_path.relative_to(ROOT)}")
    print(f"Created {test_path.relative_to(ROOT)}")
    print(
        f"Next: fill in the TODOs in task_{module_slug}.py (auto-discovered by tasks.py) and run:"
    )
    print(f"  just agent-eval --reference --task {args.task_id} --fake-embedding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
