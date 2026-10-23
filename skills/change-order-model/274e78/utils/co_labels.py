"""Change-order labels per project, from the contractor's draw summary in the monthly reports.

Every monthly progress report (`Mo_Updates/PR#N ... .xls[x]`) carries a
`Contractor` sheet with the AIA G702 summary of the general contractor's draw:

    1  Initial Contract     21,223,000
    2  CO's Todate             444,199
    3  Current Contract     21,667,199
       Through CO's                 11
    4  Amount Requested To Date  15,221,932

Read across a project's reports in PR order it is the evolution of the contract
budget; the latest report gives the label:

- `had_change_orders` — net change-order total ≠ 0 or a change-order count > 0;
- `co_amount`, `co_count`, `co_share` (= amount / initial contract);
- `billed_share` — amount requested to date / current contract, i.e. how far the
  project had got. A project with no change orders after two reports and 10%
  billed is *not yet* a negative; filter on `billed_share` or `reports` before
  fitting (`labels(conn, min_billed=...)`).

    cd /Users/raduparadovschi/dex/assets/construction
    ../../.venv/bin/python -m utils.co_labels             # every ingested project
    ../../.venv/bin/python -m utils.co_labels --summary   # counts only

Stored in `corpus.project_labels`, one row per project folder, with the whole
per-report series in `series` and anything odd in `flags`. Workbooks are read
with `engine="calamine"` (see AGENTS.md: xlrd/openpyxl fail on ~8% of them).
A project whose monthly reports are PDF-only has no row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import warnings
from pathlib import Path

from . import corpus, edil_sources

SCHEMA = corpus.SCHEMA
WORKBOOK = (".xls", ".xlsx", ".xlsm", ".xlsb")
PR = re.compile(r"PR\s*#?\s*(\d+)", re.I)
FIELDS = {
    "initial": r"initial\s+contract",
    "cos_to_date": r"co'?s\s*to\s*-?\s*date|change\s*orders?\s*to\s*date",
    "current": r"current\s+contract",
    "co_count": r"through\s+co",
    "requested": r"amount\s+requested\s+to\s+date",
}

DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.project_labels (
    project_folder    text PRIMARY KEY,
    building_type     text NOT NULL,
    reports           int NOT NULL,        -- monthly workbooks with a readable G702
    last_pr           int,
    last_file         text,
    initial_contract  double precision,
    current_contract  double precision,
    co_amount         double precision,
    co_count          int,
    co_share          double precision,
    billed_share      double precision,
    had_change_orders boolean,
    series            jsonb NOT NULL DEFAULT '[]',
    flags             jsonb NOT NULL DEFAULT '[]',
    extracted_at      timestamptz NOT NULL DEFAULT now()
);
"""


def _num(v):
    import pandas as pd

    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    s = re.sub(r"[,$\s]", "", str(v))
    if not re.fullmatch(r"-?\(?\d+(\.\d+)?\)?", s):
        return None
    n = float(s.strip("()"))
    return -n if s.startswith("(") else n


def read_g702(path: Path) -> dict:
    """The G702 fields of one monthly workbook's `Contractor` sheet; `error` if unreadable."""
    import pandas as pd

    warnings.filterwarnings("ignore")
    try:
        book = pd.ExcelFile(path, engine="calamine")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"open: {type(exc).__name__}: {exc}"[:300]}
    sheet = next((s for s in book.sheet_names if s.strip().lower().startswith("contractor")), None)
    if not sheet:
        return {"error": "no Contractor sheet"}
    df = pd.read_excel(book, sheet, header=None)
    out: dict = {}
    # The first G702 table is the GC contract; a second, usually blank, follows it.
    for _, row in df.iterrows():
        cells = row.tolist()
        for i, c in enumerate(cells):
            if not isinstance(c, str):
                continue
            for key, pat in FIELDS.items():
                if key in out or not re.search(pat, c, re.I):
                    continue
                for v in cells[i + 1:]:
                    n = _num(v)
                    if n is not None:
                        out[key] = n
                        break
    return out


def monthly_workbooks(project: edil_sources.Project) -> list[Path]:
    return [f for f in project.path.rglob("*") if f.is_file() and f.suffix.lower() in WORKBOOK
            and f.relative_to(project.path).parts[0].lower().lstrip("_ -").startswith("mo")]


def label(project: edil_sources.Project) -> dict | None:
    """The label row for one project, or None if it has no readable monthly workbook."""
    series = []
    for f in monthly_workbooks(project):
        r = read_g702(f)
        m = PR.search(f.name)
        r.update(pr=int(m.group(1)) if m else None, file=f.name)
        series.append(r)
    series.sort(key=lambda r: (r["pr"] is None, r["pr"] or 0, r["file"]))
    good = [r for r in series if "initial" in r or "cos_to_date" in r]
    if not good:
        return None
    last = good[-1]
    initial, current = last.get("initial"), last.get("current")
    amount = last.get("cos_to_date")
    if amount is None and initial is not None and current is not None:
        amount = current - initial
    count = last.get("co_count")
    flags = []
    if initial and amount is not None and abs(amount) > initial:
        flags.append("co_amount exceeds the initial contract: probably a misread cell")
    if initial and current and amount is not None and abs((current - initial) - amount) > 1:
        flags.append("current - initial != CO's to date")
    if last["pr"] is None:
        flags.append("report numbers not in file names: order is by name")
    billed = last.get("requested") / current if last.get("requested") and current else None
    had = (amount not in (None, 0.0)) or bool(count and count > 0)
    return {
        "project_folder": project.folder, "building_type": project.building_type,
        "reports": len(good), "last_pr": last["pr"], "last_file": last["file"],
        "initial_contract": initial, "current_contract": current, "co_amount": amount,
        "co_count": int(count) if count is not None else None,
        "co_share": amount / initial if amount is not None and initial else None,
        "billed_share": billed, "had_change_orders": had,
        "series": [{k: r.get(k) for k in ("pr", "file", "initial", "cos_to_date", "current", "co_count",
                                          "requested", "error")} for r in series],
        "flags": flags,
    }


async def ensure_schema(conn) -> None:
    await conn.execute(DDL)


async def run(conn, *, log=print) -> dict:
    """Label every project that has an ingested document. Returns counts."""
    await ensure_schema(conn)
    folders = {r["project_folder"] for r in await conn.fetch(
        f"SELECT DISTINCT project_folder FROM {SCHEMA}.documents")}
    projects = [p for p in edil_sources.projects(with_pdf=False) if p.folder in folders]
    n = {"projects": len(projects), "labelled": 0, "no_workbooks": 0, "unreadable": 0}
    for p in projects:
        books = monthly_workbooks(p)
        if not books:
            n["no_workbooks"] += 1
            continue
        row = await asyncio.to_thread(label, p)
        if row is None:
            n["unreadable"] += 1
            await corpus.record_error(conn, "labels", "no readable G702 in any monthly workbook",
                                      path=str(p.path), detail={"workbooks": len(books)})
            continue
        await conn.execute(
            f"""INSERT INTO {SCHEMA}.project_labels (project_folder, building_type, reports, last_pr, last_file,
                    initial_contract, current_contract, co_amount, co_count, co_share, billed_share,
                    had_change_orders, series, flags)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb)
                ON CONFLICT (project_folder) DO UPDATE SET building_type = EXCLUDED.building_type,
                    reports = EXCLUDED.reports, last_pr = EXCLUDED.last_pr, last_file = EXCLUDED.last_file,
                    initial_contract = EXCLUDED.initial_contract, current_contract = EXCLUDED.current_contract,
                    co_amount = EXCLUDED.co_amount, co_count = EXCLUDED.co_count, co_share = EXCLUDED.co_share,
                    billed_share = EXCLUDED.billed_share, had_change_orders = EXCLUDED.had_change_orders,
                    series = EXCLUDED.series, flags = EXCLUDED.flags, extracted_at = now()""",
            row["project_folder"], row["building_type"], row["reports"], row["last_pr"], row["last_file"],
            row["initial_contract"], row["current_contract"], row["co_amount"], row["co_count"],
            row["co_share"], row["billed_share"], row["had_change_orders"],
            json.dumps(row["series"], default=str), json.dumps(row["flags"]),
        )
        n["labelled"] += 1
    return n


async def labels(conn, *, min_billed: float | None = None, min_reports: int = 1,
                 drop_flagged: bool = True) -> list[dict]:
    """Stored labels, optionally only projects far enough along to count a negative.

    `min_billed` keeps a project with no change orders only if at least that share
    of its contract had been billed; projects that did have change orders are kept
    whatever their progress (they are positives however early).
    """
    rows = [dict(r) for r in await conn.fetch(f"SELECT * FROM {SCHEMA}.project_labels ORDER BY project_folder")]
    out = []
    for r in rows:
        r["flags"] = json.loads(r["flags"]) if isinstance(r["flags"], str) else r["flags"]
        if drop_flagged and any("misread" in f for f in r["flags"]):
            continue
        if r["reports"] < min_reports:
            continue
        if (min_billed is not None and not r["had_change_orders"]
                and (r["billed_share"] or 0) < min_billed):
            continue
        out.append(r)
    return out


async def _main(args) -> int:
    conn = await corpus.connect()
    try:
        if not args.summary:
            print(json.dumps(await run(conn), indent=2))
        rows = await labels(conn, drop_flagged=False)
        pos = [r for r in rows if r["had_change_orders"]]
        print(f"{len(rows)} labelled projects, {len(pos)} with change orders, {len(rows) - len(pos)} without")
        for r in rows:
            billed = f"{r['billed_share']:5.0%}" if r["billed_share"] is not None else "    —"
            print(f"  {r['project_folder'][:44]:44s} PR{r['last_pr']} billed {billed}  COs {r['co_count']!s:>4}  "
                  f"${(r['co_amount'] or 0):>13,.0f}  {'; '.join(r['flags']) if r['flags'] else ''}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="python -m utils.co_labels", description=__doc__.split("\n")[0])
    p.add_argument("--summary", action="store_true", help="print the stored labels without re-reading")
    sys.exit(asyncio.run(_main(p.parse_args())))
