## `pdf_slice`

Slice a reading's prose out of a PDF's own text by start and end phrases, so passages are never retyped.

- `LIGATURES` — constant
- `MATHISH` — constant
- `page_text(pdf: str | Path, page: int) -> str` — A PDF page's text (1-based PDF page), NFC-normalised with ligatures undone; line breaks kept.
- `hyphenated_words(texts) -> frozenset` — The lowercased hyphenated words ("least-squares") printed within a line anywhere in `texts`.
- `join_lines(text: str, keep_hyphen: frozenset=frozenset()) -> str` — Text as one line: end-of-line hyphens dropped unless the word is in `keep_hyphen`, dashes rejoined.
- `between(text: str, start: str, end: str) -> str` — `text` from the first `start` through the next `end`, both included; ValueError naming the one missing.
- `substitute(text: str, subs) -> str` — Apply exact (old, new) replacements in order; ValueError if an `old` is absent, so stale fixes surface.
- `mathish_fragments(text: str, *, pad: int=10, merge: int=12, ignore=('a', 'A', 'I')) -> list[str]` — Windows around what still looks like extracted maths outside $…$ and #ref: links (close hits merged).

## `reading`

Readings: the original text behind a lesson, for the games in ~/games.

- `KIND` — constant
- `FILE` — constant
- `FIGURES` — constant
- `SCHEMA` — constant
- `EXPORT_SUBDIR` — constant
- `NAME` — constant
- `REF_ID` — constant
- `STORY` — constant
- `IMAGE` — constant
- `LINK` — constant
- `FIGURE_TYPES` — constant
- `FIGURE_MAX` — constant
- `WORDS_SOFT` — constant
- `UA` — constant
- `write_reading(package: str | Path, *, name: str, story: str, work: dict, passages: list[dict], references: dict[str, dict], tags: Iterable[str]=()) -> dict` — Write reading.json and manifest.json; returns the manifest.
- `read(package: str | Path) -> dict` — The package's reading.json.
- `passage_ids(package: str | Path) -> list[str]` — The ids of a reading's passages, in order: what a story's beats may name.
- `math_problems(md: str) -> list[str]` — Unbalanced maths in a passage: a $$ or a single $ left open.
- `validate_reading(package: str | Path) -> dict` — {ok, errors, warnings}: a reading the game can show, every citation resolvable.
- `check_links(package: str | Path, *, timeout: float=15.0) -> list[dict]` — Fetch every reference URL (and the work's): [{id, url, status, ok, final}].
- `pdf_page_png(pdf: str | Path, page: int, out: str | Path, *, dpi: int=150) -> Path` — One PDF page (1-based, the PDF's own numbering) rendered to a PNG, to look at or crop.
- `pdf_text(pdf: str | Path, page: int) -> str` — A PDF page's text, to find the page a passage or figure is on.
- `pdf_pages_text(pdf: str | Path, first: int, last: int, *, drop_lines: Iterable[str]=()) -> str` — PDF pages first..last (the PDF's numbering) as one NFC-normalised line of prose, page-number lines and `drop_lines` (running heads) removed.
- `span(text: str, start: str, end: str) -> str` — The verbatim text from marker `start` through the first `end` after it; ValueError if `start` is not found exactly once or `end` is missing.
- `numbered_equations(md: str) -> dict[str, str]` — Display equations `$$… (9.4)$$` in a markdown text, by number ('9.4' -> the LaTeX inside the $$); the first wins.
- `pdf_figure(pdf: str | Path, page: int, out: str | Path, *, crop: tuple[float, float, float, float] | None=None, dpi: int=150, max_width: int=1400, trim: bool=True) -> Path` — A figure cut from a PDF page into a PNG at `out` (figures/<name>.png).
- `outline(package: str | Path) -> str` — The reading as one markdown document: each passage, then its references. Write it to previews/outline.md.
- `export_reading(package: str | Path, game_root: str | Path) -> dict` — Install a reading into `<game_root>/public/assets/reading/<name>/` and rebuild the index.

## `reading_verbatim`

Prose of a reading passage that does not occur in the source text: what a verbatim check must look at.

- `unquoted_runs(markdown: str, source: str, *, min_chars: int=25, phrase_chars: int=12) -> list[str]` — Prose runs of a passage (between formulas, figures, citations and […] cuts) not found in `source`; a run that crosses a page break is checked phrase by phrase.

## `verbatim`

Check a reading passage's prose against the source text it was copied from, letters only.

- `letters(text: str) -> str` — The text NFKC-normalised, lowercased, with everything but a–z removed (spacing, hyphenation, punctuation).
- `prose_sentences(markdown: str) -> list[str]` — A passage's prose sentences, with maths, figures, tables, footnote marks and bold subheadings taken out; citation links keep their text.
- `unmatched_prose(markdown: str, source_text: str, *, min_letters: int=12) -> list[str]` — The passage's prose sentences (of at least `min_letters` letters) not found letter-for-letter in `source_text`.