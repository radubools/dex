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
- `pdf_figure(pdf: str | Path, page: int, out: str | Path, *, crop: tuple[float, float, float, float] | None=None, dpi: int=150, max_width: int=1400, trim: bool=True) -> Path` — A figure cut from a PDF page into a PNG at `out` (figures/<name>.png).
- `outline(package: str | Path) -> str` — The reading as one markdown document: each passage, then its references. Write it to previews/outline.md.
- `export_reading(package: str | Path, game_root: str | Path) -> dict` — Install a reading into `<game_root>/public/assets/reading/<name>/` and rebuild the index.