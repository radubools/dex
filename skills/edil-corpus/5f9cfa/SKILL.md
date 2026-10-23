---
name: edil-corpus
description: Stage 1 of the construction criteria pipeline — pick eDIL projects, extract their initial-report / estimate PDFs page by page (pypdf vs pdftotext, font-offset repair, Granite-Docling OCR for scanned pages), cut them into token-budgeted chunks flagged for prose quality, and store documents, pages and chunks in the `corpus` Postgres schema that every later stage reads.
---

# eDIL corpus

Four modules:

| Module | Does |
|---|---|
| `edil_sources` | walks the eDIL export, finds each project's init-report PDFs (misspelled folder names included), and `select()`s a deterministic spread across building types |
| `pdf_text` | extracts one PDF with both engines, keeps the one that reads as words, undoes the 29-code-point font offset, scores each page |
| `chunking` | packs each page's paragraphs → sentences → word runs into chunks of ≤ `budget` Clef tokens, remembering their pages; `flag()` marks `usable` |
| `corpus` | the `corpus` schema (`documents`, `pages`, `chunks`) and the queries other stages and agents select through |
| `ingest` | the stage-1 command |
| `ocr` | stage 1b: renders pages extraction could not read, converts them with `ibm/granite-docling` on Ollama, re-chunks keeping unchanged chunks (and their embeddings and decisions) |

## Running it

```bash
cd /Users/raduparadovschi/dex/assets/construction
../../.venv/bin/python -m utils.ingest --projects 30 --dry-run   # see the selection
../../.venv/bin/python -m utils.ingest --projects 30             # the backfill
../../.venv/bin/python -m utils.ingest --projects 30 --skip 30   # the next 30
../../.venv/bin/python -m utils.ingest --building-type Medical --projects 5
../../.venv/bin/python -m utils.ingest --folder 3965_RioBravoMedical_Bakersfield-CA
```

From a task, select arbitrary slices without the CLI:

```python
import asyncio
from utils import corpus, edil_sources, ingest

async def main():
    conn = await corpus.connect()
    picked = edil_sources.select(edil_sources.projects(), 3, building_type="Hospital")
    await ingest.run(conn, picked)
    rows = await corpus.chunks(conn, building_type="Hospital", usable=True)
    await conn.close()
asyncio.run(main())
```

Idempotent: a PDF already stored with the same size, mtime and budget is
skipped. Each document is written in one transaction, so an interrupted run keeps
what it finished. Point `DEX_CORPUS_DATABASE_URL` at `dex_test` for experiments.

## Choices worth knowing

- **Only the init-report folder.** A project's `Init_Report/` (any spelling
  starting `init`) holds its initial cost review / construction analysis /
  estimate — the documents this stage is for. Monthly updates are not read.
- **The budget is in Clef-Flash tokens** (the Qwen3.5 vocabulary), because Clef
  is the model that pays for them. 400 matches the earlier Laya/Jev work, so
  criteria wordings carry over. Bigger chunks are cheaper per criterion — each
  question costs ~100 schema tokens per chunk whatever the chunk's size — at the
  price of diluting a passage and coarsening the neighbour graph.
- **Nothing is dropped.** `usable` is false for tables of figures, OCR garble,
  scanned pages and repeated forms (thresholds from the earlier sweeps:
  function-word ratio ≥ 0.06, digit share ≤ 0.22, garble < 0.05, ≥ 90% from text
  pages, not in a near-identical set of 5+). Later stages read usable chunks by
  default; `usable=None` gets everything.

## OCR for scanned pages

About 900 of the backfill's 1,831 pages had no usable text layer (scanned
appendices, permits, warranties, logs). `python -m utils.ocr` renders each with
`pdftoppm` and converts it with `ibm/granite-docling:258m` on Ollama (522 MB;
`ollama pull ibm/granite-docling:258m`); DocTags become plain text, table cells
` | `-separated, and the result replaces the page only if it reads as more
English than what was there (the original stays in `pages.raw_text`). 13-48 s a
page measured *beside a running Clef round* — run it when the GPU is free.

Pictures Docling finds on those pages — site photos, renderings, plans, charts,
logos, stamps — are cropped and indexed in `corpus.images` with their class and
the caption line beside them (`corpus.images(conn, ...)`). Served through Ollama,
Docling's element tags do not come back, so a picture is a located line holding
only a class tag; see `ocr.pictures`. Checked on a progress-photo page: two
photos, both captioned ("2nd Floor: new wall framing"), cropped cleanly, and
Clef-Flash's image input called the framing one framing at 0.91.

## Errors

`corpus.errors` records what every stage skipped — this skill's ingest and OCR,
and the embedding, Clef and image stages of the other pipeline skills — via
`corpus.record_error(conn, stage, exc, path=..., page=..., chunk_id=...)`, one
row per (stage, item), `attempts` counted, `resolved_at` set by
`resolve_error` when a retry succeeds. `corpus.errors(conn, stage=...)` lists the
open ones.
