"""Small text helpers: token counts, language, and identifiers. Pure functions."""

import math
import re

# CJK ideographs, Hiragana and Katakana. Each character is roughly one token.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+")
_IDENTIFIER = re.compile(
    r"\b[A-Z]{2,}-\d+(?:\.\d+)*"  # control references such as POL-01 or REG-2024
    r"|\b[A-Z]\.\d+(?:\.\d+)+"  # annex clauses such as A.5.18
    r"|\b\d+(?:\.\d+){2,}"  # numbered clauses such as 5.2.1
)


def count_tokens(text: str) -> int:
    """Approximate the token count of a text.

    The bge-m3 tokenizer needs the model files, which the unit tests do not have, so chunk sizes
    use this estimate: one token per CJK character and about 1.3 tokens per Latin word.
    """
    cjk = len(_CJK.findall(text))
    latin_words = len(_LATIN_WORD.findall(_CJK.sub(" ", text)))
    return cjk + math.ceil(latin_words * 1.3)


def detect_language(text: str) -> str:
    """Return `zh` for Chinese, `en` for Latin text, `mixed` when both are common, else `und`."""
    cjk = len(_CJK.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    letters = cjk + latin
    if letters == 0:
        return "und"
    if cjk / letters >= 0.5:
        return "zh"
    if cjk / letters >= 0.1:
        return "mixed"
    return "en"


def extract_identifiers(text: str) -> list[str]:
    """Doc numbers, clause and control references, and codes, in the order they first appear."""
    seen: dict[str, None] = {}
    for match in _IDENTIFIER.finditer(text):
        seen.setdefault(match.group(0), None)
    return list(seen)
