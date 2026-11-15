"""Check a reading passage's prose against the source text it was copied from, letters only."""
from __future__ import annotations

import re
import unicodedata

__all__ = ["letters", "prose_sentences", "unmatched_prose"]


def letters(text: str) -> str:
    """The text NFKC-normalised, lowercased, with everything but a–z removed (spacing, hyphenation, punctuation)."""
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKC", text).lower())


def prose_sentences(markdown: str) -> list[str]:
    """A passage's prose sentences, with maths, figures, tables, footnote marks and bold subheadings taken out; citation links keep their text."""
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "|", markdown)
    md = re.sub(r"\$\$[\s\S]*?\$\$", "|", md)
    md = re.sub(r"\$[^$]*\$", "|", md)
    md = re.sub(r"\[([^\]]*)\]\(#ref:[^)]*\)", r"\1", md)
    md = re.sub(r"^\*\*[^*\n]+\*\*$", "|", md, flags=re.M)
    md = re.sub(r"^\|.*\|$", "|", md, flags=re.M)
    md = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]", "|", md)
    return [s.strip() for run in md.split("|") for s in re.split(r"(?<=[.;:!?])\s+", run) if s.strip()]


def unmatched_prose(markdown: str, source_text: str, *, min_letters: int = 12) -> list[str]:
    """The passage's prose sentences (of at least `min_letters` letters) not found letter-for-letter in `source_text`."""
    book = letters(source_text)
    return [s for s in prose_sentences(markdown) if len(letters(s)) >= min_letters and letters(s) not in book]
