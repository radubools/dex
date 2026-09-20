## `chunking`

Cut extracted document text into paragraph chunks, CJK-aware.

- `SENTENCE_END` — constant
- `TRAILING` — constant
- `DEFAULT_LIMIT` — constant
- `indented_lines(text: str) -> list[tuple[int, str]]` — Return (indent, stripped text) per line, with blank lines as (-1, '').
- `join_lines(parts: list[str]) -> str` — Rejoin one paragraph's wrapped lines, joining CJK directly and spacing only Latin-to-Latin.
- `blocks_by_indent(text: str) -> list[str]` — Split a laid-out column into joined paragraphs on first-line indent (or a blank line).
- `blocks_by_blank_line(text: str) -> list[str]` — Split text into joined paragraphs on blank lines, for sources with no indent signal.
- `split_at_sentences(text: str, limit: int=DEFAULT_LIMIT) -> list[str]` — Split a paragraph longer than `limit` at sentence ends only, never mid-sentence.

## `mediawiki`

Pull the readable article out of a MediaWiki page as ordered blocks.

- `DROP_SELECTORS` — constant
- `class ExtractionFailed` — Raised when a page does not look like a MediaWiki article at all.
- `clean_text(element) -> str` — Return an element's visible text with non-breaking and zero-width spaces normalised.
- `heading_level(div, default: int=2) -> int` — Read the heading level off a `mw-heading<N>` wrapper, falling back to `default`.
- `article_blocks(html: str, drop: tuple[str, ...]=DROP_SELECTORS) -> list[dict]` — Return the article's title, headings, paragraphs, captions and references, in order.