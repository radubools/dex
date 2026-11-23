"""game-e2e's review sheets: every beat on a few sheets, other routes only for what the main one misses."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from utils import e2e_review


def shot(out: Path, vp: str, route: int, idx: int, beat: str, more: bool = False) -> None:
    d = out / vp / "shots"
    d.mkdir(parents=True, exist_ok=True)
    size = (1280, 800) if vp == "desktop" else (390, 844)
    Image.new("RGB", size, (route * 20, idx * 5, 90)).save(d / f"{vp}-r{route}-{idx:03d}-{beat}{'-more' if more else ''}.png")


def run(tmp_path: Path, beats_main=30, routes=12) -> Path:
    out = tmp_path / "e2e"
    for vp in ("desktop", "phone"):
        for i in range(beats_main):
            shot(out, vp, 0, i, f"b{i}")
            shot(out, vp, 0, i, f"b{i}", more=True)
        for r in range(1, routes):          # routes 1..11: two-digit routes too
            for i in range(beats_main):
                beat = f"b{i}" if i != 5 else f"alt{r % 2}"
                shot(out, vp, r, i, beat)
        runs = [{"route": "main", "beats": [{"id": "b3", "checks": [{"name": "highlights in view", "ok": vp != "phone", "detail": "x"}]}]}]
        runs += [{"route": f"r{r}", "beats": [{"id": "b9", "checks": [{"name": "c", "ok": r != 11}]}]} for r in range(1, routes)]
        (out / vp / "report.json").write_text(json.dumps({"passed": False, "runs": runs}))
    return out


def test_sheets_cover_every_beat_on_few_images(tmp_path):
    out = run(tmp_path)
    r = e2e_review.sheets(out)
    labels = [lab for labs in r["cells"].values() for lab in labs]
    assert len(r["sheets"]) <= 8
    for p in r["sheets"]:
        assert max(Image.open(p).size) <= e2e_review.SHEET_MAX
    # every main beat, once
    assert sum(lab.startswith("r0 ") for lab in labels) == 30
    # the beats only other routes reach, one cell each with the routes that reach them
    assert any(lab.startswith("r1,r3,r5,r7,r9,r11 #5 alt1") for lab in labels)
    assert any(lab.startswith("r2,r4,r6,r8,r10 #5 alt0") for lab in labels)
    # a beat that failed on route 11 only gets that route's shot
    assert "r11 #9 b9" in labels
    assert len(labels) == 30 + 2 + 1
    assert json.loads((out / "review-sheets" / "sheets.json").read_text())


def test_only_limits_to_the_beats_named(tmp_path):
    out = run(tmp_path, routes=2)
    r = e2e_review.sheets(out, only=["b3", "b4"])
    labels = [lab for labs in r["cells"].values() for lab in labs]
    assert labels == ["r0 #3 b3", "r0 #4 b4"]
    assert len(r["sheets"]) == 1


def test_route_numbers_past_nine_are_read(tmp_path):
    out = run(tmp_path, beats_main=2, routes=13)
    assert e2e_review._routes("desktop", out)[-1] == "12"
    assert e2e_review.shots("desktop", "12", out)
