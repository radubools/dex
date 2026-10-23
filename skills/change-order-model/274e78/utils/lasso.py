"""Fit the kept criteria to the change-order label with an L1 (lasso) model, and say how far to trust it.

Each project is one row. Each kept criterion is one feature: the share of the
project's init-report chunks it fires on (P ≥ 0.5, over the chunks it was asked
about) — or, for an image criterion, the share of the project's pictures. The
label comes from `co_labels`.

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.lasso                       # had_change_orders, logistic
    ../../.venv/bin/python -m utils.lasso --min-billed 0.5      # only negatives far enough along
    ../../.venv/bin/python -m utils.lasso --target co_share     # continuous: COs / initial contract

What it reports, and why each piece is there:

- **outer cross-validated AUC** (or R² for `co_share`): the whole pipeline —
  scaling, the inner CV that picks the L1 strength, the fit — is re-run inside
  every outer fold, so the number is not flattered by having chosen the
  penalty on the same projects. With a few dozen projects it is noisy; the
  spread over repeats is printed beside it.
- **coefficients** of the final fit on all projects (standardised features, so
  comparable in size), non-zero ones only.
- **noise checks**: `--status dropped` fits the criteria the rule threw out, and
  `--permutations N` refits on shuffled labels. The kept criteria's score means
  something only if it clearly beats both.
- **selection frequency** over bootstrap resamples: how often each criterion
  survives the L1 penalty. A criterion picked in 90% of resamples is a finding;
  one picked once is noise the final fit happened to keep.

Every fit is stored in `criteria.model_fits` with its data, settings and results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import numpy as np

from . import co_labels, corpus

CRIT = "criteria"

DDL = f"""
CREATE TABLE IF NOT EXISTS {CRIT}.model_fits (
    id           serial PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    target       text NOT NULL,
    projects     int NOT NULL,
    positives    int,
    features     int NOT NULL,
    params       jsonb NOT NULL,
    cv_score     real,
    cv_score_sd  real,
    coefficients jsonb NOT NULL,
    selection    jsonb NOT NULL,
    projects_used jsonb NOT NULL
);
"""


async def features(conn, folders: list[str], *, threshold: float = 0.5,
                   modalities: tuple[str, ...] = ("text", "image"),
                   status: str | None = "kept", ids: list[int] | None = None) -> tuple[np.ndarray, list[dict]]:
    """`(X, columns)`: one row per project folder, one column per criterion of `status`.

    `status="dropped"` builds the same features from the criteria the rule threw
    out — the noise check: if those fit the label as well as the kept ones, the
    kept ones' fit is not evidence of anything.
    """
    crits = [dict(r) for r in await conn.fetch(
        f"""SELECT id, name, wording, modality FROM {CRIT}.criteria
             WHERE ($2::text IS NULL OR status = $2) AND modality = ANY($1) AND ($3::int[] IS NULL OR id = ANY($3))
             ORDER BY id""",
        list(modalities), status, ids)]
    text = await conn.fetch(
        f"""SELECT d.project_folder, x.criterion_id, avg((x.p >= $2)::int)::float8 AS share
              FROM {CRIT}.decisions x
              JOIN corpus.chunks c ON c.id = x.chunk_id AND c.usable
              JOIN corpus.documents d ON d.id = c.document_id
             WHERE d.project_folder = ANY($1)
             GROUP BY 1, 2""",
        folders, threshold,
    )
    image = await conn.fetch(
        f"""SELECT d.project_folder, x.criterion_id, avg((x.p >= $2)::int)::float8 AS share
              FROM {CRIT}.image_decisions x
              JOIN corpus.images i ON i.id = x.image_id
              JOIN corpus.documents d ON d.id = i.document_id
             WHERE d.project_folder = ANY($1)
             GROUP BY 1, 2""",
        folders, threshold,
    )
    row = {f: i for i, f in enumerate(folders)}
    col = {c["id"]: j for j, c in enumerate(crits)}
    X = np.zeros((len(folders), len(crits)))
    seen = np.zeros_like(X, dtype=bool)
    for r in list(text) + list(image):
        j = col.get(r["criterion_id"])
        if j is not None:
            X[row[r["project_folder"]], j] = r["share"]
            seen[row[r["project_folder"]], j] = True
    # A project with no pictures has no image features: 0 = "nothing seen", flagged in the column info.
    for j, c in enumerate(crits):
        c["coverage"] = float(seen[:, j].mean())
    return X, crits


CLASSIFY = ("had_change_orders", "material_change_orders")


def target_vector(rows: list[dict], target: str, material: float = 0.05) -> tuple[list[dict], np.ndarray]:
    """`(rows used, y)` for a target. `material_change_orders`: net COs ≥ `material` of the initial contract."""
    if target == "had_change_orders":
        return rows, np.array([float(r["had_change_orders"]) for r in rows])
    rows = [r for r in rows if r["co_share"] is not None]
    if target == "material_change_orders":
        return rows, np.array([float(r["co_share"] >= material) for r in rows])
    return rows, np.array([r["co_share"] for r in rows])


def _model(target: str, inner_folds: int, seed: int):
    from sklearn.linear_model import LassoCV, LogisticRegressionCV
    from sklearn.model_selection import KFold, StratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if target in CLASSIFY:
        est = LogisticRegressionCV(
            Cs=np.logspace(-2, 2, 25), penalty="l1", solver="liblinear", class_weight="balanced",
            cv=StratifiedKFold(inner_folds, shuffle=True, random_state=seed), scoring="roc_auc",
            max_iter=5000,
        )
    else:
        est = LassoCV(cv=KFold(inner_folds, shuffle=True, random_state=seed), max_iter=50000, random_state=seed)
    return make_pipeline(StandardScaler(), est)


def _coefs(pipe, target: str) -> np.ndarray:
    est = pipe[-1]
    return est.coef_.ravel() if target in CLASSIFY else est.coef_


def fit(X: np.ndarray, y: np.ndarray, *, target: str = "had_change_orders", outer_folds: int = 5,
        repeats: int = 10, inner_folds: int = 4, bootstraps: int = 200, seed: int = 0) -> dict:
    """Outer-CV score, the final fit's coefficients, and bootstrap selection frequencies."""
    from sklearn.base import clone
    from sklearn.metrics import r2_score, roc_auc_score
    from sklearn.model_selection import KFold, StratifiedKFold

    classify = target in CLASSIFY
    if classify:
        minority = int(min(y.sum(), len(y) - y.sum()))
        outer_folds = max(2, min(outer_folds, minority))
        inner_folds = max(2, min(inner_folds, minority - 1 if minority > 2 else 2))
    base = _model(target, inner_folds, seed)

    scores = []
    for rep in range(repeats):
        split = (StratifiedKFold(outer_folds, shuffle=True, random_state=seed + rep) if classify
                 else KFold(outer_folds, shuffle=True, random_state=seed + rep))
        pred = np.zeros(len(y))
        ok = True
        for tr, te in split.split(X, y):
            if classify and len(set(y[tr])) < 2:
                ok = False
                break
            m = clone(base).fit(X[tr], y[tr])
            pred[te] = m.predict_proba(X[te])[:, 1] if classify else m.predict(X[te])
        if ok:
            scores.append(roc_auc_score(y, pred) if classify else r2_score(y, pred))

    final = clone(base).fit(X, y)
    coef = _coefs(final, target)

    rng = np.random.default_rng(seed)
    picked = np.zeros(X.shape[1])
    done = 0
    for _ in range(bootstraps):
        idx = rng.integers(0, len(y), len(y))
        if classify and len(set(y[idx])) < 2:
            continue
        try:
            m = clone(base).fit(X[idx], y[idx])
        except ValueError:
            continue
        picked += np.abs(_coefs(m, target)) > 1e-8
        done += 1
    return {
        "cv_score": float(np.mean(scores)) if scores else None,
        "cv_score_sd": float(np.std(scores)) if scores else None,
        "cv_metric": "roc_auc" if classify else "r2",
        "outer_folds": outer_folds, "inner_folds": inner_folds, "repeats": len(scores),
        "coef": coef.tolist(),
        "selection": (picked / done).tolist() if done else [0.0] * X.shape[1],
        "bootstraps": done,
        "penalty": float(final[-1].C_[0]) if classify else float(final[-1].alpha_),
        "_final": final,
    }


def permutation_baseline(X: np.ndarray, y: np.ndarray, *, target: str, n: int, seed: int = 0) -> list[float]:
    """The outer-CV score of the same pipeline on `n` label shuffles: what chance scores."""
    rng = np.random.default_rng(seed + 1000)
    out = []
    for k in range(n):
        res = fit(X, rng.permutation(y), target=target, repeats=2, bootstraps=0, seed=seed + k)
        if res["cv_score"] is not None:
            out.append(res["cv_score"])
    return out


async def run(conn, *, target: str = "had_change_orders", min_billed: float | None = None,
              min_reports: int = 1, modalities: tuple[str, ...] = ("text", "image"), seed: int = 0,
              status: str = "kept", permutations: int = 0, material: float = 0.05, log=print) -> dict:
    await conn.execute(DDL)
    rows = await co_labels.labels(conn, min_billed=min_billed, min_reports=min_reports)
    rows, y = target_vector(rows, target, material)
    folders = [r["project_folder"] for r in rows]
    X, cols = await features(conn, folders, modalities=modalities, status=status)
    keep = X.std(axis=0) > 0
    X, cols = X[:, keep], [c for c, k in zip(cols, keep) if k]
    log(f"[{status} criteria] {len(folders)} projects ({int(y.sum()) if target in CLASSIFY else 'continuous'} positive), "
        f"{X.shape[1]} criteria with any variation")
    res = await asyncio.to_thread(fit, X, y, target=target, seed=seed)
    if permutations:
        perm = await asyncio.to_thread(permutation_baseline, X, y, target=target, n=permutations, seed=seed)
        res["permutation_scores"] = perm
        if perm and res["cv_score"] is not None:
            res["permutation_p"] = (1 + sum(s >= res["cv_score"] for s in perm)) / (1 + len(perm))
    table = sorted(
        ({"criterion_id": c["id"], "name": c["name"], "modality": c["modality"], "wording": c["wording"],
          "coef": co, "selected": sel} for c, co, sel in zip(cols, res["coef"], res["selection"])),
        key=lambda t: (-t["selected"], -abs(t["coef"])),
    )
    fit_id = await conn.fetchval(
        f"""INSERT INTO {CRIT}.model_fits (target, projects, positives, features, params, cv_score, cv_score_sd,
                coefficients, selection, projects_used)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb) RETURNING id""",
        target, len(folders), int(y.sum()) if target in CLASSIFY else None, X.shape[1],
        json.dumps({"min_billed": min_billed, "min_reports": min_reports, "modalities": list(modalities),
                    "seed": seed, "status": status, "material": material, "permutation_scores": res.get("permutation_scores"),
                    "permutation_p": res.get("permutation_p"), **{k: res[k] for k in ("outer_folds", "inner_folds", "repeats",
                                                         "bootstraps", "penalty", "cv_metric")}}),
        res["cv_score"], res["cv_score_sd"],
        json.dumps([t for t in table if abs(t["coef"]) > 1e-8]), json.dumps(table[:40]),
        json.dumps(dict(zip(folders, y.tolist()))),
    )
    log(f"fit {fit_id}: outer-CV {res['cv_metric']} {res['cv_score']:.3f} ± {res['cv_score_sd']:.3f} "
        f"over {res['repeats']} repeats of {res['outer_folds']}-fold; penalty {res['penalty']:.3g}")
    if res.get("permutation_scores"):
        ps = np.array(res["permutation_scores"])
        log(f"label-shuffle baseline: {ps.mean():.3f} ± {ps.std():.3f} over {len(ps)} shuffles; "
            f"p = {res['permutation_p']:.3f}")
    nonzero = [t for t in table if abs(t["coef"]) > 1e-8]
    log(f"{len(nonzero)} criteria with a non-zero coefficient; most often selected:")
    for t in table[:15]:
        log(f"  {t['selected']:4.0%}  {t['coef']:+.3f}  [{t['modality']}] {t['wording'][:90]}")
    res.pop("_final", None)
    return {"fit": fit_id, **res, "table": table}


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        await run(conn, target=args.target, min_billed=args.min_billed, min_reports=args.min_reports,
                  modalities=tuple(args.modality or ("text", "image")), seed=args.seed,
                  status=args.status, permutations=args.permutations, material=args.material)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="python -m utils.lasso", description=__doc__.split("\n")[0])
    p.add_argument("--target", choices=("had_change_orders", "material_change_orders", "co_share"),
                   default="had_change_orders")
    p.add_argument("--material", type=float, default=0.05,
                   help="material_change_orders: net COs at least this share of the initial contract")
    p.add_argument("--min-billed", type=float, help="drop no-CO projects billed less than this share")
    p.add_argument("--min-reports", type=int, default=1)
    p.add_argument("--modality", action="append", choices=("text", "image"))
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--status", choices=("kept", "dropped"), default="kept",
                   help="fit the dropped criteria instead — the noise check")
    p.add_argument("--permutations", type=int, default=0, help="label shuffles for a chance baseline")
    sys.exit(asyncio.run(_main(p.parse_args())))
