"""Does the report text add anything beyond who and where? Metadata baselines against the criteria.

The first fits found the only signal in criteria that name a lender or a place —
i.e. in *who financed it and where it is*, not in the risks the report describes.
This module makes that comparison explicit. Five models, one outer-CV protocol:

    M1     pre-construction metadata: state, building category, log initial contract, lender
    M2     M1 + exposure: monthly reports filed, share of the contract billed
           (known only after the fact — it controls for how long a project was watched)
    C      the kept criteria (text and image), as in `lasso`
    M1+C   both
    M2+C   both

and an **increment test**: refit M1+C (or M2+C) with the criteria columns
shuffled across projects while the metadata stays put. If the real M1+C does not
beat that null, the criteria add nothing that the metadata does not already say.

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.baseline
    ../../.venv/bin/python -m utils.baseline --target material_change_orders --shuffles 50

Metadata sources: state from the project folder's suffix (`_SanDiego-CA`),
building category from the building-type folder (`11-16-21.21_Residential_...`),
initial contract and exposure from `corpus.project_labels`, and the lender from
the `Client:` field of the latest monthly workbook's `Main` sheet, with spelling
variants merged (`US Bank`, `U.S. Bank`, `USB`, `US Bancorp` are one lender).
Categories with fewer than `min_count` projects pool into `other`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

import numpy as np

from . import co_labels, corpus, edil_sources, lasso

CRIT = "criteria"

LENDERS = [
    (r"\bu\.?\s*s\.?\s*(bank|bancorp)\b|^usb$", "US Bank"),
    (r"east\s*west", "East West Bank"),
    (r"bank\s+of\s+america|merrill", "Bank of America"),
    (r"bank\s+of\s+the\s+west", "Bank of the West"),
    (r"chase", "Chase"),
    (r"comerica", "Comerica"),
    (r"key\s*bank", "Key Bank"),
    (r"city\s+national", "City National"),
    (r"wells\s+fargo", "Wells Fargo"),
]


def normalise_lender(raw: str | None) -> str:
    if not raw or raw.lower().startswith(("site visit", "err")):
        return "unknown"
    for pat, name in LENDERS:
        if re.search(pat, raw, re.I):
            return name
    return raw.strip()


def client_field(path) -> str | None:
    """The `Client:` value on a monthly workbook's `Main` sheet."""
    import warnings

    import pandas as pd

    warnings.filterwarnings("ignore")
    try:
        book = pd.ExcelFile(path, engine="calamine")
        sheet = next((s for s in book.sheet_names if s.strip().lower() == "main"), None)
        if not sheet:
            return None
        df = pd.read_excel(book, sheet, header=None)
    except Exception:  # noqa: BLE001
        return None
    for _, row in df.iterrows():
        cells = row.tolist()
        for i, cell in enumerate(cells):
            if isinstance(cell, str) and re.match(r"\s*client\s*:?\s*$", cell, re.I):
                return next((str(v).strip() for v in cells[i + 1:] if isinstance(v, str) and v.strip()), None)
    return None


def state_of(folder: str) -> str:
    m = re.search(r"-([A-Z]{2})$", folder)
    return m.group(1) if m else "unknown"


def category_of(building_type: str) -> str:
    parts = building_type.split("_")
    return re.sub(r"[^A-Za-z+]", "", parts[1]).lower() if len(parts) > 1 else "unknown"


async def metadata(conn, rows: list[dict], *, min_count: int = 3) -> tuple[np.ndarray, list[str], np.ndarray, list[str]]:
    """`(M1, M1 names, exposure, exposure names)` for the labelled rows, in order."""
    byf = {p.folder: p for p in edil_sources.projects(with_pdf=False)}
    lenders = []
    for r in rows:
        p = byf.get(r["project_folder"])
        f = next((x for x in co_labels.monthly_workbooks(p) if x.name == r["last_file"]), None) if p else None
        lenders.append(normalise_lender(await asyncio.to_thread(client_field, f) if f else None))
    cats = {"state": [state_of(r["project_folder"]) for r in rows],
            "category": [category_of(r["building_type"]) for r in rows],
            "lender": lenders}
    cols, names = [], []
    for field, values in cats.items():
        counts = {v: values.count(v) for v in set(values)}
        levels = sorted(v for v, n in counts.items() if n >= min_count)
        for lv in levels[1:]:  # first level is the reference
            cols.append([float(v == lv) for v in values])
            names.append(f"{field}={lv}")
        if any(counts[v] < min_count for v in values):
            cols.append([float(counts[v] < min_count) for v in values])
            names.append(f"{field}=other")
    cols.append([np.log10(r["initial_contract"]) if r["initial_contract"] and r["initial_contract"] > 0 else 0.0
                 for r in rows])
    names.append("log10 initial contract")
    exposure = np.array([[float(r["reports"]), float(r["billed_share"] or 0.0)] for r in rows])
    return np.array(cols).T, names, exposure, ["monthly reports", "billed share"]


def _score(X, y, target, repeats, seed):
    res = lasso.fit(X, y, target=target, repeats=repeats, bootstraps=0, seed=seed)
    return res["cv_score"], res["cv_score_sd"]


async def run(conn, *, target: str = "had_change_orders", material: float = 0.05, repeats: int = 10,
              shuffles: int = 30, seed: int = 0, log=print) -> dict:
    await conn.execute(lasso.DDL)
    rows, y = lasso.target_vector(await co_labels.labels(conn), target, material)
    folders = [r["project_folder"] for r in rows]
    M1, m1_names, E, e_names = await metadata(conn, rows)
    C, cols = await lasso.features(conn, folders, status="kept")
    C = C[:, C.std(axis=0) > 0]
    M2 = np.hstack([M1, E])
    pos = f"{int(y.sum())} positive" if target in lasso.CLASSIFY else "continuous"
    log(f"{len(folders)} projects ({pos}); M1 {M1.shape[1]} columns ({', '.join(m1_names)}); "
        f"exposure {E.shape[1]}; criteria {C.shape[1]}")

    models = {"M1": M1, "M2": M2, "C": C, "M1+C": np.hstack([M1, C]), "M2+C": np.hstack([M2, C])}
    scores = {}
    for name, X in models.items():
        scores[name] = await asyncio.to_thread(_score, X, y, target, repeats, seed)
        log(f"  {name:5s} {scores[name][0]:.3f} ± {scores[name][1]:.3f}")

    rng = np.random.default_rng(seed + 11)
    increments = {}
    for base_name, base in (("M1", M1), ("M2", M2)):
        null = []
        for k in range(shuffles):
            Cs = C[rng.permutation(len(y))]
            s, _ = await asyncio.to_thread(_score, np.hstack([base, Cs]), y, target, 3, seed + k + 1)
            null.append(s)
        real = scores[f"{base_name}+C"][0]
        p = (1 + sum(n >= real for n in null)) / (1 + len(null))
        increments[base_name] = {"real": real, "null_mean": float(np.mean(null)), "null_sd": float(np.std(null)), "p": p}
        log(f"  {base_name}+C vs {base_name}+shuffled C: {real:.3f} against {np.mean(null):.3f} ± {np.std(null):.3f}; "
            f"p = {p:.3f}")

    # Which metadata matters: the M2 fit's own coefficients and bootstrap selection.
    full = await asyncio.to_thread(lasso.fit, M2, y, target=target, repeats=2, bootstraps=200, seed=seed)
    meta_table = sorted(zip(m1_names + e_names, full["coef"], full["selection"]), key=lambda t: -t[2])
    log("  metadata most often selected (M2):")
    for name, co, sel in meta_table[:10]:
        log(f"    {sel:4.0%}  {co:+.3f}  {name}")

    fit_id = await conn.fetchval(
        f"""INSERT INTO {CRIT}.model_fits (target, projects, positives, features, params, cv_score, cv_score_sd,
                coefficients, selection, projects_used)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb) RETURNING id""",
        target, len(folders), int(y.sum()) if target in lasso.CLASSIFY else None, M2.shape[1] + C.shape[1],
        json.dumps({"kind": "baseline", "scores": scores, "increments": increments, "shuffles": shuffles,
                    "m1": m1_names, "exposure": e_names, "material": material}),
        scores["M2+C"][0], scores["M2+C"][1],
        json.dumps([{"name": n, "coef": c} for n, c, _ in meta_table]),
        json.dumps([{"name": n, "selected": s} for n, _, s in meta_table]),
        json.dumps(dict(zip(folders, y.tolist()))),
    )
    log(f"stored as fit {fit_id}")
    return {"fit": fit_id, "scores": scores, "increments": increments}


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        await run(conn, target=args.target, material=args.material, repeats=args.repeats, shuffles=args.shuffles)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    p = argparse.ArgumentParser(prog="python -m utils.baseline", description=__doc__.split("\n")[0])
    p.add_argument("--target", choices=("had_change_orders", "material_change_orders", "co_share"),
                   default="had_change_orders")
    p.add_argument("--material", type=float, default=0.05)
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--shuffles", type=int, default=30)
    sys.exit(asyncio.run(_main(p.parse_args())))
