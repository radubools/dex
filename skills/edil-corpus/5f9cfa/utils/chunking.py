"""Cut extracted pages into token-budgeted chunks and flag the ones worth asking about.

Chunks are packed greedily from paragraphs, then sentences, then word runs, and
**remember the pages they came from** — the atoms are cut per page, so a chunk's
`pages` is exact rather than located after the fact by string search.

The budget is counted with the evaluator's tokenizer (Clef-flash's Qwen3.5
vocabulary by default), because that is the model that pays for every token:
each criterion asked costs ~100 schema tokens per chunk on top of the chunk
itself, so the chunk size is a throughput decision as much as a granularity one.

    from utils import chunking
    chunks = chunking.split_pages(pages, budget=400)
    chunking.flag(chunks)              # adds prose_ratio, digit_share, ..., "usable"

The quality filters are the ones `laya-decisions`' evidence module measured on
this corpus — function-word ratio, digit share, OCR garble, near-duplicate forms —
copied here so this skill stands on its own. Thresholds are keyword arguments;
the defaults are the tightened values that corpus needed (`ratio=0.06`,
`digits=0.22`).
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from typing import Callable

#: Where the default tokenizer comes from: the evaluator's own vocabulary.
TOKENIZER_REPO = "mlx-community/clef-flash-8bit"

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

FUNCTION_WORDS = re.compile(
    r"\b(the|and|for|with|this|that|is|are|was|were|has|have|had|been|will|be"
    r"|at|on|of|to|in|by|from|not|all|as|it|its|they|we|our|their|there|which"
    r"|when|per|than|into|over|still|now|also|any|no)\b",
    re.IGNORECASE,
)
#: eDIL cost takeoffs read as prose at the generic 0.035: the Note column is
#: sentences. 0.06 is what this corpus needed.
PROSE_RATIO = 0.06
DIGIT_SHARE = 0.22
GARBLE = 0.05
DUPLICATE_OVERLAP = 0.6
DUPLICATE_GROUP = 5
#: A chunk is from text pages when at least this share of its atoms are.
TEXT_PAGE_SHARE = 0.9

_GARBLE_WORD = re.compile(r"[A-Za-z][A-Za-z'’]{3,}")
_VOWEL = re.compile(r"[aeiouyAEIOUY]")


@lru_cache(maxsize=4)
def _tokenizer(repo: str = TOKENIZER_REPO):
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    return Tokenizer.from_file(hf_hub_download(repo, "tokenizer.json"))


def token_counter(repo: str = TOKENIZER_REPO) -> Callable[[str], int]:
    tok = _tokenizer(repo)
    return lambda text: len(tok.encode(text, add_special_tokens=False).ids)


def chunk_id(document_id: str, text: str) -> str:
    """16 hex characters of sha256 over the document id and the chunk's text."""
    return hashlib.sha256(f"{document_id}\0{text}".encode()).hexdigest()[:16]


def _atoms(page_text: str, budget: int, count: Callable[[str], int]):
    """A page's paragraphs, any over-long paragraph cut to sentences, any over-long sentence to word runs."""
    for paragraph in _PARAGRAPH.split(page_text):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        n = count(paragraph)
        if n <= budget:
            yield paragraph, n, "\n\n"
            continue
        for sentence in _SENTENCE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            n = count(sentence)
            if n <= budget:
                yield sentence, n, " "
                continue
            run, run_n = "", 0
            for word in sentence.split():
                wn = count(word) + 1
                if run and run_n + wn > budget:
                    yield run, run_n, " "
                    run, run_n = "", 0
                run = word if not run else run + " " + word
                run_n += wn
            if run:
                yield run, run_n, " "


def split_pages(pages: list[dict], budget: int = 400, *,
                count: Callable[[str], int] | None = None) -> list[dict]:
    """Pack the pages' atoms into chunks of at most ~`budget` tokens.

    `pages` is `pdf_text.extract(...)["pages"]`. Token counts are summed per atom
    plus one per join, which over-counts slightly and never under-counts by more
    than a join's worth. Returns chunks in document order:
    `{"ord", "text", "tokens", "pages", "text_page_share"}`.
    """
    count = count or token_counter()
    chunks: list[dict] = []
    parts: list[str] = []
    seps: list[str] = []
    n = 0
    owners: list[tuple[int, bool]] = []

    def flush() -> None:
        nonlocal parts, seps, n, owners
        if parts:
            text = parts[0] + "".join(s + p for s, p in zip(seps, parts[1:]))
            chunks.append({
                "ord": len(chunks),
                "text": text,
                "tokens": n,
                "pages": sorted({p for p, _ in owners}),
                "text_page_share": round(sum(t for _, t in owners) / len(owners), 3),
            })
        parts, seps, n, owners = [], [], 0, []

    for page in pages:
        for atom, an, sep in _atoms(page["text"], budget, count):
            if parts and n + an + 1 > budget:
                flush()
            if parts:
                seps.append(sep)
                n += 1
            parts.append(atom)
            n += an
            owners.append((page["page"], bool(page.get("is_text", True))))
    flush()
    return chunks


def prose_ratio(text: str) -> float:
    words = text.split()
    return len(FUNCTION_WORDS.findall(text)) / len(words) if words else 0.0


def digit_share(text: str) -> float:
    ink = [c for c in text if not c.isspace()]
    return sum(c.isdigit() or c in ".,$%()-" for c in ink) / len(ink) if ink else 1.0


def garble_share(text: str) -> float:
    """Share of four-letter-plus words shaped like OCR: no vowel, or a lower-then-upper inside."""
    words = _GARBLE_WORD.findall(text)
    if not words:
        return 0.0
    bad = sum(
        1 for w in words
        if not _VOWEL.search(w) or any(w[i].islower() and w[i + 1].isupper() for i in range(len(w) - 1))
    )
    return bad / len(words)


def _shingle(text: str) -> frozenset:
    return frozenset(re.findall(r"[a-z]{4,}", text.lower()))


def duplicates(texts: list[str], *, threshold: float = DUPLICATE_OVERLAP,
               group: int = DUPLICATE_GROUP) -> set[int]:
    """Indexes of texts in a near-identical set of `group` or more (a pre-printed form)."""
    sh = [_shingle(t) for t in texts]
    parent = list(range(len(texts)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(sh):
        if not a:
            continue
        for j in range(i + 1, len(sh)):
            b = sh[j]
            if b and len(a & b) / len(a | b) >= threshold:
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[ra] = rb
    sizes: dict[int, int] = {}
    for i in range(len(texts)):
        sizes[find(i)] = sizes.get(find(i), 0) + 1
    return {i for i in range(len(texts)) if sizes[find(i)] >= group}


def flag(chunks: list[dict], *, ratio: float = PROSE_RATIO, digits: float = DIGIT_SHARE,
         garble: float = GARBLE, text_share: float = TEXT_PAGE_SHARE) -> list[dict]:
    """Attach the quality measurements and a `usable` verdict to each chunk, in place.

    `usable` = prose, not a grid of figures, not OCR garble, mostly from text
    pages, and not one of a repeated form. Nothing is dropped: downstream stages
    choose whether to read only usable chunks.
    """
    repeated = duplicates([c["text"] for c in chunks])
    for i, c in enumerate(chunks):
        c["prose_ratio"] = round(prose_ratio(c["text"]), 4)
        c["digit_share"] = round(digit_share(c["text"]), 4)
        c["garble_share"] = round(garble_share(c["text"]), 4)
        c["repeated_form"] = i in repeated
        c["usable"] = (
            c["prose_ratio"] >= ratio
            and c["digit_share"] <= digits
            and c["garble_share"] < garble
            and c["text_page_share"] >= text_share
            and not c["repeated_form"]
        )
    return chunks
