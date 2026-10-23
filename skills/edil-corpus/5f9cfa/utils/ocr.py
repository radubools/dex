"""OCR the pages extraction could not read, with IBM's Granite-Docling on Ollama, and re-chunk.

About half the pages of some init reports are scans — appendices, permits,
warranties, change-order logs — with no text layer, or with a text layer that
is OCR soup. Those pages give no usable chunks. This module renders each such
page (`pdftoppm`, 144 dpi), asks `ibm/granite-docling` to convert it, turns the
DocTags it returns into plain text, and keeps it when it reads better than what
was there. The extracted text is kept in `corpus.pages.raw_text`, the DocTags in
`corpus.pages.doctags`.

**Pictures.** Every picture region Docling marks — site photos, renderings,
plans, charts, logos, stamps — is cropped from the rendered page and indexed in
`corpus.images` (page, pixel and grid box, share of the page, Docling's class, the
caption beside it, the PNG); see `pictures()` for how they are told from text. Boxes under 1% of the page or 48 px a side are skipped as
logos and stamps. `corpus.images(conn, ...)` selects them, and
`clef.ask_image(png, questions)` asks Clef-Flash whether one matches a criterion.

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.ocr                        # every candidate page
    ../../.venv/bin/python -m utils.ocr --folder Fallbrook     # one project
    ../../.venv/bin/python -m utils.ocr --limit 20 --dry-run   # list candidates

Then `utils.embed_stage` embeds whatever chunks changed. A page is a candidate
when it has fewer than `MIN_WORDS` words, reads as fragments (`is_text` false), or
its text is still garbled; a page already OCR'd is skipped.

**Speed.** 13-48 s a page on the M4 Max *while Clef-Flash was also running*,
so do not run it beside a criteria round: they share the one GPU and each slows
the other (Clef fell from 669 to 594 tok/s). Re-chunking keeps every chunk whose
text did not change, so embeddings and criterion decisions on the rest of the
document survive.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import chunking, corpus, pdf_text

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not HOST.startswith("http"):
    HOST = "http://" + HOST
MODEL = "ibm/granite-docling:258m"
PROMPT = "Convert this page to docling."
PDFTOPPM = "/opt/homebrew/bin/pdftoppm"
DPI = 144
MIN_WORDS = 30
#: Output cap per page. A dense page of contract text ran ~1,100 tokens; two pages
#: that ran 330 s at the old 8,192 cap were the model repeating itself.
MAX_TOKENS = 3000
SOURCE = f"ocr:{MODEL}"

_LOC = re.compile(r"<loc_\d+>")
_LINE = re.compile(r"^<loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>(.*)$")
#: How far (grid units, 0-500) a text line may sit from a picture to be its caption.
CAPTION_GAP = 25
#: Picture classes that are page furniture rather than content; indexed, but
#: `image_sweep` skips them unless asked.
FURNITURE = ("logo", "stamp", "signature")
#: Docling's page grid: every location is 0-500 across and down the page.
GRID = 500
#: Smaller boxes are logos, stamps and rules, not pictures worth asking about.
MIN_PAGE_SHARE = 0.01
MIN_SIDE_PX = 48
_CELL = re.compile(r"<(?:fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>")
_TAG = re.compile(r"</?[a-z_0-9]+>")


def doctags_to_text(doctags: str) -> str:
    """Plain text from Granite-Docling's DocTags: locations dropped, table cells as ` | `."""
    text = _LOC.sub("", doctags)
    text = text.replace("<nl>", "\n")
    text = _CELL.sub(" | ", text)
    text = _TAG.sub("", text)
    text = re.sub(r"\$\^\{([^}]*)\}\$", r"\1", text)  # 4$^{th}$ -> 4th
    lines = [" ".join(line.split()).strip(" |") for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _lines(doctags: str) -> list[tuple[list[int], str]]:
    """`(grid box, rest of line)` for every located line of DocTags."""
    out = []
    for line in doctags.splitlines():
        m = _LINE.match(line.strip())
        if m:
            x0, y0, x1, y1 = (int(v) for v in m.groups()[:4])
            out.append(([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], m.group(5)))
    return out


def pictures(doctags: str) -> list[dict]:
    """Picture regions in Granite-Docling's output, with a caption when one sits beside them.

    Served through Ollama, Granite-Docling's element tokens (`<picture>`, `<text>`,
    `<caption>`) are special tokens and do not come back; what does is one line
    per element, `<loc_x0><loc_y0><loc_x1><loc_y1>` then its content. A picture is
    a line whose content is only Docling's class tag — `<other>`, `<logo>`,
    `<stamp>`, `<photograph>`, `<bar_chart>`, ... — and no text. Its caption is
    the nearest text line directly above or below it that overlaps it across the
    page ("2nd Floor: new lighting" over a site photo).
    """
    lines = _lines(doctags)
    texts = [(box, doctags_to_text(rest)) for box, rest in lines if doctags_to_text(rest)]
    out = []
    for box, rest in lines:
        if doctags_to_text(rest):
            continue
        kinds = re.findall(r"<([a-z_]+)>", rest)
        x0, y0, x1, y1 = box
        best, gap_best = None, CAPTION_GAP + 1
        for (tx0, ty0, tx1, ty1), text in texts:
            overlap = min(x1, tx1) - max(x0, tx0)
            if overlap <= 0.3 * min(x1 - x0, tx1 - tx0):
                continue
            gap = y0 - ty1 if ty1 <= y0 else ty0 - y1 if ty0 >= y1 else None
            if gap is not None and 0 <= gap < gap_best:
                best, gap_best = text, gap
        out.append({"grid_bbox": box, "kind": kinds[0] if kinds else None,
                    "caption": best if best and len(best) <= 300 else None})
    return out


def crop(png: bytes, grid_bbox: list[int]) -> tuple[bytes, list[int], int, int]:
    """The region of a rendered page a grid box covers: `(png, pixel bbox, width, height)`."""
    import io

    from PIL import Image

    page = Image.open(io.BytesIO(png))
    w, h = page.size
    x0, y0, x1, y1 = grid_bbox
    box = [round(x0 * w / GRID), round(y0 * h / GRID), round(x1 * w / GRID), round(y1 * h / GRID)]
    region = page.crop(box)
    buf = io.BytesIO()
    region.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), box, region.width, region.height


async def save_pictures(conn, document_id: str, page: int, png: bytes, doctags: str) -> int:
    """Crop and index every picture region on one page, replacing the page's earlier ones."""
    import hashlib

    await conn.execute(f"DELETE FROM {corpus.SCHEMA}.images WHERE document_id = $1 AND page = $2",
                       document_id, page)
    n = 0
    for pic in pictures(doctags):
        x0, y0, x1, y1 = pic["grid_bbox"]
        share = (x1 - x0) * (y1 - y0) / (GRID * GRID)
        if share < MIN_PAGE_SHARE:
            continue
        data, box, w, h = crop(png, pic["grid_bbox"])
        if min(w, h) < MIN_SIDE_PX:
            continue
        iid = hashlib.sha256(f"{document_id}\0{page}\0{pic['grid_bbox']}".encode()).hexdigest()[:16]
        await conn.execute(
            f"""INSERT INTO {corpus.SCHEMA}.images (id, document_id, page, ord, bbox, grid_bbox, page_share,
                    width, height, dpi, kind, caption, png, source)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                ON CONFLICT (id) DO NOTHING""",
            iid, document_id, page, n, box, pic["grid_bbox"], share, w, h, DPI, pic["kind"],
            pic["caption"], data, SOURCE,
        )
        n += 1
    return n


def render(pdf: str, page: int, dpi: int = DPI) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([PDFTOPPM, "-f", str(page), "-l", str(page), "-r", str(dpi), "-png",
                        "-singlefile", pdf, f"{tmp}/p"], check=True, capture_output=True, timeout=300)
        with open(f"{tmp}/p.png", "rb") as fh:
            return fh.read()


def convert(image: bytes, *, model: str = MODEL, timeout: float = 900) -> tuple[str, dict]:
    """`(plain text, raw response)` for one page image, streamed.

    Ollama aborts a generation that repeats one token too often ("prediction
    aborted, token repeat limit reached", HTTP 500 when not streaming). Granite-
    Docling trips it on long ruled tables — runs of empty cells — and a
    repetition penalty does not help, so the same pages failed every time.
    Streaming keeps everything generated before the abort: `raw["truncated"]`
    says so, and the partial page is judged by `better()` like any other.
    """
    body = {"model": model, "prompt": PROMPT, "images": [base64.b64encode(image).decode()],
            "stream": True, "options": {"temperature": 0, "num_predict": MAX_TOKENS}}
    req = urllib.request.Request(HOST.rstrip("/") + "/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    parts: list[str] = []
    raw: dict = {"truncated": False}
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            if not line.strip():
                continue
            msg = json.loads(line)
            if "error" in msg:
                raw.update(truncated=True, error=msg["error"])
                break
            parts.append(msg.get("response", ""))
            if msg.get("done"):
                raw.update({k: v for k, v in msg.items() if k != "response"})
    raw["response"] = "".join(parts)
    if raw["truncated"] and not raw["response"].strip():
        raise RuntimeError(f"Ollama aborted with nothing generated: {raw['error']}")
    return doctags_to_text(raw["response"]), raw


def better(ocr_text: str, old_text: str) -> bool:
    """Keep OCR when it has more words that read as English than the extracted text."""
    def score(t: str) -> float:
        words = re.findall(r"[A-Za-z]+", t)
        return len(words) * pdf_text.english_share(t)
    return score(ocr_text) > score(old_text) * 1.2 + 2


async def candidates(conn, *, building_type: str | None = None, project_folder: str | None = None,
                     limit: int | None = None) -> list[dict]:
    """Pages never OCR'd that are short, fragmentary, or still garbled, in document order."""
    rows = await conn.fetch(
        f"""SELECT d.id AS document_id, d.path, d.filename, p.page, p.text, p.words, p.junk_share, p.is_text
              FROM {corpus.SCHEMA}.pages p JOIN {corpus.SCHEMA}.documents d ON d.id = p.document_id
             WHERE p.source = 'pdf'
               AND ($1::text IS NULL OR d.building_type ILIKE '%' || $1 || '%')
               AND ($2::text IS NULL OR d.project_folder ILIKE '%' || $2 || '%')
             ORDER BY d.building_type, d.project_folder, d.filename, p.page""",
        building_type, project_folder,
    )
    out = [dict(r) for r in rows
           if r["words"] < MIN_WORDS or not r["is_text"]
           or chunking.garble_share(r["text"]) >= chunking.GARBLE]
    return out[:limit] if limit else out


async def rechunk(conn, document_id: str, *, budget: int) -> dict:
    """Re-chunk one document from its stored pages, keeping unchanged chunks."""
    pages = await corpus.pages(conn, document_id)
    chunks = chunking.flag(chunking.split_pages(pages, budget))
    for c in chunks:
        c["id"] = chunking.chunk_id(document_id, c["text"])
    return await corpus.replace_chunks(conn, document_id, chunks)


async def run(conn, *, building_type: str | None = None, project_folder: str | None = None,
              limit: int | None = None, log=print) -> dict:
    await corpus.ensure_schema(conn)
    todo = await candidates(conn, building_type=building_type, project_folder=project_folder, limit=limit)
    log(f"{len(todo)} pages to OCR with {MODEL}")
    touched: set[str] = set()
    kept = tried = images = 0
    started = time.time()
    for i, page in enumerate(todo, 1):
        t0 = time.time()
        try:
            image = await asyncio.to_thread(render, page["path"], page["page"])
            text, raw = await asyncio.to_thread(convert, image)
            doctags = raw.get("response", "")
            await conn.execute(
                f"UPDATE {corpus.SCHEMA}.pages SET doctags = $3 WHERE document_id = $1 AND page = $2",
                page["document_id"], page["page"], doctags)
            pics = await save_pictures(conn, page["document_id"], page["page"], image, doctags)
            images += pics
        except Exception as exc:  # noqa: BLE001 - one unreadable page must not stop the run
            log(f"  FAILED {page['filename']} p{page['page']}: {type(exc).__name__}: {exc}")
            await corpus.record_error(conn, "ocr", exc, document_id=page["document_id"], page=page["page"],
                                      path=page["path"])
            continue
        if raw.get("truncated"):
            await corpus.record_error(conn, "ocr", raw.get("error", "truncated"), severity="warning",
                                      document_id=page["document_id"], page=page["page"], path=page["path"],
                                      detail={"kept_words": len(text.split())})
        else:
            await corpus.resolve_error(conn, "ocr", document_id=page["document_id"], page=page["page"],
                                       path=page["path"])
        tried += 1
        seconds = round(time.time() - t0, 1)
        if better(text, page["text"]):
            share = pdf_text.junk_share(text)
            await conn.execute(
                f"""UPDATE {corpus.SCHEMA}.pages SET raw_text = text, text = $3, words = $4,
                        junk_share = $5, is_text = $6, source = $7, ocr_seconds = $8
                     WHERE document_id = $1 AND page = $2""",
                page["document_id"], page["page"], text, len(text.split()), share,
                share < pdf_text.JUNK_PAGE, SOURCE, seconds,
            )
            touched.add(page["document_id"])
            kept += 1
        else:
            # Recorded as tried, so the next run does not ask again.
            await conn.execute(
                f"UPDATE {corpus.SCHEMA}.pages SET source = 'pdf+ocr-rejected', ocr_seconds = $3 "
                f"WHERE document_id = $1 AND page = $2",
                page["document_id"], page["page"], seconds,
            )
        if i % 10 == 0 or i == len(todo):
            rate = (time.time() - started) / i
            log(f"  {i}/{len(todo)} pages, {kept} kept, {images} pictures, {rate:.1f} s/page, "
                f"~{rate * (len(todo) - i) / 60:.0f} min left")
    # Re-chunk every document that has any OCR'd page, not only the ones this run
    # touched: a run stopped before this point leaves pages replaced but their
    # document's chunks stale, and the next run skips those pages as done.
    # `replace_chunks` keeps unchanged chunks, so an up-to-date document is a no-op.
    touched |= {r["document_id"] for r in await conn.fetch(
        f"SELECT DISTINCT document_id FROM {corpus.SCHEMA}.pages WHERE source = $1", SOURCE)}
    changes = {}
    for did in sorted(touched):
        budget = await conn.fetchval(f"SELECT budget FROM {corpus.SCHEMA}.documents WHERE id = $1", did)
        changes[did] = await rechunk(conn, did, budget=budget or 400)
        if changes[did]["removed"] or changes[did]["added"]:
            log(f"  re-chunked {did}: {changes[did]}")
    return {"pages": len(todo), "tried": tried, "kept": kept, "pictures": images, "documents": len(touched),
            "seconds": round(time.time() - started)}


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        if args.dry_run:
            await corpus.ensure_schema(conn)
            todo = await candidates(conn, building_type=args.building_type,
                                    project_folder=args.folder, limit=args.limit)
            for p in todo:
                print(f"{p['filename']} p{p['page']}: {p['words']} words, junk {p['junk_share']:.2f}")
            print(f"{len(todo)} candidate pages")
            return 0
        print(json.dumps(await run(conn, building_type=args.building_type, project_folder=args.folder,
                                   limit=args.limit), indent=2))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="python -m utils.ocr", description=__doc__.split("\n")[0])
    p.add_argument("--building-type")
    p.add_argument("--folder", help="substring of a project folder")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true")
    sys.exit(asyncio.run(_main(p.parse_args())))
