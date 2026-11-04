"""Parse Markdown, text and PDF into one common tree of sections and paragraphs.

Pure apart from reading the bytes it is given. A file that cannot be read raises ParseError, and
that file's batch fails, so nothing half-parsed reaches search.
"""

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pypdf import PdfReader

PARSEABLE_TYPES = frozenset({"text/plain", "application/pdf"})

# Numbering such as `5`, `5.2.1`, `A.5.18` or `第三章` starts a heading when the line is short.
_NUMBERED_HEADING = re.compile(
    r"^(?P<number>\d+(?:\.\d+)*|[A-Z]\.\d+(?:\.\d+)*|第[一二三四五六七八九十百\d]+章)\s+(?P<title>\S.*)$"
)
_MARKDOWN_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>\S.*?)\s*#*\s*$")
_MAX_HEADING_LENGTH = 120


class ParseError(Exception):
    """The file could not be read into text. The message says why, for the batch report."""


@dataclass(frozen=True)
class Section:
    heading_path: str
    locator: str
    paragraphs: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


@dataclass(frozen=True)
class DocumentTree:
    title: str
    sections: tuple[Section, ...]
    status: Literal["ok", "no_structure"]

    @property
    def text(self) -> str:
        return "\n\n".join(section.text for section in self.sections)


@dataclass(frozen=True)
class _Line:
    text: str
    locator: str


@dataclass
class _Heading:
    level: int
    title: str


@dataclass
class _OpenSection:
    heading_path: str
    locator: str
    paragraphs: list[str]


def parse_document(data: bytes, name: str, content_type: str) -> DocumentTree:
    """Read the bytes of one file into a document tree.

    Raises ParseError when that is not possible.
    """
    if content_type == "application/pdf":
        lines = _pdf_lines(data)
    elif content_type == "text/plain":
        lines = _text_lines(data)
    else:
        raise ParseError(f"Files of type {content_type} cannot be parsed yet.")
    if not any(line.text.strip() for line in lines):
        raise ParseError(
            "The file has no readable text. Scanned pages need OCR, which is not set up."
        )
    return _build_tree(lines, fallback_title=Path(name).stem or "Untitled")


def _text_lines(data: bytes) -> list[_Line]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ParseError("The file is not UTF-8 text.") from error
    return [_Line(line.rstrip(), "") for line in text.splitlines()]


def _pdf_lines(data: bytes) -> list[_Line]:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, 1)]
    except (
        Exception
    ) as error:  # the PDF parser raises many types on malformed input, all of them a failed file
        raise ParseError("The PDF could not be read.") from error
    lines: list[_Line] = []
    for number, page_text in pages:
        lines.extend(_Line(line.rstrip(), f"page {number}") for line in page_text.splitlines())
    return lines


def _heading_of(line: str) -> _Heading | None:
    markdown = _MARKDOWN_HEADING.match(line)
    if markdown is not None:
        return _Heading(len(markdown.group("hashes")), markdown.group("title"))
    numbered = _NUMBERED_HEADING.match(line)
    # A sentence that starts with a number is not a heading, so a heading never ends like one.
    if (
        numbered is not None
        and len(line) <= _MAX_HEADING_LENGTH
        and not line.endswith((".", ";", ",", "。"))
    ):
        number = numbered.group("number")
        level = number.count(".") + 1 if number[0].isdigit() or number[0].isupper() else 1
        return _Heading(level, line.strip())
    return None


def _build_tree(lines: list[_Line], *, fallback_title: str) -> DocumentTree:
    sections: list[_OpenSection] = []
    stack: list[_Heading] = []
    current = _OpenSection(heading_path="", locator=_first_locator(lines), paragraphs=[])
    sections.append(current)
    paragraph: list[str] = []
    has_heading = False
    title: str | None = None

    def flush() -> None:
        if paragraph:
            current.paragraphs.append("\n".join(paragraph))
            paragraph.clear()

    for line in lines:
        heading = _heading_of(line.text.strip()) if line.text.strip() else None
        if heading is not None:
            flush()
            has_heading = True
            while stack and stack[-1].level >= heading.level:
                _ = stack.pop()
            stack.append(heading)
            if title is None:
                title = heading.title
            path = " > ".join(item.title for item in stack)
            current = _OpenSection(heading_path=path, locator=line.locator, paragraphs=[])
            sections.append(current)
            current.paragraphs.append(heading.title)
        elif line.text.strip():
            paragraph.append(line.text.strip())
        else:
            flush()
    flush()

    kept = tuple(
        Section(section.heading_path, section.locator, tuple(section.paragraphs))
        for section in sections
        if section.paragraphs
    )
    return DocumentTree(
        title=title or fallback_title,
        sections=kept,
        status="ok" if has_heading else "no_structure",
    )


def _first_locator(lines: list[_Line]) -> str:
    return lines[0].locator if lines else ""
