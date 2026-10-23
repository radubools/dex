"""Pick the chunks worth asking about — prose, not a repeated form, not a grid of figures.

A decision model answers whatever you hand it. It has no way to say "this is not
text", so a page of column headings and a page of a pre-printed form come back
with the same confident probability as a sentence — and on a document that is
mostly forms, the strongest answer to every question is boilerplate and the
maximum ranks nothing.

The case this was written from: a 221-page lender's progress report, cut into 458
chunks, asked 30 yes/no questions. Twenty-two of the 30 peaked at exactly 1.00,
all on the same handful of chunks — an AIA G702 continuation sheet, and a
lien-waiver form that appears 84 times with a different subcontractor's name on
it. That form is written in precisely the vocabulary the questions asked about —
*rights*, *disputed*, *claims*, *has not been paid* — so it scored high on almost
everything while saying nothing about the project. Filtering to 29 chunks left
probabilities that discriminated, and the same 30 questions then ranked in an
order that matched the document.

Four filters, each answering a different question about a chunk, and each one
here because the others let something through:

- **`prose_ratio`** — the share of words that are English function words. A
  schedule of values is nouns and numbers with almost no `the`, `of`, `has
  been`; narrative is full of them. This separates a sentence from a table.
- **`digit_share`** — how much of the chunk is digits and money punctuation.
  This is the only one that tells a *log* — headings, a description column and
  figures, worth quoting — from a *continuation sheet* carrying the same
  headings and nothing but figures.
- **`duplicates`** — near-identical chunks by Jaccard overlap on word sets,
  single-link clustered, for finding pre-printed forms.
- **`drop_decoded`**, in `core()` — skips chunks whose store entry carries
  `"decoded": True`. Optional convention, for a caller that repaired mangled
  pages during extraction: text recovered from a broken font encoding has
  readable headings and nonsense where its digits were, so it passes every
  textual test here while being unquotable. Set the key when you extract, or
  ignore it.

`core()` composes all four and returns ids. `rank()` orders one criterion's
answers over a set of ids.

Nothing here decides anything or formats anything. Every function returns ids or
a number, every threshold is a keyword argument with a default, and which
threshold suits a corpus is the caller's call — the defaults came from one
document and are a starting point, not a rule. Measure yours: print
`prose_ratio` and `digit_share` across a sample before trusting either.
"""

import re

#: Function words common to any English prose and rare in a spreadsheet dump.
FUNCTION_WORDS = re.compile(
    r"\b(the|and|for|with|this|that|is|are|was|were|has|have|had|been|will|be"
    r"|at|on|of|to|in|by|from|not|all|as|it|its|they|we|our|their|there|which"
    r"|when|per|than|into|over|still|now|also|any|no)\b",
    re.IGNORECASE,
)

#: A chunk counts as readable at or above this share of function words. Two
#: tiers were useful on the document this came from and the caller picks by
#: passing `ratio`: 0.035, the default, is where a table with a real description
#: column still reads as something a person can quote; 0.10 keeps only
#: continuous narrative, which is what a supporting quote usually wants. For
#: scale, that document's median chunk sat at 0.051 and its narrative at 0.12+.
PROSE_RATIO = 0.035

#: Above this share of digits and money punctuation a chunk is a grid of
#: figures, whatever its headings say. On the source document the change-order
#: logs sat at 0.10-0.30 and the continuation and draw sheets at 0.39-0.65, so
#: the gap was wide and the threshold is not delicately placed.
DIGIT_SHARE = 0.35

#: Two chunks are near-duplicates at or above this Jaccard overlap of their
#: distinctive-word sets.
DUPLICATE_OVERLAP = 0.6

#: A near-duplicate set this large or larger is a pre-printed form, not a
#: passage that happens to resemble another.
DUPLICATE_GROUP = 5


def prose_ratio(text: str) -> float:
    """The share of `text`'s words that are English function words, 0.0 to 1.0."""
    words = text.split()
    return len(FUNCTION_WORDS.findall(text)) / len(words) if words else 0.0


def is_prose(text: str, ratio: float = PROSE_RATIO) -> bool:
    """True when `text` reads as sentences rather than as a table of values."""
    return prose_ratio(text) >= ratio


def digit_share(text: str) -> float:
    """The share of `text`'s non-space characters that are digits or money marks.

    `prose_ratio` says whether a chunk has sentences in it; this says whether it
    is mostly numbers. A log has both — headings, a description column and
    figures — and only this test tells it apart from a continuation sheet, which
    has the same headings and nothing else.
    """
    ink = [c for c in text if not c.isspace()]
    return sum(c.isdigit() or c in ".,$%()-" for c in ink) / len(ink) if ink else 1.0


def _shingle(text: str) -> frozenset:
    """A chunk's distinctive words: lower-cased, alphabetic, 4+ characters.

    Numbers, names and dates are what differ between two copies of one form, so
    dropping them is what lets the copies be recognised as copies.
    """
    return frozenset(re.findall(r"[a-z]{4,}", text.lower()))


def overlap(a: str, b: str) -> float:
    """Jaccard overlap of two chunks' distinctive words, 0.0 to 1.0."""
    sa, sb = _shingle(a), _shingle(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def duplicates(chunks: dict, *, threshold: float = DUPLICATE_OVERLAP,
               group: int = DUPLICATE_GROUP) -> set:
    """Ids of chunks belonging to a repeated set of `group` or more.

    Takes a `sweep` chunk store's inner mapping — `{id: {"text": ...}}`, i.e.
    `load_chunks(path)["chunks"]`.

    Single-link clustering at `threshold`: a chunk joins a set if it overlaps
    *any* member, which is what a form with a variable header needs — copy 1 and
    copy 40 may differ more than either differs from copy 20.
    """
    items = [(cid, _shingle(c["text"])) for cid, c in chunks.items()]
    parent = {cid: cid for cid, _ in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, (cid, sa) in enumerate(items):
        if not sa:
            continue
        for cjd, sb in items[i + 1:]:
            if not sb:
                continue
            if len(sa & sb) / len(sa | sb) >= threshold:
                ra, rb = find(cid), find(cjd)
                if ra != rb:
                    parent[ra] = rb

    sizes: dict = {}
    for cid, _ in items:
        root = find(cid)
        sizes[root] = sizes.get(root, 0) + 1
    return {cid for cid, _ in items if sizes[find(cid)] >= group}


def core(chunks: dict, *, ratio: float = PROSE_RATIO,
         threshold: float = DUPLICATE_OVERLAP, group: int = DUPLICATE_GROUP,
         digits: float = DIGIT_SHARE, drop_decoded: bool = True) -> set:
    """Ids of the chunks a quote can honestly come from: the four filters composed.

    Takes a `sweep` chunk store's inner mapping — `load_chunks(path)["chunks"]`.
    Every threshold is passed straight through to the filter that owns it, so a
    corpus that needs different ones needs no change here.
    """
    repeated = duplicates(chunks, threshold=threshold, group=group)
    return {cid for cid, c in chunks.items()
            if cid not in repeated
            and not (drop_decoded and c.get("decoded"))
            and is_prose(c["text"], ratio)
            and digit_share(c["text"]) <= digits}


def rank(answers: dict, ids: set) -> list:
    """(verdict, chunk id) for `ids`, strongest first; ids with no answer dropped.

    `answers` is one criterion's `{chunk id: laya.detail(...) entry}`.
    """
    return sorted(((a["verdict"], cid) for cid, a in answers.items() if cid in ids),
                  reverse=True)


def sink_scores(answers: dict, ids: set, *, at: float = 0.5) -> dict:
    """Per chunk, how many yes/no criteria peak on it and how many fire on it.

    The four filters above judge a chunk by its *text*. This one judges it by
    what the model did with it, which catches the case they cannot: text that
    reads as prose and is not.

    A scanned bid form returned by `pdftotext` as `Insula tion Sl ,716.00` and
    `"-\\cousrical Tile Cei.l.ings` carries enough short function words to pass
    `is_prose`, few enough digits to pass `digit_share`, and appears once, so
    `duplicates` never sees it — and it means nothing. One such chunk was the
    peak chunk for **eight of eighteen** criteria in a single round: a thin
    contingency, an outdated schedule, an unsigned agreement, a contractor's
    capacity and a budget that would not reconcile, all supposedly evidenced by
    the same garbled list of finish trades. A chunk that answers every question
    is answering none of them.

    `answers` is `{question id: {chunk id: laya.detail row}}`, and `ids`
    restricts it to the chunks in play — normally `core()`'s output. Questions
    whose verdict is not a number, a choice or a score, are skipped, as is any
    chunk outside `ids`.

    Pass only the criteria you mean. A claim and its own denial both peaking on
    one chunk is that pair working rather than a sink, and a control question
    peaks wherever it likes.

    Returns `{chunk id: {"peaks", "fires", "fire_share"}}`. How many peaks is
    too many is the caller's: it depends on how many criteria were asked and
    how much of one document they are genuinely about. `at` is the probability
    a yes/no answer counts as fired at, matching `unlabelled.FIRE`.
    """
    peaks = {cid: 0 for cid in ids}
    fires = {cid: 0 for cid in ids}
    counted = 0
    for per_chunk in answers.values():
        inside = {
            cid: float(row["verdict"])
            for cid, row in per_chunk.items()
            if cid in peaks and isinstance(row.get("verdict"), (int, float))
        }
        if not inside:
            continue
        counted += 1
        peaks[max(inside, key=lambda c: inside[c])] += 1
        for cid, p in inside.items():
            if p >= at:
                fires[cid] += 1
    return {
        cid: {
            "peaks": peaks[cid],
            "fires": fires[cid],
            "fire_share": round(fires[cid] / counted, 4) if counted else 0.0,
        }
        for cid in peaks
    }
