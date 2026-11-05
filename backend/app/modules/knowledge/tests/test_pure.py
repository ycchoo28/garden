"""Pure helpers of the knowledge module: rank fusion, parsing, chunking, preview and text."""

import uuid

import pytest

from app.modules.knowledge.reading.chunking import CHILD_TOKENS, PARENT_TOKENS, chunk_tree
from app.modules.knowledge.reading.parsing import DocumentTree, ParseError, Section, parse_document
from app.modules.knowledge.reading.text import detect_language, extract_identifiers
from app.modules.knowledge.search.rrf import RRF_K, rrf_merge
from app.modules.knowledge.search.search import Candidate, assemble


def words(count: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{index}" for index in range(count))


def test_rrf_scores_items_seen_in_several_lists_higher() -> None:
    merged = rrf_merge([["a", "b", "c"], ["b", "a"]])
    scores = dict(merged)
    # a and b are both ranked 1 and 2, so they tie. c appears once, at rank 3.
    assert scores["a"] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert scores["a"] == pytest.approx(scores["b"])
    assert scores["c"] == pytest.approx(1 / (RRF_K + 3))
    assert [item for item, _ in merged][-1] == "c"


def test_rrf_of_nothing_is_empty() -> None:
    assert rrf_merge([]) == []


def test_markdown_headings_build_heading_paths() -> None:
    data = b"# Policy\n\nIntro text.\n\n## Access\n\nRules here.\n"
    tree = parse_document(data, "policy.md", "text/plain")
    assert tree.status == "ok"
    assert tree.title == "Policy"
    assert [section.heading_path for section in tree.sections] == ["Policy", "Policy > Access"]


def test_text_without_headings_is_marked_as_no_structure() -> None:
    tree = parse_document(b"Just a plain note.\n\nAnother paragraph.", "note.txt", "text/plain")
    assert tree.status == "no_structure"
    assert tree.title == "note"


def test_empty_text_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        _ = parse_document(b"   \n  ", "empty.txt", "text/plain")


def test_non_utf8_text_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        _ = parse_document(b"\xff\xfe\x00bad", "bad.txt", "text/plain")


def test_broken_pdf_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        _ = parse_document(b"%PDF-1.4 not really a pdf", "broken.pdf", "application/pdf")


def test_small_neighbouring_sections_merge_into_one_chunk() -> None:
    tree = DocumentTree(
        title="Doc",
        sections=(Section("A", "", (words(20),)), Section("B", "", (words(30, "b"),))),
        status="ok",
    )
    drafts = chunk_tree(tree, count=lambda text: len(text.split()))
    assert [(draft.kind, draft.token_count) for draft in drafts] == [("single", 50)]


def test_sections_of_a_useful_size_stay_apart() -> None:
    tree = DocumentTree(
        title="Doc",
        sections=(Section("A", "", (words(300),)), Section("B", "", (words(300, "b"),))),
        status="ok",
    )
    drafts = chunk_tree(tree, count=lambda text: len(text.split()))
    assert [draft.kind for draft in drafts] == ["single", "single"]


def test_large_sections_become_parents_with_embedded_children() -> None:
    paragraphs = tuple(words(100, f"p{index}_") for index in range(40))
    tree = DocumentTree(title="Big", sections=(Section("Big", "", paragraphs),), status="ok")
    drafts = chunk_tree(tree, count=lambda text: len(text.split()))
    parents = [draft for draft in drafts if draft.kind == "parent"]
    children = [draft for draft in drafts if draft.kind == "child"]
    assert parents and children
    assert all(draft.token_count <= PARENT_TOKENS for draft in drafts)
    assert all(
        child.parent is not None and drafts[child.parent].kind == "parent" for child in children
    )
    assert all(child.token_count <= CHILD_TOKENS for child in children)


def candidate(
    unit: uuid.UUID, chunk: uuid.UUID, group: str, language: str, tokens: int, score: float
) -> Candidate:
    return Candidate(
        chunk_id=chunk,
        unit_id=unit,
        batch_file_id=uuid.uuid4(),
        translation_group=group,
        title="Doc",
        language=language,
        heading_path="Doc",
        locator="",
        text=f"text {chunk}",
        token_count=tokens,
        score=score,
    )


def test_child_chunks_of_one_parent_answer_once() -> None:
    parent = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    hits, truncated = assemble(
        [
            candidate(parent, first, "g1", "en", 100, 0.9),
            candidate(parent, second, "g1", "en", 100, 0.8),
        ],
        query_language="en",
        limit=5,
        max_tokens=1000,
    )
    assert len(hits) == 1
    assert hits[0].chunk_ids == (first, second)
    assert not truncated


def test_a_translation_group_answers_once_in_the_query_language() -> None:
    chinese_unit, english_unit = uuid.uuid4(), uuid.uuid4()
    hits, _ = assemble(
        [
            candidate(chinese_unit, uuid.uuid4(), "g", "zh", 50, 0.9),
            candidate(english_unit, uuid.uuid4(), "g", "en", 50, 0.8),
        ],
        query_language="en",
        limit=5,
        max_tokens=1000,
    )
    assert [hit.language for hit in hits] == ["en"]


def test_the_token_budget_cuts_results_and_sets_truncated() -> None:
    units = [uuid.uuid4() for _ in range(3)]
    hits, truncated = assemble(
        [
            candidate(unit, uuid.uuid4(), f"g{index}", "en", 300, 0.5)
            for index, unit in enumerate(units)
        ],
        query_language="en",
        limit=5,
        max_tokens=700,
    )
    assert len(hits) == 2
    assert truncated


def test_the_limit_cuts_results_without_truncated() -> None:
    units = [uuid.uuid4() for _ in range(3)]
    hits, truncated = assemble(
        [
            candidate(unit, uuid.uuid4(), f"g{index}", "en", 10, 0.5)
            for index, unit in enumerate(units)
        ],
        query_language="en",
        limit=2,
        max_tokens=1000,
    )
    assert len(hits) == 2
    assert not truncated


def test_language_and_identifiers() -> None:
    assert detect_language("Quarterly access review") == "en"
    assert detect_language("每季度检查访问权限") == "zh"
    assert detect_language("访问 review of access") == "mixed"
    assert detect_language("123 !!!") == "und"
    assert extract_identifiers("See POL-01 and A.5.18, then 5.2.1 again.") == [
        "POL-01",
        "A.5.18",
        "5.2.1",
    ]
