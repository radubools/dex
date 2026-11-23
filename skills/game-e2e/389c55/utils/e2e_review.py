"""Screenshot review kit for e2e story runs: diffs, contact sheets, card strips."""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME_RE = re.compile(r"^(desktop|phone)-r(\d+)-(\d{3})-([a-z0-9\-]+?)(-more)?\.png$")


def shots(vp: str, route: str, shots_dir: Path) -> dict:
    """Return {(idx, beat, face): Path} for the given viewport and route."""
    out: dict = {}
    for f in sorted((shots_dir / vp / "shots").glob(f"{vp}-r{route}-*.png")):
        m = NAME_RE.match(f.name)
        if m and m.group(1) == vp:
            face = "more" if m.group(5) else "main"
            out[(int(m.group(3)), m.group(4), face)] = f
    return out


def mad(a: str | Path, b: str | Path) -> float:
    """Mean absolute difference (quarter-scale RGB) between two image files."""
    from PIL import Image
    import numpy as np
    ia = Image.open(a).convert("RGB")
    ib = Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 999.0
    small = (ia.width // 4, ia.height // 4)
    xa = np.asarray(ia.resize(small), dtype=np.int16)
    xb = np.asarray(ib.resize(small), dtype=np.int16)
    return float(np.abs(xa - xb).mean())


def _routes(vp: str, shots_dir: Path) -> list[str]:
    """Every route a run has shots of, in order: "0" (the main route) first."""
    found = {m.group(2) for f in (shots_dir / vp / "shots").glob(f"{vp}-r*.png")
             if (m := NAME_RE.match(f.name))}
    return sorted(found, key=int)


def route_diffs(shots_dir: Path, threshold: float = 2.0) -> tuple[dict, dict]:
    """Compute MAD diffs between alternate routes and r0; return (diffs dict, route_specific)."""
    diffs: dict = {}
    route_specific: dict = defaultdict(list)
    for vp in ("desktop", "phone"):
        base = shots(vp, "0", shots_dir)
        for route in _routes(vp, shots_dir)[1:]:
            alt = shots(vp, route, shots_dir)
            for key, f in alt.items():
                k = f'{vp}/r{route}/{key[1]}{"-more" if key[2]=="more" else ""}'
                if key in base:
                    d = mad(f, base[key])
                    diffs[k] = round(d, 2)
                    if d > threshold:
                        route_specific[(vp, "r" + route)].append((key, f, d))
                else:
                    route_specific[(vp, "r" + route)].append((key, f, None))
    return diffs, route_specific


def build_review(out_dir: Path, shots_dir: Path, routes: tuple[str, ...] = ("0",)) -> dict:
    """Build all review sheets: stage, card strips, route diffs; returns sheet paths."""
    from PIL import Image
    from utils import review_sheets  # noqa: E402
    sheet_dir = out_dir / "review-sheets"
    sheet_dir.mkdir(exist_ok=True)

    # r0 main face: full frames, both sizes
    for vp in ("desktop", "phone"):
        files = sorted([f for k, f in shots(vp, "0", shots_dir).items() if k[2] == "main"],
                       key=lambda x: str(x))
        pages = [files[i:i + 8] for i in range(0, len(files), 8)]
        for n, page in enumerate(pages):
            review_sheets.contact_sheet(page, sheet_dir / f"r0-{vp}-stage-{n}.jpg",
                                         cell=(640, 400), cols=2)

    # Card strips at native scale (r0 only)
    r0dir = out_dir / ".review-r0"
    for vp in ("desktop", "phone"):
        dst = r0dir / "e2e" / vp / "shots"
        dst.mkdir(parents=True, exist_ok=True)
        for f in (shots_dir / vp / "shots").glob(f"{vp}-r0-*.png"):
            link = dst / f.name
            if not link.exists():
                link.symlink_to(f)
    review_sheets.card_strips(r0dir, viewports=("desktop", "phone"),
                               face="main", out_dir=sheet_dir / "cards-main")
    review_sheets.card_strips(r0dir, viewports=("desktop", "phone"),
                               face="more", out_dir=sheet_dir / "cards-more")

    # Route-specific diffs
    diffs, route_specific = route_diffs(shots_dir)
    (out_dir / "review-diffs.json").write_text(json.dumps(diffs, indent=1))

    by_beat = defaultdict(dict)
    for (vp, route), items in route_specific.items():
        for key, f, d in items:
            by_beat[(route, key)].setdefault(vp, f)
    pairs = []
    for (route, key), f in sorted(by_beat.items()):
        label = f"r{route} {key[1]}{'-more' if key[2]=='more' else ''} #{key[0]}"
        for vp in ("desktop", "phone"):
            if vp in by_beat[(route, key)]:
                im = Image.open(by_beat[(route, key)][vp]).convert("RGB")
                im = im.resize((700, round(im.height * 700 / im.width)))
                pairs.append((f"{label} [{vp}]", im))
    page_list = [pairs[i:i + 8] for i in range(0, len(pairs), 8)]
    for n, page in enumerate(page_list):
        review_sheets.contact_sheet_with_strips(page, sheet_dir / f"routes-diff-{n}.jpg",
                                                 w=700, cols=2)
    return {"diffs": diffs, "route_specific": dict(route_specific), "sheet_dir": sheet_dir}


# --- Review sheets with a fixed image budget -------------------------------
#
# A vision model takes a bounded number of images in one request, and a task's
# request carries its whole session: every image opened in this attempt and in
# every earlier attempt of the task (a follow-up resumes the same session).
# The local Qwen on Splash refuses a request with more than 64; past that the
# task can never run again. So the review never opens single screenshots: it
# reads these sheets, which put every beat of the main route at both sizes,
# and whatever another route shows differently, on a handful of images.

SHEET_MAX = 2048          # longest side of a sheet, px (Splash's largest image)
CELL_H = 300              # a shot's height in a cell, px
LABEL_H = 22


def _failures(out_dir: Path) -> dict:
    """{(size, route, beat id): 'check: detail'} for every check that failed in the run.

    A run's routes are numbered in report order: route 0 is the main one, as
    in the shots' names (`<size>-r<route>-<index>-<beat>.png`).
    """
    bad: dict = {}
    for vp in ("desktop", "phone"):
        rep = out_dir / vp / "report.json"
        if not rep.exists():
            continue
        for n, run in enumerate(json.loads(rep.read_text()).get("runs", [])):
            for b in run.get("beats", []):
                for c in b.get("checks", []):
                    if not c.get("ok", True):
                        bad.setdefault((vp, str(n), b["id"]), f'{c["name"]}: {c.get("detail", "")}'[:90])
    return bad


def _cell(items: list, label: str, failed: bool):
    """One labelled cell: the given shots side by side at CELL_H px high."""
    from PIL import Image, ImageDraw
    ims = []
    for f in items:
        im = Image.open(f).convert("RGB")
        ims.append(im.resize((max(1, round(im.width * CELL_H / im.height)), CELL_H)))
    w = sum(i.width for i in ims) + 4 * (len(ims) - 1)
    cell = Image.new("RGB", (max(w, 200), CELL_H + LABEL_H), (200, 40, 40) if failed else (30, 30, 30))
    x = 0
    for im in ims:
        cell.paste(im, (x, LABEL_H))
        x += im.width + 4
    ImageDraw.Draw(cell).text((4, 4), label[:110], fill=(255, 255, 255))
    return cell


def _pages(cells: list) -> list:
    """Pack cells, in order, into as few sheets of at most SHEET_MAX px as they fit."""
    from PIL import Image
    pages, row, rows, y, x = [], [], [], 0, 0
    def flush_page():
        nonlocal rows, y
        if rows:
            h = sum(max(c.height for c, _ in r) for r in rows) + 6 * (len(rows) - 1)
            w = max(sum(c.width for c, _ in r) + 6 * (len(r) - 1) for r in rows)
            page = Image.new("RGB", (w, h), (12, 12, 12))
            yy = 0
            labels = []
            for r in rows:
                xx = 0
                for c, lab in r:
                    page.paste(c, (xx, yy))
                    xx += c.width + 6
                    labels.append(lab)
                yy += max(c.height for c, _ in r) + 6
            pages.append((page, labels))
        rows, y = [], 0
    for c, lab in cells:
        if row and x + c.width > SHEET_MAX:
            rh = max(cc.height for cc, _ in row)
            if y + rh > SHEET_MAX:
                flush_page()
            rows.append(row)
            y += rh + 6
            row, x = [], 0
        row.append((c, lab))
        x += c.width + 6
    if row:
        rh = max(cc.height for cc, _ in row)
        if y + rh > SHEET_MAX:
            flush_page()
        rows.append(row)
    flush_page()
    return pages


def sheets(out_dir: Path, *, only=None) -> dict:
    """Build the review sheets for a run in `out_dir` (e2e_run's `out`).

    The main route: one cell per beat holding its desktop shot, its phone shot
    and the card's "more" face at both sizes. The other routes add only what
    the main route never shows: one cell per beat it does not reach (the
    routes that reach it named on the cell), and the shot of any beat that
    failed a check on that route. A cell whose beat failed is red, with the
    check. `only` (beat ids) limits the sheets to those beats, for a run again.

    Writes `out_dir/review-sheets/sheet-<n>.jpg` and `sheets.json` (each
    sheet's cells) and returns {"sheets": [paths], "cells": {path: [labels]}}.
    """
    out_dir = Path(out_dir)
    dst = out_dir / "review-sheets"
    if dst.exists():
        for f in dst.glob("sheet-*.jpg"):
            f.unlink()
    dst.mkdir(exist_ok=True)
    keep = set(only) if only else None
    bad = _failures(out_dir)
    sizes = ("desktop", "phone")
    routes = sorted({r for vp in sizes for r in _routes(vp, out_dir)}, key=int)
    by_route = {(vp, r): shots(vp, r, out_dir) for vp in sizes for r in routes}

    def faces(r: str, idx: int, beat: str) -> list:
        return [by_route[(vp, r)][(idx, beat, face)] for face in ("main", "more") for vp in sizes
                if (idx, beat, face) in by_route.get((vp, r), {})]

    def failed(r: str, beat: str) -> list:
        return [f"{vp} {bad[(vp, r, beat)]}" for vp in sizes if (vp, r, beat) in bad]

    order = "[desktop | phone | more: desktop | phone]"
    cells = []
    main = sorted({(k[0], k[1]) for vp in sizes for k in by_route.get((vp, "0"), {})})
    main_ids = {beat for _, beat in main}
    for idx, beat in main:
        if keep and beat not in keep:
            continue
        fails = failed("0", beat)
        label = f"r0 #{idx} {beat}  {order}" + (f"  FAILED {'; '.join(fails)}" if fails else "")
        cells.append((_cell(faces("0", idx, beat), label, bool(fails)), f"r0 #{idx} {beat}"))
    elsewhere: dict = {}                        # beat id -> (first route, index, [routes])
    for r in routes[1:]:
        for idx, beat in sorted({(k[0], k[1]) for vp in sizes for k in by_route.get((vp, r), {})}):
            if keep and beat not in keep:
                continue
            if beat not in main_ids:
                elsewhere.setdefault(beat, (r, idx, []))[2].append(f"r{r}")
            elif fails := failed(r, beat):
                label = f"r{r} #{idx} {beat}  {order}  FAILED {'; '.join(fails)}"
                cells.append((_cell(faces(r, idx, beat), label, True), f"r{r} #{idx} {beat}"))
    for beat, (r, idx, rs) in sorted(elsewhere.items(), key=lambda kv: (int(kv[1][0]), kv[1][1])):
        fails = [f for x in rs for f in failed(x[1:], beat)]
        label = f"{','.join(rs)} #{idx} {beat}  {order}" + (f"  FAILED {'; '.join(fails)}" if fails else "")
        cells.append((_cell(faces(r, idx, beat), label, bool(fails)), f"{','.join(rs)} #{idx} {beat}"))
    paths, index = [], {}
    for n, (page, labels) in enumerate(_pages(cells)):
        p = dst / f"sheet-{n}.jpg"
        page.save(p, quality=85)
        paths.append(p)
        index[p.name] = labels
    (dst / "sheets.json").write_text(json.dumps(index, indent=1))
    return {"sheets": paths, "cells": {str(p): index[p.name] for p in paths}}
