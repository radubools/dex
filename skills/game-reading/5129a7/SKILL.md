---
name: game-reading
description: The source behind a lesson in the games in ~/games — for each concept a story teaches, a faithful and complete retelling of what the book or paper says about it, in our own words, with every idea and equation (LaTeX, with its number) kept, figures drawn from the data, page references, a link to read it in the source and every publication it cites linked to its DOI, arXiv or publisher page (scouted on the web and checked), in one kind "reading" package per story that the game shows behind the story card's book button.
---

# Game reading — the source behind a lesson

A lesson retells an idea in a few short cards. Behind the card's **book
button** the player can read the whole of what the source says about the
concept the beat is on: its argument, its equations, its figures and the
papers it cites, each one link away, and a link to read it in the source
itself (engine `design/27_reading.md`). You make that text, as a package of
kind `reading`, one per story.

```python
from pathlib import Path
from utils import reading as rd
```

Read `utils/API.md` for every signature.

## Our words, all of the ideas

A passage is a **faithful, complete retelling in your own words** of what the
source says about one concept, **not the source's text**. Quoting a book at
length would republish it; a retelling with page references and a link to
the original does not. So:

- **Lose nothing.** Every idea, step, assumption, caveat, example and result
  the source gives for the concept is in your passage, in the source's order.
  Retell it closely, sentence by sentence if that is how the argument goes,
  but in your own sentences. Do not shorten the argument to save words: a
  passage is as long as the source's treatment needs.
- **Equations are kept exactly**, in LaTeX, with the source's numbers
  (`\qquad (3.41)`), and the derivation steps the source shows. Notation is
  the source's.
- **Quote only where the wording itself matters** (a definition, a famous
  line), at most a sentence or two, marked as a quotation (`> …` or quotation
  marks) with its page.
- **Cite what the source cites**, as `#ref:` links, where it cites them.
- Say where it is (`where`: chapter, section, pages) and link to the source
  (`url`, or the work's `url`), so the player can read the original.
- Never copy whole paragraphs, and never paste the source's text to edit it:
  write from your understanding of it.

## The package

Named after its story (`reading/llm-svd` for `story/llm-svd`), in this task's
package directory, tagged `type:reading` and `story:<name>` (write_reading
adds both):

```
reading.json    {schema 1, name, story, work, passages, references}
figures/*.png   the passages' figures, drawn for the reading
previews/outline.md   the whole reading as one document, for review
```

```python
rd.write_reading(Path("."), name="eslr-ridge", story="story/eslr-ridge",
    work={"title": "The Elements of Statistical Learning", "authors": ["Trevor Hastie", "Robert Tibshirani", "Jerome Friedman"],
          "year": 2009, "edition": "2nd ed.", "publisher": "Springer",
          "url": "https://hastie.su.domains/ElemStatLearn/printings/ESLII_print12_toc.pdf.download.html"},
    passages=[{"id": "ridge-penalty", "title": "Ridge regression: a price on size", "where": "Chapter 3, Section 3.4.1, pp. 61–63",
               "markdown": "Ridge regression keeps all the inputs but shrinks their coefficients by charging for their size [Hoerl and Kennard, 1970](#ref:hoerl-kennard-1970) …",
               "refs": ["hoerl-kennard-1970"]}],
    references={"hoerl-kennard-1970": {"text": "Hoerl, A. E. and Kennard, R. (1970). Ridge regression: biased estimation for nonorthogonal problems, Technometrics 12: 55–67.",
                                       "url": "https://doi.org/10.1080/00401706.1970.10488634"}})
```

- **`passages`**: one per concept your brief names, with **exactly the ids the
  brief gives** (the story's beats point at them). `title` names the concept
  (the source's section title is fine); `where` says where it is in the
  source; `url` optionally links to that place (else the work's `url` is used).
- **`markdown`**: the retelling. `$…$` inline maths and `$$…$$` displays (a `$$`
  block on lines of its own), fenced code, tables, lists, quotes. A figure is
  an image alone in its paragraph, `![What the figure shows](figures/ridge-paths.png)`,
  captioned by its alt text. A citation is a link to `#ref:<id>`. Other links
  must be http(s).
- **`references`**: every work a passage cites, by id (`<first-author>-<year>`),
  its full citation (as the source's bibliography gives it), and the `url` of
  the publication.

## Where the sources are (the ML project)

| Source | Its text, to read | Equations | Data for figures | Bibliography | Work `url` |
|---|---|---|---|---|---|
| *The Elements of Statistical Learning*, 2nd ed. (`eslr-` lessons) | `~/dex/datasets/ml/eslr/raw/chNN.txt` (from the PDF, `<<<page N>>>` book-page markers) and the PDF itself, `~/dex/datasets/ml/ESLR.pdf` (**PDF page = book page + 19**) | `~/dex/datasets/ml/eslr/chNN-*.md` (study notes, every numbered equation in LaTeX) | `~/dex/datasets/ml/eslr/*.data`, `eslr/raw/*.data` (prostate, spam, SAheart, ozone, nci, zip) | the PDF's References, book pp. 699–745: `pdftotext -f 718 -l 764 ESLR.pdf - \| grep -A3 "Hoerl"` | `https://hastie.su.domains/ElemStatLearn/printings/ESLII_print12_toc.pdf.download.html` (the authors' free edition) |
| *The Mathematics of Large Language Models* (Karpeles) (`llm-` lessons) | `~/dex/datasets/ml/mathematics-of-llms/chNN-*.md` | in the text | none | none: the text names people, works and years; find each publication yourself | the book's publisher or listing page: find it |

Read the source's whole treatment of the concept before you write, so the
retelling misses nothing; `rd.pdf_text(PDF, page)` reads a PDF page.

## Figures: draw them, don't cut them

Don't copy the source's figures. When a figure carries an idea the passage
needs, **draw your own version** of it with matplotlib, from the source's data
where it has some (ESL's datasets above: Figure 3.8's ridge paths come from
the prostate data), else from the formula or a small simulation, and say in
the caption what it shows and which figure of the source it follows
("After Figure 3.8 of ESL: …"). Save it under `figures/` (PNG, at most 1400
px wide, light background, labelled axes), and **look at every figure you
make**. Where no honest redraw is possible, describe the figure in the text and
point to its page.

`rd.pdf_page_png(PDF, page, out)` renders a source page so you can look at
the original figure while you redraw it; the rendered page stays out of the
package.

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
- you have looked at every figure you drew;
- `previews/outline.md` holds `rd.outline(Path("."))`, and you have **read it
  end to end beside the source**: every idea of the source's treatment is
  there, in your own words, equations exact, every citation linked, no
  paragraph copied;
- your final message lists each passage id with a one-line summary of what it
  covers (the story task points its beats at them), and any citation left
  without a link.

Nothing here writes into a game: the story's task (or the game's pull) installs
the reading with `rd.export_reading`, through
`~/games/tools/pull_dex_assets.py <game> --only <this task dir>`.
