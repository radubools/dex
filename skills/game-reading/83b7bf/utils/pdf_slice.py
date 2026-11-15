"""Slice a reading's prose out of a PDF's own text by start and end phrases, so passages are never retyped."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from . import reading as rd

LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
MATHISH = re.compile(r"[^\x00-\x7f’“”—–…]+|(?<![\w$\\{])[A-Za-z]\d*(?![\w$}])|\s[,.)]\s")


@lru_cache(maxsize=None)
def page_text(pdf: str | Path, page: int) -> str:
    """A PDF page's text (1-based PDF page), NFC-normalised with ligatures undone; line breaks kept."""
    t = unicodedata.normalize("NFC", rd.pdf_text(pdf, page))
    for a, b in LIGATURES.items():
        t = t.replace(a, b)
    return t


def hyphenated_words(texts) -> frozenset:
    """The lowercased hyphenated words ("least-squares") printed within a line anywhere in `texts`."""
    words = set()
    for t in texts:
        words.update(w.lower() for w in re.findall(r"[A-Za-z]+-[A-Za-z]+", t))
    return frozenset(words)


def join_lines(text: str, keep_hyphen: frozenset = frozenset()) -> str:
    """Text as one line: end-of-line hyphens dropped unless the word is in `keep_hyphen`, dashes rejoined."""
    def dehyph(m):
        a, b = m.group(1), m.group(2)
        return f"{a}-{b}" if f"{a}-{b}".lower() in keep_hyphen else a + b
    t = re.sub(r"([A-Za-z]+)-\n\s*([a-z]+)", dehyph, text)
    t = re.sub(r"([—–])\s*\n\s*", r"\1", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def between(text: str, start: str, end: str) -> str:
    """`text` from the first `start` through the next `end`, both included; ValueError naming the one missing."""
    i = text.find(start)
    if i < 0:
        raise ValueError(f"start not found: {start!r}")
    j = text.find(end, i)
    if j < 0:
        raise ValueError(f"end not found after start: {end!r}")
    return text[i:j + len(end)]


def substitute(text: str, subs) -> str:
    """Apply exact (old, new) replacements in order; ValueError if an `old` is absent, so stale fixes surface."""
    for a, b in subs:
        if a not in text:
            raise ValueError(f"substitution source not found: {a!r}")
        text = text.replace(a, b)
    return text


def mathish_fragments(text: str, *, pad: int = 10, merge: int = 12, ignore=("a", "A", "I")) -> list[str]:
    """Windows around what still looks like extracted maths outside $…$ and #ref: links (close hits merged)."""
    plain = re.sub(r"\$[^$]*\$|\[[^\]]*\]\(#ref:[^)]*\)", lambda m: "\0" * len(m.group()), text)
    spans: list[list[int]] = []
    for m in MATHISH.finditer(plain):
        if m.group().strip() in ignore:
            continue
        if spans and m.start() - spans[-1][1] < merge:
            spans[-1][1] = m.end()
        else:
            spans.append([m.start(), m.end()])
    return [text[max(0, a - pad):b + pad] for a, b in spans]
