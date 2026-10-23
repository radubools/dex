"""Tune which criteria to accept: search contiguous windows of the confidence ranking against the label.

The coverage rule drops a criterion when too many of its answers are undecided.
Where to draw that line is a choice, and this module lets the label make it:
rank every judged text criterion by how confident Clef is on it (mean
confidence, |P - 0.5| x 2, from its latest judgement), slide windows of several
sizes down that ranking, and score each window's criteria with the same lasso
pipeline (`lasso.fit`, outer-CV AUC or R²).

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.window --target material_change_orders
    ../../.venv/bin/python -m utils.window --sizes 10 20 40 --step 5 --shuffles 20

**Picking the best of many windows on 44 projects flatters it.** So the same
search is re-run on `--shuffles` label permutations, and the real best window is
compared with the distribution of the *best* window under shuffled labels — the
search-corrected p-value. Only a window that beats that is worth adopting as the
acceptance band; a best window inside the null's range is the search finding
noise.

Results go to `criteria.window_searches`: every window's score, the null
maxima, and the corrected p.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import numpy as np

from . import co_labels, corpus, lasso

CRIT = "criteria"

DDL = f"""
CREATE TABLE IF NOT EXISTS {CRIT}.window_searches (
    id          serial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    target      text NOT NULL,
    rank_by     text NOT NULL,
    params      jsonb NOT NULL,
    windows     jsonb NOT NULL,       -- one entry per window: start, size, conf_hi, conf_lo, score
    best        jsonb NOT NULL,
    null_max    jsonb NOT NULL,       -- best-window score per label shuffle
    corrected_p real
);
"""


async def ranking(conn, *, rank_by: str = "mean_confidence") -> list[dict]:
    """Every text criterion with a judgement, most confident first (its latest judgement's numbers)."""
    rows = await conn.fetch(
        f"""SELECT DISTINCT ON (j.criterion_id) j.criterion_id, c.wording, c.status, j.mean_confidence,
                   j.uncertain_share, j.std_p, j.hits
              FROM {CRIT}.judgements j JOIN {CRIT}.criteria c ON c.id = j.criterion_id
             WHERE c.modality = 'text'
               AND coalesce(c.reason, '') NOT LIKE 'removed by operator%'
             ORDER BY j.criterion_id, j.round_id DESC"""
    )
    key = {"mean_confidence": lambda r: -r["mean_confidence"],
           "uncertain_share": lambda r: r["uncertain_share"]}[rank_by]
    return sorted((dict(r) for r in rows), key=key)


def windows(n: int, sizes: list[int], step: int) -> list[tuple[int, int]]:
    out = []
    for size in sizes:
        if size > n:
            continue
        out += [(start, size) for start in range(0, n - size + 1, step)]
        if (n - size) % step:
            out.append((n - size, size))
    return sorted(set(out))


def scan(X: np.ndarray, y: np.ndarray, wins: list[tuple[int, int]], *, target: str, repeats: int,
         seed: int) -> list[float]:
    scores = []
    for start, size in wins:
        sub = X[:, start:start + size]
        sub = sub[:, sub.std(axis=0) > 0]
        if sub.shape[1] == 0:
            scores.append(float("nan"))
            continue
        res = lasso.fit(sub, y, target=target, repeats=repeats, bootstraps=0, seed=seed)
        scores.append(res["cv_score"] if res["cv_score"] is not None else float("nan"))
    return scores


async def run(conn, *, target: str = "material_change_orders", material: float = 0.05,
              rank_by: str = "mean_confidence", sizes: list[int] = (10, 20, 40), step: int = 5,
              shuffles: int = 20, repeats: int = 3, min_billed: float | None = None, seed: int = 0,
              log=print) -> dict:
    await conn.execute(DDL)
    rows, y = lasso.target_vector(await co_labels.labels(conn, min_billed=min_billed), target, material)
    folders = [r["project_folder"] for r in rows]
    ranked = await ranking(conn, rank_by=rank_by)
    X_all, cols = await lasso.features(conn, folders, modalities=("text",), status=None,
                                       ids=[r["criterion_id"] for r in ranked])
    pos = {c["id"]: j for j, c in enumerate(cols)}
    order = [pos[r["criterion_id"]] for r in ranked if r["criterion_id"] in pos]
    ranked = [r for r in ranked if r["criterion_id"] in pos]
    X = X_all[:, order]
    wins = windows(len(ranked), list(sizes), step)
    pos_txt = f"{int(y.sum())} positive" if target in lasso.CLASSIFY else "continuous"
    log(f"{len(folders)} projects ({pos_txt}); {len(ranked)} criteria ranked by {rank_by}; "
        f"{len(wins)} windows x (1 + {shuffles} shuffles)")

    real = await asyncio.to_thread(scan, X, y, wins, target=target, repeats=repeats, seed=seed)
    rng = np.random.default_rng(seed + 7)
    null_max = []
    for k in range(shuffles):
        s = await asyncio.to_thread(scan, X, rng.permutation(y), wins, target=target, repeats=repeats,
                                    seed=seed + k + 1)
        null_max.append(float(np.nanmax(s)))
        log(f"  shuffle {k + 1}/{shuffles}: best window {null_max[-1]:.3f}")

    table = []
    for (start, size), score in zip(wins, real):
        part = ranked[start:start + size]
        table.append({"start": start, "size": size, "score": score,
                      "conf_hi": part[0][rank_by], "conf_lo": part[-1][rank_by]})
    best = max((t for t in table if not np.isnan(t["score"])), key=lambda t: t["score"])
    best["criteria"] = [r["wording"] for r in ranked[best["start"]:best["start"] + best["size"]]]
    p = (1 + sum(m >= best["score"] for m in null_max)) / (1 + len(null_max)) if null_max else None
    sid = await conn.fetchval(
        f"""INSERT INTO {CRIT}.window_searches (target, rank_by, params, windows, best, null_max, corrected_p)
            VALUES ($1,$2,$3::jsonb,$4::jsonb,$5::jsonb,$6::jsonb,$7) RETURNING id""",
        target, rank_by, json.dumps({"sizes": list(sizes), "step": step, "shuffles": shuffles,
                                     "repeats": repeats, "material": material, "min_billed": min_billed,
                                     "projects": len(folders), "criteria": len(ranked)}),
        json.dumps(table), json.dumps(best), json.dumps(null_max), p,
    )
    log(f"search {sid}: best window = ranks {best['start']}-{best['start'] + best['size'] - 1} "
        f"({rank_by} {best['conf_hi']:.3f} .. {best['conf_lo']:.3f}), score {best['score']:.3f}")
    if null_max:
        log(f"  best window under shuffled labels: {np.mean(null_max):.3f} ± {np.std(null_max):.3f}; "
            f"search-corrected p = {p:.3f}")
    for size in sorted(set(sizes)):
        line = [f"{t['score']:.2f}" for t in table if t["size"] == size]
        log(f"  size {size:3d}: " + " ".join(line))
    return {"search": sid, "best": best, "null_max": null_max, "p": p, "table": table}


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        await run(conn, target=args.target, material=args.material, rank_by=args.rank_by, sizes=args.sizes,
                  step=args.step, shuffles=args.shuffles, repeats=args.repeats, min_billed=args.min_billed)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    p = argparse.ArgumentParser(prog="python -m utils.window", description=__doc__.split("\n")[0])
    p.add_argument("--target", choices=("had_change_orders", "material_change_orders", "co_share"),
                   default="material_change_orders")
    p.add_argument("--material", type=float, default=0.05)
    p.add_argument("--rank-by", choices=("mean_confidence", "uncertain_share"), default="mean_confidence")
    p.add_argument("--sizes", type=int, nargs="+", default=[10, 20, 40])
    p.add_argument("--step", type=int, default=5)
    p.add_argument("--shuffles", type=int, default=20)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--min-billed", type=float)
    sys.exit(asyncio.run(_main(p.parse_args())))
