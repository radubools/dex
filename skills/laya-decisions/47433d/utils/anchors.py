"""Walk a criterion's chunks by score until one contains the words it asked about.

A decision model answers whatever it is handed, so a high probability is not
evidence that the passage says the thing.  On one lender's construction
analysis the top-ranked criterion of a round scored 0.968 on the paragraph
describing a roof assembly, and a later round measured the size of the problem
directly: nine criteria asked about causes the document never mentions —
injury, lien, litigation, mold — and one of them reached 0.900.

The fix is to stop asking the model whether it was right.  Write down the
literal phrases the text would have to use for a criterion to be about
anything, then check the passage the model actually chose.  That is a string
search: it owes nothing to the model and a reader can repeat it by eye.

**This is the join between two modules that already exist.**  `cite.holders`
finds the chunk holding a phrase when you know which phrase you want;
`unlabelled.top_chunks` ranks chunks by what a criterion scored on them,
knowing nothing about what they should say.  Neither answers the question that
matters — *how far down its own ranking is the first chunk that contains the
subject?*  `best` walks the ranking and reports it:

    at = anchors.best(answers["no_permit_issued"], ids, chunks,
                      ["no permit issued"])
    at["anchored"]   # True: the model's own peak chunk contains the phrase
    at["rank"]       # 1 — where the first chunk that does sat in the ranking
    at["quote"]      # the sentence, via cite.window

Rank 1 means the model pointed where a reader would.  Rank 20 means the
document supports the criterion and the model found it somewhere else, which
is worth reporting as a different thing rather than as a failure.

Two differences from `cite.holders`, both because anchors are written by hand
against extracted PDFs.  Matching folds case, since a report capitalises
mid-sentence — "Add $60", "A code compliance review".  And a criterion carries
several phrases rather than one, because a report words a thing more than one
way; they are alternatives, so a dead one is not a fault and `unmatched` is
there to list them.

Nothing here decides anything.  Whether an unanchored peak disqualifies a
criterion or merely annotates it is the caller's policy.
"""

from __future__ import annotations

from typing import Any


def _sibling(name: str) -> Any:
    """Import a module sitting beside this one, package-relative or flat."""
    try:
        return __import__(f"{__package__}.{name}", fromlist=[name]) if __package__ \
            else __import__(name)
    except ImportError:
        return __import__(name)


def _cite() -> Any:
    """`cite`, which does the matching and the excerpting."""
    try:
        return _sibling("cite")
    except ImportError as exc:  # pragma: no cover - depends on what is enabled
        raise RuntimeError(
            "anchors needs utils/cite.py, which is not beside it. Both ship "
            "with the laya-decisions skill; if only one was materialised, "
            "re-enable the skill."
        ) from exc


def hits(text: str, phrases: list[str]) -> list[str]:
    """Which of `phrases` occur in `text`, on collapsed whitespace, folding case.

    Collapsing first is what makes this work at all: `pdftotext -layout` wraps
    mid-phrase and indents the continuation, so "A code compliance\\n    review
    is not within our scope" does not contain the literal "code compliance
    review" — which silently unanchored three criteria on the document this
    came from while the text said exactly what they asked about.
    """
    cite = _cite()
    low = cite.flatten(text).lower()
    return [p for p in phrases if cite.flatten(p).lower() in low]


def ranked(answers_for_q: dict, ids: list[str]) -> list[tuple[float, str]]:
    """`(verdict, chunk id)` over `ids`, strongest first.

    `answers_for_q` is one criterion's `{chunk id: laya.detail row}`.
    """
    return sorted(
        ((float(answers_for_q[c]["verdict"]), c)
         for c in ids if c in answers_for_q),
        reverse=True)


def best(answers_for_q: dict, ids: list[str], chunks: dict,
         phrases: list[str], *, width: int | None = None) -> dict[str, Any]:
    """The strongest chunk for one criterion that actually contains its words.

    `chunks` is a `sweep` chunk store's inner mapping — `{id: {"text": ...}}`.
    Returns `peak` and `peak_chunk`, the strongest chunk whether or not it is
    anchored; `anchored`, whether that one was; and for the best one that was,
    `chunk`, `verdict`, `rank`, `matched` and `quote`.  `width` passes through
    to `cite.window`.
    """
    cite = _cite()
    order = ranked(answers_for_q, ids)
    if not order:
        return {"peak": 0.0, "peak_chunk": None, "anchored": False,
                "chunk": None, "verdict": None, "rank": None,
                "matched": [], "quote": None}
    peak, peak_chunk = order[0]
    out: dict[str, Any] = {
        "peak": round(peak, 4),
        "peak_chunk": peak_chunk,
        "anchored": bool(hits(chunks[peak_chunk]["text"], phrases)),
        "chunk": None, "verdict": None, "rank": None,
        "matched": [], "quote": None,
    }
    for rank, (verdict, cid) in enumerate(order, 1):
        matched = hits(chunks[cid]["text"], phrases)
        if not matched:
            continue
        # `window` needs the phrase as the text spells it; `hits` matched with
        # case folded, so recover the text's own casing before quoting.
        flat = cite.flatten(chunks[cid]["text"])
        needle = cite.flatten(matched[0])
        at = flat.lower().find(needle.lower())
        kwargs = {"width": width} if width is not None else {}
        out |= {
            "chunk": cid,
            "verdict": round(verdict, 4),
            "rank": rank,
            "matched": matched,
            "quote": cite.window(
                chunks[cid]["text"], flat[at:at + len(needle)], **kwargs),
        }
        break
    return out


def check(answers: dict, ids: list[str], chunks: dict,
          phrases: dict[str, list[str]], **kwargs: Any) -> dict[str, dict]:
    """`best` for every criterion in `phrases` that was answered."""
    return {qid: best(answers[qid], ids, chunks, wanted, **kwargs)
            for qid, wanted in phrases.items() if qid in answers}


def unmatched(chunks: dict, phrases: dict[str, list[str]]) -> dict[str, list[str]]:
    """Per criterion, the phrases no chunk contains — anchors written against air.

    `cite.missing` answers this for a flat list matched as written; this keeps
    the criterion each phrase belongs to and folds case the way `hits` does, so
    what it reports is what the walk will actually fail to find.  A criterion
    with some phrases listed is fine — they are alternatives.  One with *all*
    of them listed can never anchor, and is the case worth acting on.
    """
    cite = _cite()
    flat = [cite.flatten(c["text"]).lower() for c in chunks.values()]
    out = {}
    for qid, wanted in phrases.items():
        gone = [p for p in wanted
                if not any(cite.flatten(p).lower() in t for t in flat)]
        if gone:
            out[qid] = gone
    return out
