"""Find the chunk holding a known sentence, and excerpt around it.

Scoring a criterion tells you how strongly the model answered. It does not
tell you whether the model answered strongly about *the passage a person would
have pointed at*, and those come apart. On one lender's construction analysis,
five of ten criteria that separated cleanly from their own denials — a real
separation by `unlabelled.polarity`, up to 0.85 against a 0.66 ceiling — peaked
on a segment other than the sentence that supports them. Separating and
pointing are two different failures, and only the second one is visible to a
reader, because the quote is what ends up in the report.

Measuring it needs a known sentence to check against. That part is a judgement
and stays with the caller: write down the phrase you would cite, and these two
functions turn it into something mechanical.

    from utils import cite, unlabelled

    held = cite.holders(chunks, "Fire Sprinkler plans were not provided")
    peak, p = unlabelled.top_chunks(answers["fire_sprinkler_plans_absent"], n=1)[0]
    on_target = peak in held                       # the caller's call, not ours
    quote = cite.window(chunks[peak]["text"], "Fire Sprinkler plans were not provided")

**Match on collapsed whitespace, which is the whole reason `holders` exists.**
`pdftotext -layout` breaks lines inside sentences and pads them into columns,
so a phrase that is plainly on the page is not a substring of the extracted
text: `"Fire Sprinkler plans were not provided" in chunk["text"]` is `False`
for the chunk that contains it. Both functions flatten before matching, so the
phrase you write is the phrase as it reads, not as it was laid out.

**`window` exists because a chunk is bigger than a sentence.** At a 400-token
budget a segment runs to a couple of thousand characters, and quoting its head
prints the right segment with the wrong words in it — which reads as a miss
when it is not one. Centring on the phrase is what makes the quote evidence
rather than decoration.

Neither function decides anything. `holders` returns ids, `window` returns a
string, the widths are keyword defaults, and whether a peak outside the
holders disqualifies a criterion or merely annotates it is the caller's policy.

**When there is no known sentence**, two more functions cover the other
direction. `best_sentence(chunk, question)` picks the line of a chunk sharing
most words with the question and returns *how many* it shared, so a caller can
tell a real match from a chunk that merely carries the topic and decline to
quote it — the same on-target/off-target distinction as `holders`, for a
criterion nobody has written a citation for yet. `page_of(document, chunk)`
gives the page a chunk begins on, for citing it as "page 10" rather than as an
opaque hash; it needs the extractor to have marked page boundaries, and takes
the pattern as an argument for extractors that mark them differently.

Both match on collapsed whitespace for the same reason everything else here
does — `laya.split` normalises it, so a segment need not be a literal substring
of the text it was cut from. On one 55-segment report, 29 were not.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Characters of context `window` returns by default. Roughly a paragraph:
#: enough to read the cited sentence in its surroundings, short enough to sit
#: in a table cell or a bullet.
WIDTH = 420

#: Where in the window the phrase sits, as a fraction from the left. A third
#: of the way in leaves the sentence preceded by enough to place it and
#: followed by enough to finish the thought.
LEAD = 1 / 3

#: What `window` puts where it cut. Set it to "" for a quote that will be
#: processed further rather than read.
ELLIPSIS = "..."


def flatten(text: str) -> str:
    """`text` with every run of whitespace collapsed to one space.

    This is what makes a phrase matchable against text a PDF extractor laid
    out in columns: newlines inside a sentence and the padding between columns
    both become single spaces.
    """
    return " ".join(text.split())


def holders(chunks: dict, phrase: str) -> set:
    """Ids of the chunks whose text contains `phrase`, matched on collapsed whitespace.

    Takes a `sweep` chunk store's inner mapping — `{id: {"text": ...}}`, i.e.
    `load_chunks(path)["chunks"]`.

    Returns a set rather than a single id because a report often makes the same
    point twice, and because a phrase that lands in no chunk and a phrase that
    lands in three are different problems for the caller to notice. An empty
    set means the phrase is not in this text at all — usually a stale citation
    after the extraction changed, which is worth raising rather than scoring.
    """
    return {cid for cid, c in chunks.items() if phrase in flatten(c["text"])}


def locate(chunks: dict, phrases: dict) -> dict:
    """`holders` for each `{key: phrase}`, as `{key: set of ids}`.

    Convenience for the usual shape — one cited sentence per criterion — so a
    caller with a table of them does not write the loop. Keys whose phrase
    matches nothing come back with an empty set rather than being dropped, so
    a stale citation stays visible.
    """
    return {key: holders(chunks, phrase) for key, phrase in phrases.items()}


def window(
    text: str,
    phrase: str | None = None,
    *,
    width: int = WIDTH,
    lead: float = LEAD,
    ellipsis: str = ELLIPSIS,
) -> str:
    """`width` characters of `text`, collapsed, centred on `phrase` if it is there.

    With no `phrase`, or one this text does not contain, returns the opening
    `width` characters — the sensible fallback for a chunk that is being quoted
    because it scored well rather than because it was cited.

    `ellipsis` marks each end that was cut, and only the ends that were: a
    window reaching the start of the text is not prefixed, and one reaching the
    end is not suffixed, so a short chunk comes back whole and unmarked.
    """
    flat = flatten(text)
    at = flat.find(phrase) if phrase else -1
    if at < 0:
        return flat[:width] + (ellipsis if len(flat) > width else "")
    start = max(0, at - int(width * lead))
    end = min(len(flat), start + width)
    return ((ellipsis if start else "")
            + flat[start:end]
            + (ellipsis if end < len(flat) else ""))


def missing(chunks: dict, phrases: Iterable[str]) -> list:
    """The `phrases` that no chunk contains — stale citations, in order given.

    Worth calling before scoring anything: a phrase that matches nothing
    silently makes every criterion built on it look off-target, and the cause
    is almost always that the extraction changed under a citation written
    against an older one.
    """
    return [p for p in phrases if not holders(chunks, p)]


# --------------------------------------------------------------------------
# No known sentence: pick a line, and say which page it came from.

#: How a page boundary is marked in extracted text. A caller whose extractor
#: marks them differently passes its own; the capture group is the page number.
PAGE_MARKER = r"\[page (\d+)\]"

#: Leading characters of a chunk used to locate it in its document. Long enough
#: to be unique, short enough that a shorter chunk still resolves.
PROBE = 200

#: Words shorter than this carry no signal for matching a line to a question.
MIN_WORD = 4

#: A line shorter than this is a heading or a fragment, not a quotable sentence.
MIN_WORDS = 4

_WORD = re.compile(r"[a-z]{%d,}" % MIN_WORD)
_SENTENCE = re.compile(r"(?<=[.;:])\s+|\n\s*[•\n]")


def page_marks(document: str, *, marker: str = PAGE_MARKER) -> dict:
    """Offset in the *flattened* document -> the page number in force from there.

    Offsets index the flattened string, not the original, so pass them only to
    things that flatten too — which is everything in this module.
    """
    return {m.start(): int(m.group(1))
            for m in re.finditer(marker, flatten(document))}


def locate_in(flat_document: str, chunk: str, *, probe: int = PROBE):
    """Offset of `chunk` within an already-flattened document, or None."""
    at = flat_document.find(flatten(chunk)[:probe])
    return None if at < 0 else at


def page_of(document: str, chunk: str, *, marks: dict | None = None,
            marker: str = PAGE_MARKER, probe: int = PROBE):
    """The page `chunk` begins on, from the nearest preceding marker, or None.

    Pass `marks` from `page_marks` when resolving many chunks against one
    document; otherwise this recomputes them on every call. None means the
    chunk was not found, which is a different thing from page 1 and is left for
    the caller to notice.
    """
    flat = flatten(document)
    marks = page_marks(document, marker=marker) if marks is None else marks
    at = locate_in(flat, chunk, probe=probe)
    if at is None:
        return None
    earlier = [off for off in marks if off <= at]
    return marks[max(earlier)] if earlier else None


def words(text: str) -> set:
    """The distinctive words of `text`, lowercased — the unit overlap is counted in."""
    return set(_WORD.findall(text.lower()))


def sentences(chunk: str, *, min_words: int = MIN_WORDS) -> list:
    """`chunk` split into quotable lines, each flattened, fragments dropped."""
    out = []
    for raw in _SENTENCE.split(chunk):
        sentence = flatten(raw)
        if len(sentence.split()) >= min_words:
            out.append(sentence)
    return out


def best_sentence(chunk: str, question: str, *,
                  min_words: int = MIN_WORDS) -> tuple:
    """The line in `chunk` sharing most words with `question`, and how many it shared.

    The count is the point, and it is why this returns a pair rather than a
    string. A lexical pick is right often enough to be worth showing and wrong
    often enough that the caller has to be able to tell: an overlap of 0 or 1
    usually means the chunk carries the question's topic but not its claim, and
    printing that line as the criterion's evidence would misrepresent it. What
    counts as enough overlap is the caller's threshold, not this function's.

    Returns `("", 0)` when no line is long enough to quote.
    """
    wanted = words(question)
    best, score = "", 0
    for sentence in sentences(chunk, min_words=min_words):
        overlap = len(wanted & words(sentence))
        if overlap > score or not best:
            best, score = sentence, overlap
    return best, score
