"""Screenshot review kit for e2e story runs: diffs, contact sheets, card strips."""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME_RE = re.compile(r"^(desktop|phone)-r(\d)-(\d{3})-([a-z0-9\-]+?)(-more)?\.png$")


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


def route_diffs(shots_dir: Path, threshold: float = 2.0) -> tuple[dict, dict]:
    """Compute MAD diffs between alternate routes and r0; return (diffs dict, route_specific)."""
    diffs: dict = {}
    route_specific: dict = defaultdict(list)
    for vp in ("desktop", "phone"):
        base = shots(vp, "0", shots_dir)
        for route in ("1", "2", "3", "4"):
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
