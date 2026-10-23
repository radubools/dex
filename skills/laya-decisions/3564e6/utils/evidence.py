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

**A fifth filter works a page at a time, before any of those.** All four above
judge a chunk on its own words, and that is not enough when part of the PDF is
a scan: OCR of a drawing produces fragments — `FLODR2 FOR KEYNOTE DESCR1PTION`
— but a scanned page usually also carries a typed paragraph, and a chunk
straddling the two reads middling on every test here. Measured per *page* the
same document separates cleanly. `page_quality` scores each page with
`junk_share`, `chunk_pages` maps each chunk back to the pages its characters
came from, and `from_text_pages` composes them into a keep list. The case that
wanted it: a 62-page progress review with a 36-page punch-list attachment
bound into the back. `core()` passed 68 of its 98 chunks, scanned floor plans
included, and all 24 of that round's criteria put their strongest answer on one
of three of them. The scanned pages sat at 0.54-0.78 junk share and every page
of text at 0.24-0.38, with nothing in the gap.

Nothing here decides anything or formats anything. Every function returns ids or
a number, every threshold is a keyword argument with a default, and which
threshold suits a corpus is the caller's call — the defaults came from one
document and are a starting point, not a rule. Measure yours: print
`prose_ratio` and `digit_share` across a sample before trusting either.
"""

import math
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


def prose_cut(texts: list[str]) -> dict:
    """The function-word ratio splitting the widest gap in `texts`' own distribution.

    `PROSE_RATIO` is one number taken from one corpus, and a genre it does not
    fit fails quietly rather than loudly: on a lender's construction analysis
    0.035 kept the borrower's budget sheet and three pages of exported Gantt
    rows as prose, because a Primavera export still contains the word "of".
    When a document's ratios are bimodal — narrative well above tables, with a
    band between them nothing occupies — the document can say where its own
    threshold goes.

    Returns `{"cut", "below", "above", "gap", "ratios"}`: the midpoint of the
    widest gap, the two values either side of it, how wide it is, and every
    ratio sorted, to hand `core` or `is_prose` as `ratio=`.

    **It always returns a cut, including when there is no gap worth the name.**
    Every distribution has a widest gap, and on one that shades evenly from
    table to prose that gap is noise. Look at `gap` before using `cut`: this
    returns the measurement and leaves the judgement with the caller, because
    how bimodal is bimodal enough depends on the corpus and on what a wrong
    split would cost.
    """
    ratios = sorted(prose_ratio(t) for t in texts)
    if len(ratios) < 2:
        return {"cut": PROSE_RATIO, "below": None, "above": None,
                "gap": 0.0, "ratios": ratios}
    below, above = max(zip(ratios, ratios[1:]), key=lambda pair: pair[1] - pair[0])
    return {"cut": (below + above) / 2, "below": below, "above": above,
            "gap": above - below, "ratios": ratios}


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
#: words above, plus the modals and reporting verbs a question and an unrelated
#: sentence share for free, plus the scaffolding a criterion's wording usually
#: carries ("the text says ..."). Pass your own `stop` if your questions are
#: phrased differently.
QUOTE_STOP = frozenset("""
the and for with this that are was were has have had been will not all its they
their there which when per than into over still now also any each one some other
only from but should would could may might can does did being need needs needed
make made give given shown show noted note appears appear seems within about
text says say said provided required must
""".split())

#: Summed word rarity a candidate sentence must reach before `quote` returns it.
#: Under this, a match is one or two ordinary words in common and the sentence is
#: not evidence of anything. Deliberately not zero: a quote that does not support
#: the criterion printed above it is worse than no quote at all.
MIN_MATCH = 3.0

#: How many of a criterion's strongest chunks `quote` searches. The peak chunk is
#: often not where the evidence reads best — a criterion can separate cleanly
#: from its denial on a passage that merely discusses the subject, while the
#: sentence that states it sits in a summary somewhere else.
QUOTE_TOP = 4


def content_words(text: str, stop: frozenset | set = QUOTE_STOP) -> set:
    """`text`'s words worth matching on: 3+ letters, hyphens closed, minus `stop`.

    Closing a hyphen between two letters is what makes "re-designed" match
    "redesigned" and "sub-contractor" match "subcontractor". Reports hyphenate
    where questions do not, and without this a criterion misses its own evidence
    on one character.
    """
    flat = re.sub(r"(?<=[a-z])[-\u2010\u2011](?=[a-z])", "", text.lower())
    return {w for w in re.findall(r"[a-z]{3,}", flat) if w not in stop}


def sentences(text: str, *, shortest: int = 30) -> list:
    """`text` split into candidate sentences, whitespace collapsed.

    Breaks on `.`, `;`, `:` and the bullet character, because an extracted report
    carries as much of its meaning in bulleted fragments as in full sentences.
    Anything under `shortest` characters is dropped as a heading or a stub.
    """
    flat = " ".join(text.split())
    parts = re.split(r"(?<=[.;:])\s+|(?<=\u2022)\s+", flat)
    return [p.strip() for p in parts if len(p.strip()) >= shortest]


def word_weights(chunks: dict, *, stop: frozenset | set = QUOTE_STOP) -> dict:
    """How rare each word is across a chunk store, as `log(N / chunks holding it)`.

    Takes a `sweep` chunk store's inner mapping — `load_chunks(path)["chunks"]`.

    Unweighted overlap picks a sentence for sharing the word "budget" with a
    question about budgets, which in a lender's report is every second sentence.
    Weighting by rarity is what lets "retention" or "mezzanine" outrank it.

    Compute this over the chunks a quote may come from, not the whole document:
    a word's rarity among the appendices is not its rarity in the prose.
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

    `rank()` and `unlabelled.top_chunks()` tell you which *chunk* a criterion
    rests on. A report has to quote a *line*, and this is that step: among the
    `top` chunks the criterion answered highest, the sentence sharing the most
    rare words with `claim`.

    - `chunks` is a store's inner mapping, `answers` is one criterion's
      `{chunk id: laya.detail row}`, and `claim` is the question's own wording.
    - `ids` restricts the search, and normally should — pass `core()`'s output,
      or it will quote a column of figures that happens to share the vocabulary.
    - `weights` is `word_weights(...)`; computed over the searched chunks if you
      do not pass it.

    Returns `{"sentence", "score", "chunk", "rank"}` — the chunk it came from and
    that chunk's rank among the criterion's strongest, so a caller can say
    whether it was the peak. **`sentence` is `None` under `min_match`**, with
    `score` still reporting how close the best candidate came, because the honest
    answer to "which line says this" is sometimes that no line does.

    It does not judge. A returned sentence shares vocabulary with the question,
    which is not the same as answering it — print the chunk beside the quote and
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
            score = sum(weights.get(w, 0.0)
                        for w in content_words(sentence, stop) & want)
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


def baseline(scored: dict, ids=None) -> dict:
    """Per chunk, the mean score across every criterion — how agreeable it is.

    The four filters above drop the chunks that are not prose. They do not
    catch the ones that *are* prose and still answer yes to almost everything:
    a long enumerated paragraph, a log of dated line items, a page of contract
    bullets. Those score high for every criterion, so whichever criterion you
    look at, its strongest chunk is one of them.

    The case this was written from: 114 criteria over 88 chunks of a lender's
    cost review. Four prose chunks — a cost-estimate log, a road-widening
    description and two pages of contract provisions — took 47 of the 114
    criteria's top spots, and six different criteria were quoting the same two
    paragraphs as their evidence. Their means across all 114 criteria were 0.62,
    0.50, 0.47 and 0.41, against 0.08 for the quietest chunk in the document.

    Subtracting this from a criterion's score on a chunk gives that criterion's
    **lift** there: how much more it said about the chunk than the average
    criterion did. Ranking on lift rather than on the raw score is what
    separates the chunk a criterion actually found from the chunk everything
    finds.

    `scored` is `{criterion id: {chunk id: score}}`, the scores already on one
    scale — `laya.scale` is what puts mixed question types there. `ids` limits
    the result, and defaults to every chunk any criterion scored.
    """
    if ids is None:
        ids = {cid for per_chunk in scored.values() for cid in per_chunk}
    out = {}
    for cid in ids:
        values = [per_chunk[cid] for per_chunk in scored.values() if cid in per_chunk]
        out[cid] = sum(values) / len(values) if values else 0.0
    return out


#: A chunk that is the strongest evidence for more than this many criteria is
#: the strongest evidence for none of them. Read the distribution before
#: trusting it: a real cut shows up as a gap, not a round number.
MAGNET_PEAKS = 5


def peaks(rounds, *, ids=None) -> dict:
    """Per chunk, how many criteria have it as their single strongest — their argmax.

    `baseline` answers the same worry by averaging, and misses the chunk that is
    middling almost everywhere and still wins the questions it wins. The case
    this was written from: a site plan's title block — zoning, acreage, seat
    counts, no sentence in it — sat at a baseline of 0.57, comfortably inside
    the evidence pool, while being the strongest evidence for **14** of one
    document's 76 criterion-askings. Averaging cannot see that; counting
    argmaxes can.

    `rounds` is an iterable of `{criterion id: {chunk id: score}}` — one per
    round of a question set, or a single mapping's worth for one pass, with the
    scores already on one scale as `laya.scale` puts them. Counting across
    rounds rather than within one is what makes the result stable: a chunk that
    wins for a dozen questions asked in different words is not winning on any
    one wording. Drop controls and classifier questions first; they are not
    claims about the corpus and their argmaxes say nothing.
    """
    counts: dict = {}
    for scored in rounds:
        for per_chunk in scored.values():
            if ids is not None:
                per_chunk = {c: v for c, v in per_chunk.items() if c in ids}
            if not per_chunk:
                continue
            best = max(per_chunk, key=lambda c: per_chunk[c])
            counts[best] = counts.get(best, 0) + 1
    return counts


def magnets(counts: dict, *, threshold: int = MAGNET_PEAKS) -> set:
    """Chunk ids that are the strongest evidence for more than `threshold` criteria.

    Takes `peaks`' output rather than recomputing it, so the counts stay
    available to report beside the verdict and the threshold can be moved
    without rescoring anything.
    """
    return {cid for cid, n in counts.items() if n > threshold}


#: How many times its own share of the argmaxes a chunk must win to be a magnet,
#: when the threshold is scaled to the pool rather than fixed.
MAGNET_MULTIPLE = 3.0


def magnet_threshold(
    counts: dict,
    pool: int,
    *,
    multiple: float = MAGNET_MULTIPLE,
    floor: int = MAGNET_PEAKS,
) -> int:
    """A magnet threshold scaled to the pool: `multiple` times a chunk's own share.

    `MAGNET_PEAKS` is an absolute count, and it is calibrated for a pool large
    enough that five argmaxes is an anomaly. On a short document it is not. With
    sixty criterion-askings over eight chunks the average chunk wins eight, so
    the default threshold sits *below* the mean and `magnets` excludes most of
    the document — including, on the report this came from, both narrative
    segments that held the findings. Two criteria fell from 0.75 to 0.46 and
    from 0.79 to 0.25 purely by losing the sentences they existed for, with
    nothing in the output to say why.

    So: `sum(counts.values()) / pool` is what an unremarkable chunk wins, and a
    magnet is one winning `multiple` times that. `floor` keeps the absolute rule
    as a lower bound, so this never loosens the threshold on a large pool.

    `pool` is how many chunks were eligible, not `len(counts)` — a chunk that
    never won an argmax has no key in `counts`, and leaving those out would
    overstate the average and let a real magnet through.

    Returns the number to hand to `magnets(counts, threshold=...)`. Whether a
    given multiple is right for a document is the caller's judgement; this only
    converts it into a count.
    """
    if pool <= 0:
        return floor
    return max(floor, round(multiple * sum(counts.values()) / pool))


def narrow(rounds, ids, *, threshold: int | None = None,
           multiple: float = MAGNET_MULTIPLE, floor: int = MAGNET_PEAKS,
           passes: int | None = None) -> tuple:
    """Drop magnet chunks, recount among the survivors, repeat until none appear.

    `magnets` run once sees only the loudest tier. A criterion has exactly one
    argmax, so the loudest chunks take every count and whatever is loudest
    among the *rest* has none and looks innocent. Recounting after each drop is
    what exposes the next tier.

    Measured on a lender's construction report: 55 chunks, 79 criterion-askings
    over three rounds. Starting from the 28 chunks that read as prose, the
    first pass dropped two reproduced drawing sheets holding 85 argmaxes
    between them — and the report's **table of contents** scored zero. A list
    of section headings naming every topic a question could ask about, it won
    for something in nearly every round, and surfaced only on the third pass,
    with 14 argmaxes. A single pass leaves it in and six criteria quote an
    index as their evidence.

    - `rounds` is an iterable of `{criterion id: {chunk id: score}}`, as
      `peaks` takes it. Drop controls and classifier questions first; their
      argmaxes say nothing about the corpus.
    - `ids` is the chunks to start from, in the order to keep. Not modified.
    - `threshold` fixes the bar. Left None it is recomputed each pass by
      `magnet_threshold` against the *surviving* pool, which is the point: the
      bar falls as the pool shrinks. `multiple` and `floor` pass through.
    - `passes` caps the iteration; None runs to a fixed point, which always
      terminates because every pass either drops a chunk or stops.

    Returns `(surviving, history)`. `history` is one record per pass —
    `threshold`, `pool_before`, and `dropped` as `{chunk id: argmax count}` —
    so a caller can report what went and why, or disagree and put some back.
    It reaches no verdict about a chunk: it counts argmaxes and says who lost.
    """
    surviving = list(ids)
    history: list = []
    gone: set = set()
    while passes is None or len(history) < passes:
        counts = peaks(rounds, ids=set(surviving))
        bar = (
            threshold
            if threshold is not None
            else magnet_threshold(
                counts, pool=len(surviving), multiple=multiple, floor=floor
            )
        )
        found = magnets(counts, threshold=bar) - gone
        if not found:
            break
        history.append({
            "threshold": bar,
            "pool_before": len(surviving),
            "dropped": {
                cid: counts[cid] for cid in sorted(found, key=lambda c: -counts[c])
            },
        })
        gone |= found
        surviving = [cid for cid in surviving if cid not in gone]
    return surviving, history


# --- page-level quality -----------------------------------------------------
#
# Everything above judges a chunk by its own words. These judge the *page* a
# chunk came from, which is the only thing that separates OCR of a drawing from
# the typed paragraph printed beside it.

#: A page whose words are fragments this often is a scan rather than text. On
#: the document this came from the scanned floor plans sat at 0.54-0.78 and
#: every page of narrative, correspondence, permit review and pay application
#: at 0.24-0.38, so the threshold is not delicately placed.
JUNK_PAGE = 0.45

#: A chunk is kept when at least this much of it came from pages that are text.
#: Not 1.0, because a chunk straddling a page break picks up a few characters of
#: the next page's header; not much lower either, because at 0.6 a chunk that is
#: a scanned drawing followed by the punch list beside it survives.
TEXT_PAGE_SHARE = 0.9

_LETTERS = re.compile(r"[^A-Za-z]")
_MIXED = re.compile(r"[A-Za-z][0-9]|[0-9][A-Za-z]")


def junk_share(text: str) -> float:
    """The share of `text`'s words that are fragments rather than words.

    A fragment is a word with fewer than three letters in it, or one mixing
    letters and digits inside a single token. Both are what an OCR pass makes
    of a drawing's leader lines, room tags and dimension strings; neither is
    common in prose, in a table of money, or in contract boilerplate — which is
    what distinguishes this from `prose_ratio`, that would fail all three.
    """
    words = text.split()
    if not words:
        return 1.0
    fragments = sum(
        1 for w in words
        if len(_LETTERS.sub("", w)) < 3 or _MIXED.search(w)
    )
    return fragments / len(words)


#: A text is garbled by the doubling artefact above this share of stray
#: letters. Prose runs at 0.0; the damaged lines measured ran 0.045 to 0.147.
STRAY_LETTERS = 0.04

#: Single letters that really are words, and so are never the artefact.
_REAL_SINGLE = frozenset({"a", "i"})


def stray_letters(text: str) -> float:
    """The share of `text`'s alphabetic words that are one stray letter.

    A different extraction failure from the one `junk_share` measures, and the
    reason both exist. On a two-column PDF page `pdftotext -layout` can double
    characters and scatter the spares as isolated letters: "App proximately y
    6-ACRE withw landsca aping, circuulations drivves". The text still parses
    as sentences and still reads as prose to every other filter here, but a
    decision model answers it *high* on almost any question, so it behaves as
    a magnet — on the report this was written from, four criteria peaked on one
    such line.

    `junk_share` cannot separate it. It counts a word a fragment when it is
    short, which scores the garbled line 0.395 and the clean sentence "A Army
    Corp 404 and 401 permit is required" 0.444 — the clean one *worse* — so no
    threshold divides them. This counts only the artefact itself, and on a
    61-page report a cut at `STRAY_LETTERS` took the two affected pages and
    nothing else.

    Returns a share, not a verdict: which cut to use, and whether to drop a
    unit or merely flag it, is the caller's.
    """
    words = [w.strip(".,;:()[]\"'’“”") for w in text.split()]
    alpha = [w for w in words if w.isalpha()]
    if not alpha:
        return 0.0
    strays = sum(1 for w in alpha if len(w) == 1 and w.lower() not in _REAL_SINGLE)
    return strays / len(alpha)


#: A text is OCR mojibake above this share of misshapen words. Prose runs at
#: 0.000-0.010; the scanned page measured ran 0.094, so the cut is a wide one.
GARBLE = 0.05

_GARBLE_WORD = re.compile(r"[A-Za-z][A-Za-z'’]{3,}")
_VOWEL = re.compile(r"[aeiouyAEIOUY]")


def garble_share(text: str) -> float:
    """The share of `text`'s four-letter-plus words whose shape says OCR, not language.

    The third extraction failure in this family, and the third reason a page can
    read as prose to every other filter here while carrying no sentences. A
    scanned page of a permit or a stamp comes back as *word-length* tokens with
    the letters wrong: "THISEERMIT ISlllEREBvISSUED TO THE PERSON", "WORK MIJST
    BE STARTED WITHIN 180 DAYS", "Signaturdibfiowno-rlconlractor".

    `junk_share` cannot separate it, because nothing in it is short and nothing
    mixes letters with digits: on the report this was written from the scanned
    page scored 0.387, inside the range the genuine prose pages occupied —
    0.399 for one page of narrative and 0.435 for another. No threshold divides
    them. `stray_letters` cannot either; the spare characters are inside the
    words rather than scattered between them.

    What does divide them is the shape of a word: a lowercase letter followed
    by an uppercase one *inside* it, or no vowel in it at all. Neither happens
    often in English, in a money table or in contract boilerplate, and both
    happen constantly to a mis-mapped glyph run. Measured over that report every
    genuine page scored 0.010 or below against the scanned page's 0.094.

    Compose it over `core()` rather than passing it in — `core(chunks) & {cid
    for cid, c in chunks.items() if garble_share(c["text"]) < GARBLE}` — since
    it is a fourth independent signal and not a parameter of the other three.

    Returns a share, not a verdict: `GARBLE` is a default to start from, and
    whether to drop a unit or merely flag it stays with the caller.
    """
    words = _GARBLE_WORD.findall(text)
    if not words:
        return 0.0
    misshapen = sum(
        1
        for w in words
        if not _VOWEL.search(w)
        or any(w[i].islower() and w[i + 1].isupper() for i in range(len(w) - 1))
    )
    return misshapen / len(words)


def page_quality(pages: list, *, threshold: float = JUNK_PAGE) -> list:
    """Per page: its word count, its junk share, and whether it reads as text.

    `pages` is the document one string per page — `pdftotext` output split on
    the form feed, or `pypdf`'s `extract_text()` per page. Returns a list of
    dicts in page order, 1-indexed on `page`; nothing is dropped and no verdict
    is reached about the document.
    """
    out = []
    for i, page in enumerate(pages, 1):
        share = junk_share(page)
        out.append({"page": i, "words": len(page.split()),
                    "junk_share": round(share, 4), "text": share < threshold})
    return out


def _flatten(pages: list) -> tuple:
    """The pages as one whitespace-collapsed string, plus each character's page.

    `laya.split` normalises whitespace, so a chunk is never a literal substring
    of the text it was cut from. Collapsing both sides is what lines them up.
    """
    collapsed, owner = [], []
    for n, page in enumerate(pages, 1):
        for w in page.split():
            if collapsed:
                collapsed.append(" ")
                owner.append(n)
            collapsed.append(w)
            owner += [n] * len(w)
    return "".join(collapsed), owner


def locate(pages: list, chunks: list) -> list:
    """The page each character of each chunk came from, one list per chunk.

    Chunks are matched in document order and the search only moves forward, so
    a repeated stretch of boilerplate cannot pull a later chunk back to an
    earlier page. A chunk that cannot be located at all gets an empty list.
    """
    flat, owner = _flatten(pages)
    spans, cursor = [], 0
    for chunk in chunks:
        needle = " ".join(chunk.split())
        at = flat.find(needle, cursor)
        if at < 0:
            # A chunk boundary falling inside a word would not be found whole;
            # its first 200 characters are plenty to locate the page.
            at = flat.find(needle[:200], cursor)
        if at < 0:
            spans.append([])
            continue
        spans.append(owner[at:at + len(needle)])
        cursor = at + max(1, len(needle) // 2)
    return spans


def chunk_pages(pages: list, chunks: list) -> list:
    """The distinct page numbers each chunk's text came from, one list per chunk."""
    return [sorted(set(span)) for span in locate(pages, chunks)]


def from_text_pages(pages: list, chunks: list, *, threshold: float = JUNK_PAGE,
                    share: float = TEXT_PAGE_SHARE) -> list:
    """Per chunk: is enough of it from pages that are text to ask questions about?

    A chunk inside one page inherits that page's verdict; a chunk straddling a
    page break is kept when at least `share` of its characters came from pages
    that are text. Returns a list of bools in chunk order, for the caller to
    compose with `core()` or use on its own.
    """
    quality = {q["page"]: q["text"] for q in page_quality(pages, threshold=threshold)}
    out = []
    for span in locate(pages, chunks):
        if not span:
            out.append(False)
            continue
        out.append(sum(1 for p in span if quality.get(p, False)) / len(span) >= share)
    return out
