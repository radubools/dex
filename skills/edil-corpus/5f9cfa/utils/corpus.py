"""The `corpus` schema: documents, their pages, and their chunks, in the dex Postgres.

Every later stage reads from here — embeddings join on `corpus.chunks.id`, the
criteria loop scores `corpus.chunks` — so this is the one place a chunk exists.
A document is keyed by its path (`document_id`), a chunk by its document and its
text (`chunking.chunk_id`), so re-ingesting an unchanged PDF writes nothing new
and changing the chunk budget replaces that document's chunks wholesale.

    import asyncio
    from utils import corpus

    async def main():
        conn = await corpus.connect()
        await corpus.ensure_schema(conn)
        rows = await corpus.chunks(conn, building_type="Medical", usable=True)
        print(len(rows), rows[0]["text"][:80])
        await conn.close()
    asyncio.run(main())

`DEX_CORPUS_DATABASE_URL` overrides the database (default: the dex database on
127.0.0.1); point it at `dex_test` for anything that is not the real corpus.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import asyncpg

DATABASE_URL = os.environ.get("DEX_CORPUS_DATABASE_URL", "postgresql://127.0.0.1/dex")
SCHEMA = "corpus"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.documents (
    id             text PRIMARY KEY,
    path           text NOT NULL UNIQUE,
    building_type  text NOT NULL,
    project_folder text NOT NULL,
    subfolder      text NOT NULL,
    filename       text NOT NULL,
    size           bigint NOT NULL,
    mtime          double precision NOT NULL,
    engine         text,
    engines        jsonb NOT NULL DEFAULT '{{}}',
    errors         jsonb NOT NULL DEFAULT '{{}}',
    shifted_pages  int[] NOT NULL DEFAULT '{{}}',
    page_count     int NOT NULL DEFAULT 0,
    words          int NOT NULL DEFAULT 0,
    budget         int,
    tokenizer      text,
    ingested_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_project ON {SCHEMA}.documents (building_type, project_folder);
CREATE TABLE IF NOT EXISTS {SCHEMA}.pages (
    document_id text NOT NULL REFERENCES {SCHEMA}.documents(id) ON DELETE CASCADE,
    page        int NOT NULL,
    text        text NOT NULL,
    words       int NOT NULL,
    junk_share  real NOT NULL,
    is_text     boolean NOT NULL,
    font_shifted boolean NOT NULL DEFAULT false,
    PRIMARY KEY (document_id, page)
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.chunks (
    id              text PRIMARY KEY,
    document_id     text NOT NULL REFERENCES {SCHEMA}.documents(id) ON DELETE CASCADE,
    ord             int NOT NULL,
    text            text NOT NULL,
    tokens          int NOT NULL,
    pages           int[] NOT NULL,
    text_page_share real NOT NULL,
    prose_ratio     real NOT NULL,
    digit_share     real NOT NULL,
    garble_share    real NOT NULL,
    repeated_form   boolean NOT NULL,
    usable          boolean NOT NULL,
    UNIQUE (document_id, ord)
);
CREATE INDEX IF NOT EXISTS chunks_usable ON {SCHEMA}.chunks (usable, document_id);
-- OCR (the `ocr` module) replaces a page's text and keeps what extraction gave.
ALTER TABLE {SCHEMA}.pages ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'pdf';
ALTER TABLE {SCHEMA}.pages ADD COLUMN IF NOT EXISTS raw_text text;
ALTER TABLE {SCHEMA}.pages ADD COLUMN IF NOT EXISTS ocr_seconds real;
ALTER TABLE {SCHEMA}.pages ADD COLUMN IF NOT EXISTS doctags text;
-- Picture regions OCR found, cropped from the rendered page: what Clef-Flash's
-- image input reads (`clef.ask_image`).
CREATE TABLE IF NOT EXISTS {SCHEMA}.images (
    id          text PRIMARY KEY,
    document_id text NOT NULL REFERENCES {SCHEMA}.documents(id) ON DELETE CASCADE,
    page        int NOT NULL,
    ord         int NOT NULL,
    bbox        int[] NOT NULL,          -- x0, y0, x1, y1 in pixels of the rendered page
    grid_bbox   int[] NOT NULL,          -- the same box on Docling's 0-500 page grid
    page_share  real NOT NULL,           -- the box's share of the page area
    width       int NOT NULL,
    height      int NOT NULL,
    dpi         int NOT NULL,
    kind        text,                    -- Docling's picture class, when it gives one
    caption     text,
    png         bytea NOT NULL,
    source      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, page, ord)
);
-- Everything a stage skipped, to revisit: one row per failing item and stage,
-- attempts counted, resolved_at set when a later attempt succeeds.
CREATE TABLE IF NOT EXISTS {SCHEMA}.errors (
    id           serial PRIMARY KEY,
    stage        text NOT NULL,           -- ingest | ocr | embed | clef | image
    severity     text NOT NULL DEFAULT 'error',   -- error | warning
    item         text NOT NULL,           -- stable key of what failed, see `error_key`
    path         text,
    document_id  text,
    page         int,
    chunk_id     text,
    criterion_id int,
    image_id     text,
    error_type   text NOT NULL,
    message      text NOT NULL,
    detail       jsonb NOT NULL DEFAULT '{{}}',
    attempts     int NOT NULL DEFAULT 1,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    last_seen    timestamptz NOT NULL DEFAULT now(),
    resolved_at  timestamptz,
    UNIQUE (stage, item)
);
CREATE INDEX IF NOT EXISTS errors_open ON {SCHEMA}.errors (stage, resolved_at);
"""


def document_id(path: Path | str) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:16]


async def connect(url: str | None = None) -> asyncpg.Connection:
    return await asyncpg.connect(url or DATABASE_URL)


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def known(conn: asyncpg.Connection, path: Path) -> dict | None:
    """The stored document row for `path`, or None."""
    row = await conn.fetchrow(f"SELECT * FROM {SCHEMA}.documents WHERE path = $1", str(path))
    return dict(row) if row else None


async def write_document(conn: asyncpg.Connection, meta: dict[str, Any], extraction: dict,
                         chunks: list[dict], *, budget: int, tokenizer: str) -> str:
    """Replace one document — its row, pages and chunks — in a single transaction."""
    path = Path(extraction["pdf"])
    did = document_id(path)
    stat = path.stat()
    pages = extraction["pages"]
    async with conn.transaction():
        await conn.execute(f"DELETE FROM {SCHEMA}.documents WHERE id = $1", did)
        await conn.execute(
            f"""INSERT INTO {SCHEMA}.documents
                (id, path, building_type, project_folder, subfolder, filename, size, mtime,
                 engine, engines, errors, shifted_pages, page_count, words, budget, tokenizer)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)""",
            did, str(path), meta["building_type"], meta["project_folder"], meta["subfolder"],
            path.name, stat.st_size, stat.st_mtime, extraction["engine"],
            json.dumps(extraction["engines"]), json.dumps(extraction["errors"]),
            extraction["font_offset_shifted_pages"], len(pages),
            sum(p["words"] for p in pages), budget, tokenizer,
        )
        await conn.executemany(
            f"""INSERT INTO {SCHEMA}.pages (document_id, page, text, words, junk_share, is_text, font_shifted)
                VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            [(did, p["page"], p["text"], p["words"], p["junk_share"], p["is_text"],
              p["font_offset_shifted"]) for p in pages],
        )
        await conn.executemany(
            f"""INSERT INTO {SCHEMA}.chunks (id, document_id, ord, text, tokens, pages, text_page_share,
                    prose_ratio, digit_share, garble_share, repeated_form, usable)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (id) DO NOTHING""",
            [(c["id"], did, c["ord"], c["text"], c["tokens"], c["pages"], c["text_page_share"],
              c["prose_ratio"], c["digit_share"], c["garble_share"], c["repeated_form"], c["usable"])
             for c in chunks],
        )
    return did


async def documents(conn: asyncpg.Connection, *, building_type: str | None = None,
                    project_folder: str | None = None) -> list[dict]:
    """Stored documents, optionally narrowed by a substring of the type or project folder."""
    rows = await conn.fetch(
        f"""SELECT d.*, (SELECT count(*) FROM {SCHEMA}.chunks c WHERE c.document_id = d.id) AS chunks,
                   (SELECT count(*) FROM {SCHEMA}.chunks c WHERE c.document_id = d.id AND c.usable) AS usable
              FROM {SCHEMA}.documents d
             WHERE ($1::text IS NULL OR d.building_type ILIKE '%' || $1 || '%')
               AND ($2::text IS NULL OR d.project_folder ILIKE '%' || $2 || '%')
             ORDER BY d.building_type, d.project_folder, d.filename""",
        building_type, project_folder,
    )
    return [dict(r) for r in rows]


async def chunks(conn: asyncpg.Connection, *, document_ids: list[str] | None = None,
                 building_type: str | None = None, project_folder: str | None = None,
                 usable: bool | None = True, limit: int | None = None) -> list[dict]:
    """Chunks in document order, with their document's type and folder.

    Every filter is optional, so an agent can select an arbitrary slice:
    `chunks(conn, project_folder="RioBravo")`, `chunks(conn, usable=None)` for
    all of them, junk included.
    """
    rows = await conn.fetch(
        f"""SELECT c.*, d.building_type, d.project_folder, d.filename
              FROM {SCHEMA}.chunks c JOIN {SCHEMA}.documents d ON d.id = c.document_id
             WHERE ($1::text[] IS NULL OR c.document_id = ANY($1))
               AND ($2::text IS NULL OR d.building_type ILIKE '%' || $2 || '%')
               AND ($3::text IS NULL OR d.project_folder ILIKE '%' || $3 || '%')
               AND ($4::boolean IS NULL OR c.usable = $4)
             ORDER BY d.building_type, d.project_folder, d.filename, c.ord
             LIMIT $5""",
        document_ids, building_type, project_folder, usable, limit,
    )
    return [dict(r) for r in rows]


async def pages(conn: asyncpg.Connection, document_id: str) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT page, text, words, junk_share, is_text FROM {SCHEMA}.pages WHERE document_id = $1 ORDER BY page",
        document_id,
    )
    return [dict(r) for r in rows]


async def replace_chunks(conn: asyncpg.Connection, document_id: str, chunks: list[dict]) -> dict:
    """Swap a document's chunks for `chunks`, keeping every chunk whose text did not change.

    A chunk's id is its document and text, so an unchanged chunk keeps its id —
    and with it its embedding and every criterion decision about it. Only chunks
    whose text changed are deleted (cascading to their embeddings and decisions)
    and inserted fresh. Returns `{"kept", "removed", "added"}`.
    """
    new = {c["id"]: c for c in chunks}
    async with conn.transaction():
        old = {r["id"] for r in await conn.fetch(
            f"SELECT id FROM {SCHEMA}.chunks WHERE document_id = $1", document_id)}
        gone = sorted(old - set(new))
        await conn.execute(f"DELETE FROM {SCHEMA}.chunks WHERE id = ANY($1)", gone)
        # Park the survivors' ords out of the way so renumbering cannot collide.
        await conn.execute(f"UPDATE {SCHEMA}.chunks SET ord = -1 - ord WHERE document_id = $1", document_id)
        await conn.executemany(
            f"""INSERT INTO {SCHEMA}.chunks (id, document_id, ord, text, tokens, pages, text_page_share,
                    prose_ratio, digit_share, garble_share, repeated_form, usable)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (id) DO UPDATE SET ord = EXCLUDED.ord, pages = EXCLUDED.pages,
                    text_page_share = EXCLUDED.text_page_share, repeated_form = EXCLUDED.repeated_form,
                    usable = EXCLUDED.usable""",
            [(c["id"], document_id, c["ord"], c["text"], c["tokens"], c["pages"], c["text_page_share"],
              c["prose_ratio"], c["digit_share"], c["garble_share"], c["repeated_form"], c["usable"])
             for c in chunks],
        )
    return {"kept": len(old & set(new)), "removed": len(gone), "added": len(set(new) - old)}


async def images(conn: asyncpg.Connection, *, document_ids: list[str] | None = None,
                 building_type: str | None = None, project_folder: str | None = None,
                 min_page_share: float = 0.0, with_png: bool = False, limit: int | None = None) -> list[dict]:
    """Indexed picture regions, with their document's type and folder. `png` only if asked."""
    cols = "i.*" if with_png else ", ".join(f"i.{c}" for c in (
        "id", "document_id", "page", "ord", "bbox", "grid_bbox", "page_share", "width", "height",
        "dpi", "kind", "caption", "source", "created_at"))
    rows = await conn.fetch(
        f"""SELECT {cols}, d.building_type, d.project_folder, d.filename
              FROM {SCHEMA}.images i JOIN {SCHEMA}.documents d ON d.id = i.document_id
             WHERE ($1::text[] IS NULL OR i.document_id = ANY($1))
               AND ($2::text IS NULL OR d.building_type ILIKE '%' || $2 || '%')
               AND ($3::text IS NULL OR d.project_folder ILIKE '%' || $3 || '%')
               AND i.page_share >= $4
             ORDER BY d.building_type, d.project_folder, i.page, i.ord
             LIMIT $5""",
        document_ids, building_type, project_folder, min_page_share, limit,
    )
    return [dict(r) for r in rows]


def error_key(*, path=None, document_id=None, page=None, chunk_id=None, criterion_id=None,
              image_id=None) -> str:
    """The item a stage failed on, as one string: `doc:<id>/p<page>`, `chunk:<id>`, ..."""
    parts = []
    if document_id or path:
        parts.append(f"doc:{document_id or document_id_of(path)}")
    if page is not None:
        parts.append(f"p{page}")
    if chunk_id:
        parts.append(f"chunk:{chunk_id}")
    if image_id:
        parts.append(f"image:{image_id}")
    if criterion_id is not None:
        parts.append(f"criterion:{criterion_id}")
    return "/".join(parts) or "unknown"


def document_id_of(path) -> str:
    return document_id(path)


async def record_error(conn: asyncpg.Connection, stage: str, exc: BaseException | str, *,
                       severity: str = "error", detail: dict | None = None, **where) -> None:
    """Note that `stage` skipped an item; a repeat bumps `attempts` and reopens it.

    `where` takes `path`, `document_id`, `page`, `chunk_id`, `criterion_id`,
    `image_id` — whatever identifies the item. Never raises: losing the note is
    better than losing the run.
    """
    etype = type(exc).__name__ if isinstance(exc, BaseException) else "message"
    msg = str(exc)[:2000]
    try:
        await conn.execute(
            f"""INSERT INTO {SCHEMA}.errors (stage, severity, item, path, document_id, page, chunk_id,
                    criterion_id, image_id, error_type, message, detail)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                ON CONFLICT (stage, item) DO UPDATE SET attempts = {SCHEMA}.errors.attempts + 1,
                    last_seen = now(), resolved_at = NULL, severity = EXCLUDED.severity,
                    error_type = EXCLUDED.error_type, message = EXCLUDED.message, detail = EXCLUDED.detail""",
            stage, severity, error_key(**where), str(where["path"]) if where.get("path") else None,
            where.get("document_id") or (document_id(where["path"]) if where.get("path") else None),
            where.get("page"), where.get("chunk_id"), where.get("criterion_id"), where.get("image_id"),
            etype, msg, json.dumps(detail or {}),
        )
    except Exception:  # noqa: BLE001
        pass


async def resolve_error(conn: asyncpg.Connection, stage: str, **where) -> None:
    """Mark an item's open error for `stage` resolved, if there is one."""
    await conn.execute(
        f"UPDATE {SCHEMA}.errors SET resolved_at = now() WHERE stage = $1 AND item = $2 AND resolved_at IS NULL",
        stage, error_key(**where),
    )


async def errors(conn: asyncpg.Connection, *, stage: str | None = None, open_only: bool = True,
                 severity: str | None = None) -> list[dict]:
    """Recorded errors, newest first — what to revisit."""
    rows = await conn.fetch(
        f"""SELECT e.*, d.filename FROM {SCHEMA}.errors e LEFT JOIN {SCHEMA}.documents d ON d.id = e.document_id
             WHERE ($1::text IS NULL OR e.stage = $1) AND (NOT $2 OR e.resolved_at IS NULL)
               AND ($3::text IS NULL OR e.severity = $3)
             ORDER BY e.last_seen DESC""",
        stage, open_only, severity,
    )
    return [dict(r) for r in rows]
