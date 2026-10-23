"""Stage 3: generate criteria, evaluate them with Clef-Flash over probes and neighbour walks, keep the useful ones.

    cd /Users/raduparadovschi/dex/assets/construction
    PY=../../.venv/bin/python
    $PY -m utils.criteria_loop seed --from /Volumes/external/construction/dex-backups/2026-10-07/criteria-seed.json
    $PY -m utils.criteria_loop pick --n 40            # round-0 candidates from the seeds
    $PY -m utils.criteria_loop round                  # evaluate + judge the candidates
    $PY -m utils.criteria_loop round --generate 20    # LLM writes 20 more first, then the same
    $PY -m utils.criteria_loop run --rounds 5 --generate 20
    $PY -m utils.criteria_loop status

One round:

1. *(optional)* the local LLM proposes `--generate` new criteria from what the
   last round kept and dropped and from passages nothing covers yet;
2. every live criterion (candidate or kept) is asked about the probe set —
   only pairs never asked before, so a kept criterion costs nothing again
   until new documents arrive;
3. from each probe hit the walk steps to neighbours, asks, and steps again from
   the hits, up to `--depth`, until the round's `--budget` of Clef tokens is
   spent;
4. `usefulness.measure` + `verdict` judge every live criterion; the verdicts go
   to `criteria.usefulness` and each criterion becomes `kept` or `dropped`.

Every decision is written as soon as its chunk is answered, so a round stopped
halfway resumes for free: the next round finds those pairs already decided.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
from pathlib import Path

from . import bge, clef, corpus, coverage, criteria_store as store, generator, neighbors, usefulness

#: Schema tokens one yes/no question adds to a record (instructions + two option descriptions).
TOKENS_PER_QUESTION = 105
DEFAULTS = {
    "probes_per_document": 10,
    "walk_k": 5,
    "max_depth": 4,
    "threshold": 0.5,
    "budget_tokens": 2_500_000,
    "workers": clef.WORKERS,
}


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


async def _texts(conn, chunk_ids: list[str]) -> dict[str, tuple[str, int]]:
    rows = await conn.fetch(f"SELECT id, text, tokens FROM {corpus.SCHEMA}.chunks WHERE id = ANY($1)", chunk_ids)
    return {r["id"]: (r["text"], r["tokens"]) for r in rows}


async def evaluate(conn, plan: dict[str, set[int]], questions: dict[int, dict], *, how: str,
                   round_id: int, budget: dict, log=_log) -> int:
    """Ask each planned chunk its criteria; write as it goes. Returns tokens spent."""
    if not plan:
        return 0
    texts = await _texts(conn, list(plan))
    order = sorted(plan)
    est = sum(texts[c][1] + TOKENS_PER_QUESTION * len(plan[c]) for c in order)
    log(f"  {how}: {len(order)} chunks, {sum(len(v) for v in plan.values())} pairs, "
        f"~{est:,} tokens (~{est / clef.TOKENS_PER_SECOND / 60:.0f} min)")
    per_chunk = {cid: {f"c{c}": questions[c] for c in sorted(plan[cid])} for cid in order}
    submitted = {"n": 0}

    def items():
        # Lazily, so the budget stops new submissions rather than discarding finished work.
        for cid in order:
            if budget["left"] <= 0:
                log(f"  budget spent after {submitted['n']}/{len(order)} chunks; "
                    "the rest waits for the next round")
                return
            submitted["n"] += 1
            yield cid, texts[cid][0]

    # Always through worker processes, even one: MLX work stays off this
    # process's threads, and the event loop stays free to write as results land.
    stream = budget["pool"].ask_each(items(), per_chunk, per_item=True, errors="yield")
    spent, started, i = 0, time.time(), 0
    it = iter(stream)
    while True:
        # The pool blocks on GPU work; keep the event loop (and the connection) free meanwhile.
        got = await asyncio.to_thread(next, it, None)
        if got is None:
            break
        cid, result = got
        i += 1
        if isinstance(result, BaseException):
            log(f"    FAILED chunk {cid}: {type(result).__name__}: {result}")
            await corpus.record_error(conn, "clef", result, chunk_id=cid,
                                      detail={"criteria": sorted(plan[cid]), "how": how, "round": round_id})
            continue
        await conn.execute(
            f"UPDATE {corpus.SCHEMA}.errors SET resolved_at = now() "
            f"WHERE stage = 'clef' AND chunk_id = $1 AND resolved_at IS NULL", cid)
        rows = [(int(q[1:]), cid, a["noul"], how) for q, a in result["answers"].items()]
        await store.write_decisions(conn, rows, round_id=round_id, evaluator=clef.MODEL)
        used = result["usage"]["input_tokens"]
        spent += used
        budget["left"] -= used
        if i % 25 == 0 or i == len(order):
            rate = spent / max(time.time() - started, 1e-6)
            log(f"    {i}/{len(order)} chunks, {spent:,} tokens, {rate:.0f} tok/s")
    return spent


async def run_round(conn, *, generate: int = 0, note: str | None = None,
                    rule: usefulness.Rule | None = None, log=_log, **params) -> dict:
    p = {**DEFAULTS, **{k: v for k, v in params.items() if v is not None}}
    rule = rule or usefulness.Rule(threshold=p["threshold"])
    await store.ensure_schema(conn)
    rid = await store.start_round(conn, evaluator=clef.MODEL,
                                  generator=generator.MODEL if generate else None,
                                  params={**p, "generate": generate, "rule": rule.__dict__}, note=note)
    log(f"round {rid}: {json.dumps(p)}")
    probes = await usefulness.probe_set(conn, per_document=p["probes_per_document"])
    log(f"  {len(probes)} probe chunks")

    if generate:
        added = await _generate(conn, rid, generate, probes, p["threshold"], log=log)
        log(f"  generator added {len(added)} criteria")

    live = await store.criteria(conn, status=["candidate", "kept"])
    if not live:
        log("  nothing to evaluate: run `pick` or `--generate N`")
        return {"round": rid, "criteria": 0}
    questions = {c["id"]: c["question"] for c in live}
    ids = list(questions)
    budget = {"left": p["budget_tokens"], "pool": clef.Pool(workers=p["workers"])}
    try:
        spent = await _evaluate_round(conn, rid, ids, questions, probes, p, budget, log)
    finally:
        budget["pool"].close()

    measured = await usefulness.measure(conn, ids, probes, threshold=p["threshold"], k=p["walk_k"],
                                        max_depth=p["max_depth"])
    verdicts = [usefulness.verdict(m, rule) for m in measured.values()]
    await store.judge(conn, rid, verdicts)
    kept = sum(v["keep"] for v in verdicts)
    stats = {"criteria": len(verdicts), "kept": kept, "dropped": len(verdicts) - kept,
             "tokens": spent, "probes": len(probes)}
    await store.finish_round(conn, rid, stats)
    log(f"round {rid} done: {kept} kept, {len(verdicts) - kept} dropped, {spent:,} Clef tokens")
    return {"round": rid, **stats}


async def _evaluate_round(conn, rid, ids, questions, probes, p, budget, log) -> int:

    done = await store.decided(conn, ids)
    plan: dict[str, set[int]] = {}
    for ch in probes:
        for c in ids:
            if (c, ch) not in done:
                plan.setdefault(ch, set()).add(c)
    spent = await evaluate(conn, plan, questions, how="probe", round_id=rid, budget=budget, log=log)

    frontier = await usefulness.hits_among(conn, [(c, ch) for c in ids for ch in probes], p["threshold"])
    visited = set(frontier)
    log(f"  {len(frontier)} probe hits to walk from")
    for depth in range(1, p["max_depth"] + 1):
        if not frontier or budget["left"] <= 0:
            break
        plan = await usefulness.walk_plan(conn, ids, threshold=p["threshold"], k=p["walk_k"],
                                          frontier=frontier)
        log(f"  depth {depth}: {len(frontier)} frontier hits")
        spent += await evaluate(conn, plan, questions, how="walk", round_id=rid, budget=budget, log=log)
        frontier = await usefulness.neighbor_hits(conn, frontier, threshold=p["threshold"],
                                                  k=p["walk_k"]) - visited
        visited |= frontier
    return spent


def screen_leakage(proposals: list[dict], log=_log) -> list[dict]:
    """Refuse proposals whose meaning sits too close to the change-order target (BGE-M3 cosine).

    The generator's prompt and its wording filter (`generator.implies_target`) catch
    the explicit cases; this catches paraphrases ("the GMP was raised after ...").
    """
    import numpy as np

    if not proposals:
        return proposals
    targets = np.asarray(bge.embed(generator.TARGET_DESCRIPTIONS))
    words = [p["question"]["instructions"] for p in proposals]
    sims = (np.asarray(bge.embed(words)) @ targets.T).max(axis=1)
    kept = []
    for p, w, sim in zip(proposals, words, sims):
        if sim >= generator.LEAKAGE_SIMILARITY:
            log(f"  refused (implies the target, cosine {sim:.2f}): {w}")
        else:
            kept.append(p)
    return kept


async def _generate(conn, rid: int, n: int, probes: list[str], threshold: float, log=_log) -> list[int]:
    kept = await store.criteria(conn, status="kept", modality="text")
    last = await conn.fetchval(f"SELECT max(judged_in) FROM {store.SCHEMA}.criteria WHERE modality = 'text'")
    dropped = [c for c in await store.criteria(conn, status="dropped", modality="text")
               if c["judged_in"] == last]
    rows = await conn.fetch(
        f"""SELECT c.id, c.text FROM {corpus.SCHEMA}.chunks c
             WHERE c.id = ANY($1) AND NOT EXISTS (
                   SELECT 1 FROM {store.SCHEMA}.decisions d JOIN {store.SCHEMA}.criteria k
                       ON k.id = d.criterion_id AND k.status = 'kept'
                    WHERE d.chunk_id = c.id AND d.p >= $2 AND k.modality = 'text')""",
        probes, threshold,
    )
    rng = random.Random(rid)
    uncovered = [r["text"] for r in rng.sample(list(rows), min(8, len(rows)))]
    log(f"  generating {n} with {generator.MODEL} ({len(kept)} kept, {len(dropped)} dropped, "
        f"{len(rows)} uncovered probes)")
    proposals = await asyncio.to_thread(generator.propose, kept, dropped, uncovered, n=n)
    proposals = await asyncio.to_thread(screen_leakage, proposals, log)
    by_name = {c["name"]: c["id"] for c in kept + dropped}
    items = [{"question": pr["question"], "parent_id": by_name.get(pr.get("revises") or "")}
             for pr in proposals]
    revised = [i for i in items if i["parent_id"]]
    fresh = [i for i in items if not i["parent_id"]]
    return (await store.add_criteria(conn, revised, origin="revised", round_id=rid)
            + await store.add_criteria(conn, fresh, origin="generated", round_id=rid))


async def run_coverage_round(conn, *, generate: int = 0, share: float = 0.75,
                             rule: coverage.Rule | None = None, budget_tokens: int = 40_000_000,
                             workers: int | None = None, note: str | None = None, log=_log) -> dict:
    """One coverage round: (generate) -> ask every live criterion about the coverage set -> judge.

    Live = candidate + kept. Only undecided (criterion, chunk) pairs are asked, so
    a criterion already covered costs nothing until new documents arrive. A
    criterion short of the rule's coverage stays a candidate (pending).
    """
    rule = rule or coverage.Rule()
    await store.ensure_schema(conn)
    await coverage.ensure_schema(conn)
    params = {"mode": "coverage", "share": share, "generate": generate,
              "budget_tokens": budget_tokens, "rule": coverage.rule_dict(rule)}
    rid = await store.start_round(conn, evaluator=clef.MODEL,
                                  generator=generator.MODEL if generate else None, params=params, note=note)
    cover = await coverage.coverage_set(conn, share=share)
    total = await coverage.usable_total(conn)
    log(f"round {rid} (coverage): {len(cover)} of {total} usable chunks ({len(cover) / max(total, 1):.0%})")
    if generate:
        added = await _generate(conn, rid, generate, cover, 0.5, log=log)
        log(f"  generator added {len(added)} criteria")
    live = await store.criteria(conn, status=["candidate", "kept"], modality="text")
    if not live:
        log("  nothing to evaluate")
        return {"round": rid, "criteria": 0}
    questions = {c["id"]: c["question"] for c in live}
    ids = list(questions)
    done = await store.decided(conn, ids)
    plan: dict[str, set[int]] = {}
    for ch in cover:
        for c in ids:
            if (c, ch) not in done:
                plan.setdefault(ch, set()).add(c)
    budget = {"left": budget_tokens, "pool": clef.Pool(workers=workers or clef.WORKERS)}
    try:
        spent = await evaluate(conn, plan, questions, how="cover", round_id=rid, budget=budget, log=log)
    finally:
        budget["pool"].close()
    profiles = await coverage.profile(conn, ids, cover, band=rule.band)
    verdicts = coverage.judge(profiles, rule)
    await coverage.record(conn, rid, verdicts)
    counts = {k: sum(v["verdict"] == k for v in verdicts) for k in ("kept", "dropped", "pending")}
    stats = {"criteria": len(verdicts), **counts, "tokens": spent, "cover": len(cover)}
    await store.finish_round(conn, rid, stats)
    log(f"round {rid} done: {counts['kept']} kept, {counts['dropped']} dropped, "
        f"{counts['pending']} pending, {spent:,} Clef tokens")
    for v in sorted(verdicts, key=lambda v: v["verdict"]):
        if v["verdict"] == "dropped":
            log(f"    dropped {v['criterion_id']}: {v['reason']}")
    return {"round": rid, **stats}


async def reopen(conn, *, log=_log) -> int:
    """Put every dropped criterion back to candidate, so the coverage rule judges it afresh."""
    n = await conn.fetchval(
        f"""WITH r AS (UPDATE {store.SCHEMA}.criteria SET status = 'candidate',
                       reason = 'reopened: ' || coalesce(reason, '') WHERE status = 'dropped' RETURNING 1)
            SELECT count(*) FROM r""")
    log(f"reopened {n} dropped criteria")
    return n


async def pick_seeds(conn, n: int = 40, *, top: int = 15, log=_log) -> list[int]:
    """Round-0 candidates: the `top` best-scoring seeds, then a diverse spread of the rest.

    Every distinct seed wording is embedded with BGE-M3; those whose nearest
    chunk is closer than the median are "about this corpus", and among those
    farthest-point sampling picks wordings unlike everything picked so far.
    """
    import numpy as np

    rows = await conn.fetch(
        f"""SELECT DISTINCT ON (lower(wording)) name, question, best_j FROM {store.SCHEMA}.seeds
             WHERE question->>'type' = 'noul'
               -- Question form only: a declarative seed ("The contract does not say how
               -- disputes are resolved.") fired on 100% of probes in the first test round.
               AND rtrim(wording) LIKE '%?'
             ORDER BY lower(wording), best_j DESC NULLS LAST"""
    )
    seeds = [{"name": r["name"], "question": json.loads(r["question"]), "best_j": r["best_j"]} for r in rows]
    if not seeds:
        raise SystemExit("no seeds: run `seed --from <criteria-seed.json>` first")
    log(f"embedding {len(seeds)} distinct seed wordings")
    vecs = []
    for _, v in bge.embed_batches(((s["name"], s["question"]["instructions"]) for s in seeds), batch=256):
        vecs.extend(v)
    S = np.asarray(vecs, dtype=np.float32)
    chunk_rows = await conn.fetch(f"SELECT embedding::text AS e FROM {corpus.SCHEMA}.embeddings")
    C = np.asarray([json.loads(r["e"]) for r in chunk_rows], dtype=np.float32)
    nearest = (S @ C.T).max(axis=1)
    relevant = np.where(nearest >= np.median(nearest))[0]

    ranked = sorted((i for i in relevant if seeds[i]["best_j"] is not None),
                    key=lambda i: -seeds[i]["best_j"])
    chosen = ranked[:top]
    pool = [i for i in relevant if i not in set(chosen)]
    if not chosen:
        chosen = [int(pool.pop(int(np.argmax(nearest[pool]))))]
    while len(chosen) < n and pool:
        sim = (S[pool] @ S[chosen].T).max(axis=1)
        chosen.append(pool.pop(int(np.argmin(sim))))
    items = [{"question": seeds[i]["question"], "name": seeds[i]["name"].split(".")[-1]} for i in chosen]
    ids = await store.add_criteria(conn, items, origin="seed", round_id=None)
    log(f"picked {len(ids)} seed criteria ({min(top, len(ranked))} by score, the rest for spread)")
    return ids


async def status(conn) -> dict:
    await store.ensure_schema(conn)
    counts = {r["status"]: r["n"] for r in await conn.fetch(
        f"SELECT status, count(*) AS n FROM {store.SCHEMA}.criteria GROUP BY 1")}
    rounds = [dict(r) for r in await conn.fetch(
        f"SELECT id, started_at, finished_at, stats FROM {store.SCHEMA}.rounds ORDER BY id DESC LIMIT 5")]
    kept = await conn.fetch(
        f"""SELECT c.name, c.wording, u.probe_rate, u.hit_documents, u.mean_depth, u.walk_precision
              FROM {store.SCHEMA}.criteria c JOIN {store.SCHEMA}.usefulness u
                ON u.criterion_id = c.id AND u.round_id = c.judged_in
             WHERE c.status = 'kept' ORDER BY u.hit_documents DESC, u.mean_depth DESC NULLS LAST LIMIT 25"""
    )
    return {"criteria": counts, "rounds": rounds, "top_kept": [dict(r) for r in kept]}


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        await store.ensure_schema(conn)
        if args.cmd == "seed":
            rows = json.loads(Path(args.source).read_text())
            total = await store.import_seeds(conn, rows, source=Path(args.source).stem)
            _log(f"{total} seeds stored")
        elif args.cmd == "pick":
            await pick_seeds(conn, args.n, top=args.top)
        elif args.cmd in ("round", "run"):
            params = dict(probes_per_document=args.probes, walk_k=args.k, max_depth=args.depth,
                          threshold=args.threshold, budget_tokens=args.budget, workers=args.workers)
            for _ in range(args.rounds if args.cmd == "run" else 1):
                await run_round(conn, generate=args.generate, note=args.note, **params)
        elif args.cmd == "reopen":
            await reopen(conn)
        elif args.cmd == "cover":
            rule = coverage.Rule(min_documents=args.min_documents, min_coverage=args.min_coverage)
            for _ in range(args.rounds):
                await run_coverage_round(conn, generate=args.generate, share=args.share, rule=rule,
                                         budget_tokens=args.budget, note=args.note)
        elif args.cmd == "status":
            print(json.dumps(await status(conn), indent=2, default=str))
    finally:
        await conn.close()
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m utils.criteria_loop", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="import a criteria-seed.json")
    s.add_argument("--from", dest="source", required=True)
    s = sub.add_parser("pick", help="choose round-0 candidates from the seeds")
    s.add_argument("--n", type=int, default=40)
    s.add_argument("--top", type=int, default=15)
    for name in ("round", "run"):
        s = sub.add_parser(name)
        s.add_argument("--generate", type=int, default=0, help="criteria the LLM writes first")
        s.add_argument("--probes", type=int, help="probe chunks per document")
        s.add_argument("--k", type=int, help="neighbours per walk step")
        s.add_argument("--depth", type=int, help="walk depth")
        s.add_argument("--threshold", type=float)
        s.add_argument("--budget", type=int, help="Clef tokens per round")
        s.add_argument("--workers", type=int, help="Clef instances on the GPU")
        s.add_argument("--note")
        if name == "run":
            s.add_argument("--rounds", type=int, default=3)
    s = sub.add_parser("cover", help="coverage rounds: judge on >70%% of chunks, drop only "
                                     "non-confident, low-variance or correlated criteria")
    s.add_argument("--generate", type=int, default=0)
    s.add_argument("--rounds", type=int, default=1)
    s.add_argument("--share", type=float, default=0.75, help="share of each document's usable chunks")
    s.add_argument("--min-documents", type=int, default=5)
    s.add_argument("--min-coverage", type=float, default=0.70)
    s.add_argument("--budget", type=int, default=40_000_000, help="Clef tokens per round")
    s.add_argument("--note")
    sub.add_parser("reopen", help="dropped -> candidate, for re-judging")
    sub.add_parser("status")
    return p


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(_parser().parse_args())))
