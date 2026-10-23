"""Stage 1: extract and chunk the initial-report PDFs of a selection of eDIL projects.

Run from the project directory, outside dex or inside a task:

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.ingest --projects 30              # the backfill
    ../../.venv/bin/python -m utils.ingest --projects 30 --skip 30    # the next 30
    ../../.venv/bin/python -m utils.ingest --building-type Medical --projects 5
    ../../.venv/bin/python -m utils.ingest --folder 3965_RioBravoMedical_Bakersfield-CA
    ../../.venv/bin/python -m utils.ingest --projects 30 --dry-run    # list, write nothing

Or from Python: `await ingest.run(conn, edil_sources.select(...), budget=400)`.

Idempotent: a PDF already stored with the same size, mtime and budget is skipped
unless `--force`. Extraction runs in a process pool — pypdf and pdftotext are
CPU-bound and a 221-page report takes a while — and each finished document is
written in its own transaction, so an interrupted run keeps what it finished.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import chunking, corpus, edil_sources, pdf_text

DEFAULT_BUDGET = 400


def _process(pdf: str, budget: int) -> dict:
    """Extract, chunk and flag one PDF. Runs in a worker process."""
    started = time.time()
    extraction = pdf_text.extract(Path(pdf))
    chunks = chunking.flag(chunking.split_pages(extraction["pages"], budget))
    did = corpus.document_id(pdf)
    for c in chunks:
        c["id"] = chunking.chunk_id(did, c["text"])
    return {"extraction": extraction, "chunks": chunks, "seconds": round(time.time() - started, 1)}


async def run(conn, projects: list[edil_sources.Project], *, budget: int = DEFAULT_BUDGET,
              force: bool = False, workers: int | None = None, log=print) -> list[dict]:
    """Ingest every init PDF of `projects`. Returns one summary dict per PDF."""
    await corpus.ensure_schema(conn)
    jobs = []
    for project in projects:
        for pdf in project.init_pdfs:
            row = await corpus.known(conn, pdf)
            st = pdf.stat()
            if row and not force and row["size"] == st.st_size and row["mtime"] == st.st_mtime \
                    and row["budget"] == budget:
                log(f"skip   {project.key} / {pdf.name} (already ingested)")
                continue
            jobs.append((project, pdf))

    summaries = []
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 4) - 2))
    if not jobs:
        return summaries
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, str(pdf), budget): (project, pdf) for project, pdf in jobs}
        for future in as_completed(futures):
            project, pdf = futures[future]
            try:
                out = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop the backfill
                log(f"FAILED {project.key} / {pdf.name}: {type(exc).__name__}: {exc}")
                await corpus.record_error(conn, "ingest", exc, path=pdf,
                                          detail={"project": project.key})
                summaries.append({"project": project.key, "pdf": str(pdf), "error": str(exc)})
                continue
            meta = {
                "building_type": project.building_type,
                "project_folder": project.folder,
                "subfolder": str(pdf.parent.relative_to(project.path)),
            }
            await corpus.write_document(conn, meta, out["extraction"], out["chunks"],
                                        budget=budget, tokenizer=chunking.TOKENIZER_REPO)
            await corpus.resolve_error(conn, "ingest", path=pdf)
            usable = sum(c["usable"] for c in out["chunks"])
            pages = out["extraction"]["pages"]
            log(f"ok     {project.key} / {pdf.name}: {len(pages)} pages, "
                f"{len(out['chunks'])} chunks ({usable} usable), {out['extraction']['engine']}, "
                f"{out['seconds']}s")
            summaries.append({"project": project.key, "pdf": str(pdf), "pages": len(pages),
                              "chunks": len(out["chunks"]), "usable": usable,
                              "engine": out["extraction"]["engine"]})
    return summaries


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m utils.ingest", description=__doc__.split("\n")[0])
    p.add_argument("--projects", type=int, help="how many projects (spread across building types)")
    p.add_argument("--skip", type=int, default=0, help="skip this many from the same ordering")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--building-type", help="substring of the building-type folder name")
    p.add_argument("--folder", action="append", help="a project folder name (repeatable)")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="tokens per chunk")
    p.add_argument("--workers", type=int)
    p.add_argument("--force", action="store_true", help="re-ingest even if unchanged")
    p.add_argument("--dry-run", action="store_true", help="print the selection and stop")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    return p


async def _main(args) -> int:
    if not (args.projects or args.folder or args.building_type):
        print("name a selection: --projects N, --folder NAME, or --building-type TEXT", file=sys.stderr)
        return 2
    picked = edil_sources.select(edil_sources.projects(), args.projects, building_type=args.building_type,
                                 folders=args.folder, seed=args.seed, skip=args.skip)
    print(f"{len(picked)} projects, {sum(len(p.init_pdfs) for p in picked)} init PDFs")
    if args.dry_run:
        for p in picked:
            print(f"  {p.key}: " + "; ".join(x.name for x in p.init_pdfs))
        return 0
    conn = await corpus.connect()
    try:
        summaries = await run(conn, picked, budget=args.budget, force=args.force, workers=args.workers)
    finally:
        await conn.close()
    if args.json:
        print(json.dumps(summaries, indent=2))
    failed = [s for s in summaries if "error" in s]
    print(f"done: {len(summaries) - len(failed)} written, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(_parser().parse_args())))
