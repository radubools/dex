"""Run the stages in sequence, outside dex: ingest -> OCR -> embed -> criteria rounds.

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.pipeline backfill --projects 30 \\
        --seeds /Volumes/external/construction/dex-backups/2026-10-07/criteria-seed.json \\
        --pick 40 --rounds 3 --generate 20

    # later: grow the corpus and carry on
    ../../.venv/bin/python -m utils.pipeline backfill --projects 30 --skip 30 --rounds 2 --generate 20

Each stage is idempotent, so re-running the same command after an interruption
picks up where it stopped: unchanged PDFs are not re-extracted, embedded chunks
are not re-embedded, decided (criterion, chunk) pairs are not re-asked. Round 1
evaluates the picked seeds as they are; later rounds let the generator write
`--generate` new criteria first.

Log to a file when it runs unattended: `... backfill ... 2>&1 | tee backfill.log`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from . import corpus, criteria_loop, criteria_store, edil_sources, embed_stage, ingest, ocr


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


async def backfill(args) -> int:
    conn = await corpus.connect()
    try:
        _log(f"stage 1: ingest {args.projects} projects (skip {args.skip})")
        picked = edil_sources.select(edil_sources.projects(), args.projects, skip=args.skip,
                                     seed=args.seed, building_type=args.building_type)
        summaries = await ingest.run(conn, picked, budget=args.budget, log=_log)
        failed = [s for s in summaries if "error" in s]
        _log(f"stage 1 done: {len(summaries) - len(failed)} documents written, {len(failed)} failed")

        if not args.no_ocr:
            _log(f"stage 1b: OCR unreadable pages with {ocr.MODEL}")
            out = await ocr.run(conn, log=_log)
            _log(f"stage 1b done: {out['kept']} of {out['tried']} pages replaced, "
                 f"{out['documents']} documents re-chunked")

        _log("stage 2: embed + neighbour graph")
        await embed_stage.run(conn, k=args.k_graph, log=_log)

        if args.no_rounds:
            return 0
        _log("stage 3: criteria")
        await criteria_store.ensure_schema(conn)
        if args.seeds:
            rows = json.loads(Path(args.seeds).read_text())
            total = await criteria_store.import_seeds(conn, rows, source=Path(args.seeds).stem)
            _log(f"  {total} seeds stored")
        have = await conn.fetchval(f"SELECT count(*) FROM {criteria_store.SCHEMA}.criteria")
        if not have and args.pick:
            await criteria_loop.pick_seeds(conn, args.pick, log=_log)
        params = dict(probes_per_document=args.probes, budget_tokens=args.budget_tokens,
                      workers=args.workers)
        for r in range(args.rounds):
            generate = args.generate if (r > 0 or have) else 0
            await criteria_loop.run_round(conn, generate=generate, log=_log, **params)
        print(json.dumps(await criteria_loop.status(conn), indent=2, default=str))
    finally:
        await conn.close()
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m utils.pipeline", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--projects", type=int, default=30)
    b.add_argument("--skip", type=int, default=0)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--building-type")
    b.add_argument("--budget", type=int, default=ingest.DEFAULT_BUDGET, help="tokens per chunk")
    b.add_argument("--k-graph", type=int, default=10)
    b.add_argument("--seeds", help="criteria-seed.json to import")
    b.add_argument("--pick", type=int, default=40, help="seed criteria for round 1")
    b.add_argument("--rounds", type=int, default=3)
    b.add_argument("--generate", type=int, default=20)
    b.add_argument("--probes", type=int, help="probe chunks per document")
    b.add_argument("--budget-tokens", type=int, help="Clef tokens per round")
    b.add_argument("--workers", type=int, help="Clef instances on the GPU")
    b.add_argument("--no-rounds", action="store_true", help="stages 1 and 2 only")
    b.add_argument("--no-ocr", action="store_true", help="skip OCR of unreadable pages")
    return p


if __name__ == "__main__":
    sys.exit(asyncio.run(backfill(_parser().parse_args())))
