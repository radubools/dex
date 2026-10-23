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

Then, once a criterion has a chunk, two more questions — *is this chunk's score
inflated?* and *which line actually says it?*:

- **`sink_scores`** counts the criteria peaking on each chunk, which says
  *exclude this chunk*: a chunk that answers every question answers none.
  **`chunk_bias`** is the other remedy, the mean answer per chunk, for
  subtracting rather than excluding — the right one when a chunk is merely long
  instead of garbage.
- **`quote`** picks the line sharing the most rare words with the question. It
  needs no checkpoint and cannot tell a sentence that shares a subject from one
  that makes the claim. **`score_windows`** and **`best_window`** put the
  question to the model over short runs of sentences instead, which costs a
  forward pass per window and can. **`sentence_windows`** is the splitter under
  them, for a claim too wide for the single sentences `sentences()` returns.

Nothing here decides anything or formats anything. Every function returns ids or
a number, every threshold is a keyword argument with a default, and which
threshold suits a corpus is the caller's call — the defaults came from one
document and are a starting point, not a rule. Measure yours: print
`prose_ratio` and `digit_share` across a sample before trusting either.
"""

import math
import re
from typing import Any

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


#: Words too common to tell you a sentence is about a question. The function
#: words above, plus the modals and reporting verbs that a question and an
#: unrelated sentence share for free, plus the scaffolding a criterion's wording
#: usually carries ("the text says ..."). Pass your own `stop` to `quote` if your
#: questions are phrased differently.
QUOTE_STOP = frozenset("""
the and for with this that are was were has have had been will not all its they
their there which when per than into over still now also any each one some other
only from but should would could may might can does did being need needs needed
make made give given shown show noted note appears appear seems within about
text says say said provided required must
""".split())

#: Summed word rarity a candidate sentence must reach before `quote` will return
#: it. Under this, a match is one or two ordinary words in common and the
#: sentence is not evidence of anything. It is deliberately not zero: a quote
#: that does not support the criterion printed above it is worse than no quote.
MIN_MATCH = 3.0

#: How many of a criterion's strongest chunks `quote` searches. The peak chunk is
#: often not where the evidence reads best — a criterion can separate cleanly
#: from its denial on a passage that merely discusses the subject while the
#: sentence that states it sits in a summary somewhere else.
QUOTE_TOP = 4


def content_words(text: str, stop: frozenset | set = QUOTE_STOP) -> set:
    """`text`'s words worth matching on: 3+ letters, hyphens closed, minus `stop`.

    Closing a hyphen between two letters is what makes "re-designed" match
    "redesigned" and "sub-contractor" match "subcontractor". Reports hyphenate
    where questions do not, and without this a criterion misses its own evidence
    on one character.
    """
    flat = re.sub(r"(?<=[a-z])[-‐‑](?=[a-z])", "", text.lower())
    return {w for w in re.findall(r"[a-z]{3,}", flat) if w not in stop}


def sentences(text: str, *, shortest: int = 30) -> list:
    """`text` split into candidate sentences, whitespace collapsed.

    Breaks on `.`, `;`, `:` and the bullet character, because an extracted report
    carries as much of its meaning in bulleted fragments as in full sentences.
    Anything under `shortest` characters is dropped as a heading or a stub.
    """
    flat = " ".join(text.split())
    parts = re.split(r"(?<=[.;:])\s+|(?<=•)\s+", flat)
    return [p.strip() for p in parts if len(p.strip()) >= shortest]


def word_weights(chunks: dict, *, stop: frozenset | set = QUOTE_STOP) -> dict:
    """How rare each word is across a chunk store, as `log(N / chunks holding it)`.

    Takes a `sweep` chunk store's inner mapping — `load_chunks(path)["chunks"]`.

    Unweighted overlap picks a sentence for sharing the word "budget" with a
    question about budgets, which in a lender's report is every second sentence.
    Weighting by rarity is what lets "retention" or "mezzanine" outrank it.

    Compute this over the chunks a quote may come from, not over the whole
    document: a word's rarity among the appendices is not its rarity in the prose.
    """
    n = len(chunks)
    df: dict = {}
    for c in chunks.values():
        for word in content_words(c["text"], stop):
            df[word] = df.get(word, 0) + 1
    return {word: math.log(n / count) for word, count in df.items()}


def quote(chunks: dict, answers: dict, claim: str, *, weights: dict | None = None,
          ids: set | None = None, top: int = QUOTE_TOP,
          min_match: float = MIN_MATCH,
          stop: frozenset | set = QUOTE_STOP) -> dict:
    """The sentence best supporting one criterion, from the chunks it scored highest.

    `rank()` and `unlabelled.top_chunks()` tell you which chunk a criterion rests
    on. A report has to quote a *line*, and this is that step: among the `top`
    chunks the criterion answered highest, the sentence sharing the most rare
    words with `claim`.

    - `chunks` is a store's inner mapping, `answers` is one criterion's
      `{chunk id: laya.detail row}`, and `claim` is the question's own wording.
    - `ids` restricts the search, and normally should — pass `core()`'s output, or
      it will quote a column of figures that happens to share the vocabulary.
    - `weights` is `word_weights(...)`; computed over `ids` if you do not pass it.

    Returns `{"sentence", "score", "chunk", "rank"}` — the chunk it came from and
    that chunk's rank among the criterion's strongest, so a caller can say
    whether it was the peak. **`sentence` is `None` under `min_match`**, with
    `score` still reporting how close the best candidate came, because the honest
    answer to "which line says this" is sometimes that no line does.

    It does not judge: a returned sentence shares vocabulary with the question,
    which is not the same as answering it. Print the chunk beside the quote and
    let a reader check.
    """
    pool = {cid: row for cid, row in answers.items()
            if cid in chunks and (ids is None or cid in ids)}
    if weights is None:
        weights = word_weights({cid: chunks[cid] for cid in pool}, stop=stop)
    want = content_words(claim, stop)
    strongest = sorted(
        (cid for cid in pool if isinstance(pool[cid].get("verdict"), (int, float))),
        key=lambda cid: -float(pool[cid]["verdict"]),
    )[:top]

    best = {"sentence": None, "score": 0.0, "chunk": None, "rank": None}
    for place, cid in enumerate(strongest, 1):
        for sentence in sentences(chunks[cid]["text"]):
            score = sum(weights.get(w, 0.0) for w in content_words(sentence, stop) & want)
            if score > best["score"]:
                best = {"sentence": sentence, "score": round(score, 2),
                        "chunk": cid, "rank": place}
    if best["score"] < min_match:
        best["sentence"] = None
    return best


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


#: Sentences per window in `sentence_windows`. Two, because a claim's subject and
#: its predicate often straddle a sentence break in an extracted report.
WINDOW = 2

#: Shortest assembled window worth scoring, in characters.
WINDOW_FLOOR = 60


def _sibling(name: str) -> Any:
    """Import a module sitting beside this one, package-relative or flat."""
    try:
        return __import__(f"{__package__}.{name}", fromlist=[name]) if __package__ \
            else __import__(name)
    except ImportError:
        return __import__(name)


def chunk_bias(answers: dict, ids: set | None = None) -> dict:
    """Each chunk's mean yes/no answer across every question asked of it.

    `sink_scores` above judges a chunk by counting the criteria that peak on it,
    which says *exclude this chunk*. This says by how much a chunk flatters every
    answer, so a caller can keep it and subtract instead — the two remedies for
    one problem, and which to reach for depends on whether the chunk is garbage or
    merely long.

    The case for subtracting: over 127 criteria and 40 segments of one 33-page
    report the per-chunk mean ran from 0.085 to 0.710, tracking chunk length. The
    document's entire land-survey finding — *"4.02 Land Title Survey Plan  None
    received."* — is a 104-token chunk averaging 0.085, and it cannot win a
    question against a 400-token trade schedule averaging 0.629 however exactly it
    answers one. Ranking each criterion by `verdict - bias[chunk]` moved five
    criteria off the long chunks and onto the ones that state them.

    `answers` is `{question id: {chunk id: verdict or laya.detail row}}`, and
    `ids` restricts it. Only yes/no answers count: a choice label and a rubric
    level are not on a common scale and averaging them together means nothing.

    Returns `{chunk id: mean}`. It is an estimate off however many questions you
    asked, so read it beside `len(...)` of the same answers, and pass only the
    criteria you mean — a control question belongs in no baseline.
    """
    seen: dict[Any, list] = {}
    for per_chunk in answers.values():
        for cid, row in per_chunk.items():
            if ids is not None and cid not in ids:
                continue
            if isinstance(row, dict):
                if row.get("type") not in (None, "noul"):
                    continue
                value = row.get("verdict", row.get("noul"))
            else:
                value = row
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                seen.setdefault(cid, []).append(float(value))
    return {cid: sum(v) / len(v) for cid, v in seen.items() if v}


def sentence_windows(text: str, *, size: int = WINDOW,
                     floor: int = WINDOW_FLOOR) -> list:
    """Overlapping runs of `size` adjacent sentences of `text`, in order.

    `sentences()` above is the single-sentence form, and is what `quote()` scores.
    This is for a claim that does not fit in one — *gives a completion date but no
    schedule* is two sentences in the report it came from, and no single sentence
    supports it.

    Every window is a **contiguous** run. That is the whole difficulty: filtering
    short fragments out *before* windowing, which is what `sentences()` does with
    `shortest`, silently joins sentences that were never adjacent and produces a
    quote the document does not contain. Three of them reached a finished report
    before a check caught it. So the split here keeps every fragment and `floor`
    filters the assembled windows, and is ignored if it would leave none.
    """
    flat = " ".join(text.split())
    parts = [p for p in re.split(r"(?<=[.;:])\s+|(?<=•)\s+", flat) if p.strip()]
    out = [" ".join(parts[i:i + size])
           for i in range(max(1, len(parts) - size + 1))]
    return [w for w in out if len(w) >= floor] or out


def score_windows(agent: Any, question: dict, text: str, *, size: int = WINDOW,
                  floor: int = WINDOW_FLOOR) -> list:
    """Ask `question` of each window of `text`; `{"text", "verdict"}`, best first.

    The model-scored counterpart to `quote()`. `quote()` picks the line sharing
    the most rare words with the question, which is fast, needs no checkpoint, and
    cannot tell a sentence that shares a subject from one that makes the claim.
    This puts the question to each window and uses the answer, which costs a
    forward pass per window and can.

    Why that is worth paying for: comparing the best window's answer against the
    whole chunk's is a test the chunk's own score cannot supply. Over 126 criteria
    of one report the ratio between them had a median of 0.89 — and 26 sat under
    0.5, including four chunks that answered 1.000 while no window in them
    answered above 0.22. Those were criteria matching what a chunk was *about*.
    A low ratio is not proof of that on its own, since a claim can be spread wider
    than `size` sentences, so the ratio is for the caller to read, not for this to
    act on.

    Windows over the question's own token budget are skipped rather than
    truncated: `laya.ask` would refuse them, and a silently shortened window is a
    quote of something the document does not say.
    """
    laya = _sibling("laya")
    budget = laya.state_budget({"q": question})
    scored = []
    for window in sentence_windows(text, size=size, floor=floor):
        if laya.token_count(window) > budget:
            continue
        verdict = laya.answers(laya.ask(agent, window, {"q": question}))["q"]
        scored.append({"text": window, "verdict": verdict})
    scored.sort(key=lambda w: w["verdict"], reverse=True)
    return scored


def best_window(agent: Any, question: dict, text: str, *, size: int = WINDOW,
                floor: int = WINDOW_FLOOR) -> dict | None:
    """The window of `text` that answers `question` highest, or None if none fit."""
    scored = score_windows(agent, question, text, size=size, floor=floor)
    return scored[0] if scored else None
