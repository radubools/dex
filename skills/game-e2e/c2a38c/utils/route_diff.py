"""Which alt-route e2e screenshots actually differ from the main route's: a
pixel MAD per beat, so a full review knows what to open.

A story with choices plays most of its beats on every route; comparing each
alt-route shot against its main-route twin (mean absolute difference, averaged
down) flags the beats that changed — the choice's feedback, the remedial beats
that only that route plays — and shows that the rest are the same frames the
main route already got. Returns a list of problem dicts; the caller decides
what to look at.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image

_NAME = re.compile(r"^(desktop|phone)-r(\d+)-(\d{3})-([a-z0-9\-]+?)(-more)?\.png$")


def shots(out_dir, viewport: str, route: int) -> dict:
    """The e2e shots of one viewport and route: {(index, beat id, face): Path}.

    `out_dir` is a run's e2e directory (with `<viewport>/shots/`); `route` is
    the numeric route tag from the shot names (0 is the main route).
    """
    out = {}
    for f in sorted((Path(out_dir) / viewport / "shots").glob(f"{viewport}-r{route}-*.png")):
        m = _NAME.match(f.name)
        if m and m.group(1) == viewport:
            out[(int(m.group(3)), m.group(4), "more" if m.group(5) else "main")] = f
    return out


def mad(a, b, scale: int = 4) -> float:
    """Mean absolute pixel difference of two shots, each averaged down `scale`×.

    ~0 for identical frames; single digits are render jitter (pulse phase,
    load timing); tens are a real content difference.
    """
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 999.0
    small = (max(ia.width // scale, 1), max(ia.height // scale, 1))
    xa = np.asarray(ia.resize(small), dtype=np.int16)
    xb = np.asarray(ib.resize(small), dtype=np.int16)
    return float(np.abs(xa - xb).mean())


def route_diffs(out_dir, base: int = 0, threshold: float = 1.5, scale: int = 4) -> list[dict]:
    """Every alt-route shot that differs from its `base`-route twin.

    Returns [{viewport, route, index, beat, face, mad}] sorted by viewport,
    route, index; `mad` is None for beats the base route never plays (route-
    specific beats — remedial gotos). Only shots with no twin or with
    `mad` > `threshold` are listed: those are the ones a reviewer opens in
    full, having reviewed the base route's shots once for the rest.
    """
    out_dir = Path(out_dir)
    viewports = sorted(p.name for p in out_dir.iterdir() if (p / "shots").is_dir())
    found = []
    for vp in viewports:
        base_shots = shots(out_dir, vp, base)
        routes = sorted({int(m.group(2)) for m in map(_NAME.match,
                     (f.name for f in (out_dir / vp / "shots").glob(f"{vp}-r*-*.png"))) if m})
        for rt in routes:
            if rt == base:
                continue
            for (index, beat, face), f in shots(out_dir, vp, rt).items():
                twin = base_shots.get((index, beat, face))
                d = None if twin is None else mad(f, twin, scale)
                if d is None or d > threshold:
                    found.append({"viewport": vp, "route": rt, "index": index,
                                  "beat": beat, "face": face, "mad": d})
    return found
