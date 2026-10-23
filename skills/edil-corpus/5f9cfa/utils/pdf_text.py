"""Extract a PDF's text page by page with two engines and keep the one that reads as words.

`pypdf` and `pdftotext -layout` fail on different eDIL documents: pypdf drops the
column layout of a cost table, pdftotext doubles characters on some two-column
pages, and both return OCR soup for a scan. Both run; the one with the most words
*whose output still reads as words* wins.

Some eDIL PDFs embed a font whose glyphs sit a fixed number of code points away
from the character they draw — "Wkh#surmhfw" for "The project", or "3HUPLWV" for
"Permits" with every space a U+0003. Each known offset is tried per page, in both
directions, and kept only when the page then reads as English.

    from utils import pdf_text
    result = pdf_text.extract(Path("....pdf"))
    result["pages"]      # [{"page": 1, "text": ..., "words": ..., "junk_share": ..., "is_text": ...}]
    result["engine"], result["engines"], result["font_offset_shifted_pages"]
    pdf_text.document(result["pages"])   # one string with "[page N]" markers
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PDFTOTEXT = "/opt/homebrew/bin/pdftotext"

#: How far the broken font's glyphs sit below the characters they draw.
FONT_OFFSET = 29

#: An engine's output is "words" when at least this share of its alphabetic runs
#: are three letters or longer. Character soup from a scan falls well under it.
WORDINESS = 0.6

#: A page whose words are fragments this often is a scan, not text. Taken from
#: `laya-decisions`' evidence module, where scanned floor plans sat at 0.54-0.78
#: and every page of narrative at 0.24-0.38.
JUNK_PAGE = 0.45

_LETTERS = re.compile(r"[^A-Za-z]")
_MIXED = re.compile(r"[A-Za-z][0-9]|[0-9][A-Za-z]")


def wordiness(text: str) -> float:
    """Share of a text's alphabetic runs that are three letters or longer."""
    runs = re.findall(r"[A-Za-z]+", text)
    return sum(len(r) >= 3 for r in runs) / len(runs) if runs else 0.0


def junk_share(text: str) -> float:
    """Share of words that are fragments: under three letters, or letters mixed with digits."""
    words = text.split()
    if not words:
        return 1.0
    return sum(1 for w in words if len(_LETTERS.sub("", w)) < 3 or _MIXED.search(w)) / len(words)


#: Common English words. A page whose tokens are almost never one of these is
#: not English, whatever its letter runs look like: the offset font's output
#: ("3HUPLWV" for "Permits") is long alphabetic runs and fools `wordiness`.
_COMMON = frozenset("""the and of to in for is on that by this with be are as at or from
it an was will not have has been which all its their than any per our these other
such may cost costs project contract budget construction total work""".split())


def english_share(text: str) -> float:
    """Share of a text's words that are common English words."""
    words = re.findall(r"[A-Za-z]+", text)
    return sum(w.lower() in _COMMON for w in words) / len(words) if words else 0.0


def _shift(text: str, by: int) -> str:
    """Move every character that a `by`-offset font could have produced back by `by`.

    `by` > 0: glyphs sit `by` code points *above* what they draw ("Wkh#surmhfw").
    `by` < 0: they sit below — the space comes out as U+0003 and "Permits" as "3HUPLWV".
    """
    lo, hi = 0x20 + by, 0x7F + by
    # Whitespace the extractor laid out itself is real, not shifted: without
    # this, every layout space on a -29 page becomes "=".
    return "".join(chr(ord(c) - by) if lo <= ord(c) < hi and not c.isspace() else c for c in text)


#: Offsets seen in the eDIL export, in both directions.
OFFSETS = (FONT_OFFSET, -FONT_OFFSET, 3)


def unshift(text: str) -> tuple[str, bool]:
    """A page's text with a font offset undone, and whether undoing it helped.

    Each known offset is tried and the result scored by `english_share`; the
    best one is kept only if it reads clearly more like English than the page
    did. Undoing an offset on a page that never had one makes it worse, so a
    clean page is left alone.
    """
    if not text.strip():
        return text, False
    base = english_share(text)
    best, best_share = text, base
    for by in OFFSETS:
        fixed = _shift(text, by)
        share = english_share(fixed)
        if share > best_share:
            best, best_share = fixed, share
    if best is not text and best_share >= base + 0.05 and best_share >= 0.08:
        return best, True
    return text, False


def by_pypdf(pdf: Path) -> list[str]:
    from pypdf import PdfReader

    return [(page.extract_text() or "") for page in PdfReader(str(pdf)).pages]


def by_pdftotext(pdf: Path, *, timeout: float = 300) -> list[str]:
    out = subprocess.run(
        [PDFTOTEXT, "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=timeout
    )
    out.check_returncode()
    pages = out.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def score(pages: list[str]) -> dict:
    text = "\n".join(pages)
    return {"pages": len(pages), "words": len(text.split()), "wordiness": round(wordiness(text), 3)}


def extract(pdf: Path) -> dict:
    """Both engines' text for `pdf`, the winner chosen, per-page quality attached.

    An engine that raises (truncated or encrypted file) is recorded under
    `errors` and scores zero, rather than failing the document.
    """
    engines: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for name, fn in (("pypdf", by_pypdf), ("pdftotext -layout", by_pdftotext)):
        try:
            engines[name] = fn(pdf)
        except Exception as exc:  # noqa: BLE001 - a broken PDF is a fact to record
            engines[name] = []
            errors[name] = f"{type(exc).__name__}: {exc}"[:500]

    scores = {name: score(pages) for name, pages in engines.items()}
    winner = max(scores, key=lambda n: (scores[n]["wordiness"] >= WORDINESS, scores[n]["words"]))

    pages, shifted = [], []
    for i, raw in enumerate(engines[winner], start=1):
        text, did = unshift(raw)
        if did:
            shifted.append(i)
        share = junk_share(text)
        pages.append({
            "page": i,
            "text": text,
            "words": len(text.split()),
            "junk_share": round(share, 4),
            "is_text": share < JUNK_PAGE,
            "font_offset_shifted": did,
        })
    return {
        "pdf": str(pdf),
        "engine": winner,
        "engines": scores,
        "errors": errors,
        "font_offset_shifted_pages": shifted,
        "pages": pages,
    }


def document(pages: list[dict]) -> str:
    """The pages joined into one text, each preceded by a `[page N]` marker."""
    return "\n".join(f"[page {p['page']}]\n{p['text'].rstrip()}" for p in pages) + "\n"
