## `chunking`

Cut extracted pages into token-budgeted chunks and flag the ones worth asking about.

- `TOKENIZER_REPO` — constant
- `FUNCTION_WORDS` — constant
- `PROSE_RATIO` — constant
- `DIGIT_SHARE` — constant
- `GARBLE` — constant
- `DUPLICATE_OVERLAP` — constant
- `DUPLICATE_GROUP` — constant
- `TEXT_PAGE_SHARE` — constant
- `token_counter(repo: str=TOKENIZER_REPO) -> Callable[[str], int]` — 
- `chunk_id(document_id: str, text: str) -> str` — 16 hex characters of sha256 over the document id and the chunk's text.
- `split_pages(pages: list[dict], budget: int=400, *, count: Callable[[str], int] | None=None) -> list[dict]` — Pack the pages' atoms into chunks of at most ~`budget` tokens.
- `prose_ratio(text: str) -> float` — 
- `digit_share(text: str) -> float` — 
- `garble_share(text: str) -> float` — Share of four-letter-plus words shaped like OCR: no vowel, or a lower-then-upper inside.
- `duplicates(texts: list[str], *, threshold: float=DUPLICATE_OVERLAP, group: int=DUPLICATE_GROUP) -> set[int]` — Indexes of texts in a near-identical set of `group` or more (a pre-printed form).
- `flag(chunks: list[dict], *, ratio: float=PROSE_RATIO, digits: float=DIGIT_SHARE, garble: float=GARBLE, text_share: float=TEXT_PAGE_SHARE) -> list[dict]` — Attach the quality measurements and a `usable` verdict to each chunk, in place.

## `corpus`

The `corpus` schema: documents, their pages, and their chunks, in the dex Postgres.

- `DATABASE_URL` — constant
- `SCHEMA` — constant
- `DDL` — constant
- `document_id(path: Path | str) -> str` — 
- `connect(url: str | None=None) -> asyncpg.Connection` — 
- `ensure_schema(conn: asyncpg.Connection) -> None` — 
- `known(conn: asyncpg.Connection, path: Path) -> dict | None` — The stored document row for `path`, or None.
- `write_document(conn: asyncpg.Connection, meta: dict[str, Any], extraction: dict, chunks: list[dict], *, budget: int, tokenizer: str) -> str` — Replace one document — its row, pages and chunks — in a single transaction.
- `documents(conn: asyncpg.Connection, *, building_type: str | None=None, project_folder: str | None=None) -> list[dict]` — Stored documents, optionally narrowed by a substring of the type or project folder.
- `chunks(conn: asyncpg.Connection, *, document_ids: list[str] | None=None, building_type: str | None=None, project_folder: str | None=None, usable: bool | None=True, limit: int | None=None) -> list[dict]` — Chunks in document order, with their document's type and folder.
- `pages(conn: asyncpg.Connection, document_id: str) -> list[dict]` — 
- `replace_chunks(conn: asyncpg.Connection, document_id: str, chunks: list[dict]) -> dict` — Swap a document's chunks for `chunks`, keeping every chunk whose text did not change.
- `images(conn: asyncpg.Connection, *, document_ids: list[str] | None=None, building_type: str | None=None, project_folder: str | None=None, min_page_share: float=0.0, with_png: bool=False, limit: int | None=None) -> list[dict]` — Indexed picture regions, with their document's type and folder. `png` only if asked.
- `error_key(*, path=None, document_id=None, page=None, chunk_id=None, criterion_id=None, image_id=None) -> str` — The item a stage failed on, as one string: `doc:<id>/p<page>`, `chunk:<id>`, ...
- `document_id_of(path) -> str` — 
- `record_error(conn: asyncpg.Connection, stage: str, exc: BaseException | str, *, severity: str='error', detail: dict | None=None, **where) -> None` — Note that `stage` skipped an item; a repeat bumps `attempts` and reopens it.
- `resolve_error(conn: asyncpg.Connection, stage: str, **where) -> None` — Mark an item's open error for `stage` resolved, if there is one.
- `errors(conn: asyncpg.Connection, *, stage: str | None=None, open_only: bool=True, severity: str | None=None) -> list[dict]` — Recorded errors, newest first — what to revisit.

## `edil_sources`

Find eDIL projects and their initial-report PDFs, and pick a spread of them.

- `DATASET` — constant
- `INIT_PREFIX` — constant
- `class Project` — 
- `init_pdfs(project: Path) -> list[Path]` — Every PDF under any init-report-type subfolder of one project folder.
- `projects(dataset: Path=DATASET, *, with_pdf: bool=True) -> list[Project]` — Every project folder in the export, sorted by building type then folder name.
- `select(candidates: list[Project], n: int | None=None, *, building_type: str | None=None, folders: list[str] | None=None, seed: int=0, skip: int=0) -> list[Project]` — A deterministic spread of projects: round-robin over building types.

## `ingest`

Stage 1: extract and chunk the initial-report PDFs of a selection of eDIL projects.

- `DEFAULT_BUDGET` — constant
- `run(conn, projects: list[edil_sources.Project], *, budget: int=DEFAULT_BUDGET, force: bool=False, workers: int | None=None, log=print) -> list[dict]` — Ingest every init PDF of `projects`. Returns one summary dict per PDF.

## `ocr`

OCR the pages extraction could not read, with IBM's Granite-Docling on Ollama, and re-chunk.

- `HOST` — constant
- `MODEL` — constant
- `PROMPT` — constant
- `PDFTOPPM` — constant
- `DPI` — constant
- `MIN_WORDS` — constant
- `MAX_TOKENS` — constant
- `SOURCE` — constant
- `CAPTION_GAP` — constant
- `FURNITURE` — constant
- `GRID` — constant
- `MIN_PAGE_SHARE` — constant
- `MIN_SIDE_PX` — constant
- `doctags_to_text(doctags: str) -> str` — Plain text from Granite-Docling's DocTags: locations dropped, table cells as ` | `.
- `pictures(doctags: str) -> list[dict]` — Picture regions in Granite-Docling's output, with a caption when one sits beside them.
- `crop(png: bytes, grid_bbox: list[int]) -> tuple[bytes, list[int], int, int]` — The region of a rendered page a grid box covers: `(png, pixel bbox, width, height)`.
- `save_pictures(conn, document_id: str, page: int, png: bytes, doctags: str) -> int` — Crop and index every picture region on one page, replacing the page's earlier ones.
- `render(pdf: str, page: int, dpi: int=DPI) -> bytes` — 
- `convert(image: bytes, *, model: str=MODEL, timeout: float=900) -> tuple[str, dict]` — `(plain text, raw response)` for one page image, streamed.
- `better(ocr_text: str, old_text: str) -> bool` — Keep OCR when it has more words that read as English than the extracted text.
- `candidates(conn, *, building_type: str | None=None, project_folder: str | None=None, limit: int | None=None) -> list[dict]` — Pages never OCR'd that are short, fragmentary, or still garbled, in document order.
- `rechunk(conn, document_id: str, *, budget: int) -> dict` — Re-chunk one document from its stored pages, keeping unchanged chunks.
- `run(conn, *, building_type: str | None=None, project_folder: str | None=None, limit: int | None=None, log=print) -> dict` — 

## `pdf_text`

Extract a PDF's text page by page with two engines and keep the one that reads as words.

- `PDFTOTEXT` — constant
- `FONT_OFFSET` — constant
- `WORDINESS` — constant
- `JUNK_PAGE` — constant
- `wordiness(text: str) -> float` — Share of a text's alphabetic runs that are three letters or longer.
- `junk_share(text: str) -> float` — Share of words that are fragments: under three letters, or letters mixed with digits.
- `english_share(text: str) -> float` — Share of a text's words that are common English words.
- `OFFSETS` — constant
- `unshift(text: str) -> tuple[str, bool]` — A page's text with a font offset undone, and whether undoing it helped.
- `by_pypdf(pdf: Path) -> list[str]` — 
- `by_pdftotext(pdf: Path, *, timeout: float=300) -> list[str]` — 
- `score(pages: list[str]) -> dict` — 
- `extract(pdf: Path) -> dict` — Both engines' text for `pdf`, the winner chosen, per-page quality attached.
- `document(pages: list[dict]) -> str` — The pages joined into one text, each preceded by a `[page N]` marker.