"""Stage 2: embed every chunk that has no BGE-M3 vector yet, then rebuild the neighbour graph.

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.embed_stage                  # usable chunks, k=10
    ../../.venv/bin/python -m utils.embed_stage --all --k 15     # junk chunks too, wider graph
    ../../.venv/bin/python -m utils.embed_stage --building-type Medical --no-graph

Incremental: chunks already embedded are skipped, so running it after ingesting
the next 30 projects embeds only theirs. The graph is rebuilt from all stored
embeddings each time (a few seconds), since new chunks change old neighbours.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from . import bge, corpus, neighbors


async def run(conn, *, usable_only: bool = True, k: int = neighbors.K, batch: int = bge.BATCH,
              document_ids: list[str] | None = None, graph: bool = True, log=print) -> dict:
    await corpus.ensure_schema(conn)
    await neighbors.ensure_schema(conn)
    if not bge.available():
        raise bge.EmbedError(f"Ollama at {bge.HOST} is not serving {bge.MODEL}; run `ollama pull bge-m3`")
    todo = await neighbors.missing(conn, usable_only=usable_only, document_ids=document_ids)
    log(f"{len(todo)} chunks to embed with {bge.MODEL}")
    started, done = time.time(), 0
    failed = 0
    for start in range(0, len(todo), batch):
        part = todo[start:start + batch]
        ids = [cid for cid, _ in part]
        try:
            vectors = bge.embed([t for _, t in part])
        except Exception as exc:  # noqa: BLE001 - note the batch's chunks in corpus.errors, carry on
            log(f"  FAILED batch of {len(part)}: {type(exc).__name__}: {exc}")
            for cid in ids:
                await corpus.record_error(conn, "embed", exc, chunk_id=cid)
            failed += len(part)
            continue
        await neighbors.write(conn, ids, vectors)
        await conn.execute(
            f"UPDATE {corpus.SCHEMA}.errors SET resolved_at = now() "
            f"WHERE stage = 'embed' AND chunk_id = ANY($1) AND resolved_at IS NULL", ids)
        done += len(ids)
        if done % (batch * 10) == 0 or done == len(todo):
            rate = done / max(time.time() - started, 1e-6)
            log(f"  {done}/{len(todo)} embedded ({rate:.0f}/s)")
    out = {"embedded": done, "failed": failed, "seconds": round(time.time() - started, 1)}
    if graph:
        out["edges"] = await neighbors.build_graph(conn, k=k)
        out.update(await neighbors.graph_stats(conn))
        log(f"graph: {out['nodes']} nodes, {out['edges']} edges, mean distance "
            f"{out['mean_distance']:.3f}, {out['same_document_share']:.0%} same-document")
    return out


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        ids = None
        if args.building_type or args.folder:
            docs = await corpus.documents(conn, building_type=args.building_type, project_folder=args.folder)
            ids = [d["id"] for d in docs]
        await run(conn, usable_only=not args.all, k=args.k, batch=args.batch,
                  document_ids=ids, graph=not args.no_graph)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="python -m utils.embed_stage", description=__doc__.split("\n")[0])
    p.add_argument("--all", action="store_true", help="embed chunks flagged unusable too")
    p.add_argument("--k", type=int, default=neighbors.K, help="neighbours per chunk in the graph")
    p.add_argument("--batch", type=int, default=bge.BATCH)
    p.add_argument("--building-type")
    p.add_argument("--folder", help="substring of a project folder")
    p.add_argument("--no-graph", action="store_true", help="embed only; leave the graph as it is")
    sys.exit(asyncio.run(_main(p.parse_args())))
