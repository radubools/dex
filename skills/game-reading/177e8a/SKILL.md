---
name: game-reading
description: The original text behind a lesson in the games in ~/games — the passages of the book or paper a story retells, verbatim, with their equations in LaTeX, their figures cut from the PDF and every publication they cite linked to its DOI, arXiv or publisher page (scouted on the web and checked), in one kind "reading" package per story that the game shows behind the story card's book button.
---

# Game reading — the original text behind a lesson

A lesson retells an idea in a few short cards. Behind the card's **book
button** the player can read the original: the book's own passage about the
concept the beat is on, its equations, its figures and the papers it cites,
each one link away (engine `design/27_reading.md`). You make that text, as a
package of kind `reading`, one per story.

```python
from pathlib import Path
from utils import reading as rd
```

Read `utils/API.md` for every signature.

## The package

Named after its story (`reading/llm-svd` for `story/llm-svd`), in this task's
package directory, tagged `type:reading` and `story:<name>` (write_reading
adds both):

```
reading.json    {schema 1, name, story, work, passages, references}
figures/*.png   the passages' figures
previews/outline.md   the whole reading as one document, for review
```

```python
rd.write_reading(Path("."), name="eslr-ridge", story="story/eslr-ridge",
    work={"title": "The Elements of Statistical Learning", "authors": ["Trevor Hastie", "Robert Tibshirani", "Jerome Friedman"],
          "year": 2009, "edition": "2nd ed.", "publisher": "Springer", "url": "https://hastie.su.domains/ElemStatLearn/"},
    passages=[{"id": "ridge-penalty", "title": "3.4.1 Ridge Regression", "where": "Chapter 3, pp. 61–63",
               "markdown": "Ridge regression shrinks the regression coefficients by imposing a penalty on their size [Hoerl and Kennard, 1970](#ref:hoerl-kennard-1970) […]",
               "refs": ["hoerl-kennard-1970"]}],
    references={"hoerl-kennard-1970": {"text": "Hoerl, A. E. and Kennard, R. (1970). Ridge regression: biased estimation for nonorthogonal problems, Technometrics 12: 55–67.",
                                       "url": "https://doi.org/10.1080/00401706.1970.10488634"}})
```

- **`passages`**: one per concept your brief names, with **exactly the ids the
  brief gives** (the story's beats point at them). `title` is the source's own
  section heading where there is one; `where` says where it is (chapter,
  section, pages).
- **`markdown`**: the passage. `$…$` inline maths and `$$…$$` displays (a `$$`
  block on lines of its own), fenced code, tables, lists, quotes. A figure
  is an image alone in its paragraph, `![Figure 3.8: Profiles of ridge
  coefficients …](figures/fig-3-8.png)`, captioned by its alt text. A
  citation is a link to `#ref:<id>`: `[Hoerl and Kennard, 1970](#ref:hoerl-kennard-1970)`.
  Other links must be http(s).
- **`references`**: every work a passage cites, by id (`<first-author>-<year>`,
  `golub-kahan-1965`), its full citation as the source's bibliography gives it,
  and the `url` of the publication.

## What a passage is

**The source's own words**, not a summary and not a retelling: the reader
pressed the book button to read the original. Take the sentences that
present the concept: its definition, its key equations and the lines that
explain them, **150–500 words per passage**, never more than 700. Cut what
the concept does not need, marking each cut with `[…]`; a concept the source
spends pages on is several short excerpts joined by `[…]`, not the pages.

**Short excerpts also keep your output from being blocked.** Writing out long
stretches of a published book word for word can stop with "Output blocked by
content filtering policy", and the task then fails. Write the reading in
several small steps (one passage per `write_reading`-free draft file, then
assemble), keep each excerpt short, and if a block happens, shorten that
passage further (more `[…]` cuts, the equations and the key sentences kept)
and go on; never give up the whole reading for one passage.

- Copy the text faithfully. Fix only what the extraction broke: hyphenation
  across lines, ligatures (`ﬁ` → `fi`), running heads, page numbers and
  footers inside the text, words split by a stray space ("functio n").
- **Rebuild every formula in LaTeX.** PDF extraction garbles maths
  (subscripts, fractions, sums and Greek letters come out mangled). Write
  what the book prints: same symbols, same equation numbers (`\qquad (3.41)`).
  The study notes beside the source (below) have the equations in LaTeX.
- Mark a cut with `[…]`. Never add sentences of your own inside the quote; a
  bridge you must write goes in `[square brackets]`.
- Keep the passage's own figure or table when it is the one the text
  discusses, with its caption as the alt text.
- Keep the citations the text makes, as `#ref:` links.

## Where the texts are (the ML project)

| Source | Original text | Equations | Figures | Bibliography |
|---|---|---|---|---|
| *The Elements of Statistical Learning*, 2nd ed. (`eslr-` lessons) | `~/dex/datasets/ml/eslr/raw/chNN.txt`, extracted from the PDF, with `<<<page N>>>` book-page markers | `~/dex/datasets/ml/eslr/chNN-*.md` (study notes: every numbered equation in LaTeX) | `~/dex/datasets/ml/ESLR.pdf`: **PDF page = book page + 19** | the PDF's References, book pp. 699–745 (PDF pages 718–764): `pdftotext -f 718 -l 764 ESLR.pdf - \| grep -A3 "Hoerl"` |
| *The Mathematics of Large Language Models* (Karpeles) (`llm-` lessons) | `~/dex/datasets/ml/mathematics-of-llms/chNN-*.md` (the book's text) | in the text | none in the source: no figures | none: the text names people, works and years; find each publication yourself |

For ESL the work's `url` is `https://hastie.su.domains/ElemStatLearn/` (the
authors' free edition). For the LLM book, give its title, author and year.

## Figures (from a PDF)

Find the figure's page (`rd.pdf_text(pdf, page)`; the text names "FIGURE 3.8"),
render it, look at it, and cut out the figure without its caption:

```python
rd.pdf_page_png(PDF, 82, Path("previews/page-82.png"))          # look at it
rd.pdf_figure(PDF, 82, Path("figures/fig-3-8.png"), crop=(0.12, 0.08, 0.90, 0.55))  # x0, y0, x1, y1 as fractions
```

**Look at every figure you cut** (open the PNG): the whole figure, axes and
legend included, no caption, no running head, nothing cut off. At most a few
figures per reading, each under 2 MB (pdf_figure trims and scales it).

## Citations: find every publication

Every work a passage cites gets a reference with a working link:

1. Take its full citation from the source's bibliography (ESL), or, where the
   source gives only a name and a year (the LLM book), identify the work:
   who, what, when, in which journal or conference.
2. Search the web for it (your web search tool): the title in quotes with the
   first author's name. Prefer, in order: its **DOI** (`https://doi.org/…`),
   its **arXiv** abstract (`https://arxiv.org/abs/…`), the publisher's page,
   JSTOR, the author's own page or PDF, then a library catalogue for a book.
   Never a search results page, a pirate mirror (sci-hub, libgen, z-library) or
   a link you have not opened.
3. Open it (your web fetch tool) and check it is that work: title, authors and
   year.
4. Finally `rd.check_links(Path("."))` fetches every reference URL. Each must
   be `ok`. A publisher that refuses scripts (403) is fine when your fetch
   tool opened it: say so in your final message.

A citation you cannot identify after a real search keeps its text and no
`url`. `validate_reading` warns about it; list it in your final message.

## Done

- `rd.validate_reading(Path("."))` is `ok`, and every warning is handled or
  explained;
- `rd.check_links(Path("."))` is `ok` for every reference (or explained);
- you have looked at every figure;
- `previews/outline.md` holds `rd.outline(Path("."))`, and you have **read it
  end to end** against the source, as the player would: faithful, complete,
  formulas right, every citation linked;
- your final message lists each passage id with a one-line summary of what it
  covers (the story task points its beats at them), and any citation left
  without a link.

Nothing here writes into a game: the story's task (or the game's pull) installs
the reading with `rd.export_reading`, through
`~/games/tools/pull_dex_assets.py <game> --only <this task dir>`.
