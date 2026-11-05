"""The rules for putting a batch live, tested without a database."""

from typing import Any

from app.modules.knowledge.batches.publish import (
    ACCESS_RULES_CHANGED,
    LARGE_REMOVAL,
    documents_to_close,
    risk_triggers,
    same_rules,
    staged_digest,
)


def test_the_digest_changes_with_any_change_to_the_staged_result() -> None:
    payload = {"chunks": [{"body": "Retention is seven years."}], "mode": "full_set"}
    same = {"mode": "full_set", "chunks": [{"body": "Retention is seven years."}]}
    changed = {"mode": "full_set", "chunks": [{"body": "Retention is ten years."}]}
    assert staged_digest(payload) == staged_digest(same)
    assert staged_digest(payload) != staged_digest(changed)
    assert len(staged_digest(payload)) == 64


def test_a_replaced_document_is_closed_and_a_full_set_removes_the_rest() -> None:
    live = {"a.md", "b.md", "c.md"}
    staged = {"a.md", "d.md"}
    replaced, removed = documents_to_close(live=live, staged=staged, full_set=True)
    assert replaced == {"a.md"}
    assert removed == {"b.md", "c.md"}


def test_a_partial_batch_removes_nothing() -> None:
    replaced, removed = documents_to_close(live={"a.md", "b.md"}, staged={"a.md"}, full_set=False)
    assert replaced == {"a.md"}
    assert removed == set()


def test_a_large_removal_and_an_access_change_are_flagged() -> None:
    assert risk_triggers(removed=2, rules_changed=False) == []
    assert risk_triggers(removed=3, rules_changed=False) == [LARGE_REMOVAL]
    assert risk_triggers(removed=0, rules_changed=True) == [ACCESS_RULES_CHANGED]
    assert risk_triggers(removed=5, rules_changed=True) == [LARGE_REMOVAL, ACCESS_RULES_CHANGED]


def test_rules_that_grant_the_same_reach_compare_equal() -> None:
    first: list[dict[str, Any]] = [
        {"role": "manager", "all_departments": False, "department_codes": ["design", "ops"]},
        {"role": "finance", "all_departments": True, "department_codes": []},
    ]
    reordered: list[dict[str, Any]] = [
        {"role": "finance", "all_departments": True, "department_codes": []},
        {"role": "manager", "all_departments": False, "department_codes": ["ops", "design"]},
    ]
    assert same_rules(first, reordered)
    assert not same_rules(first, [{**first[0], "all_departments": True}, first[1]])
