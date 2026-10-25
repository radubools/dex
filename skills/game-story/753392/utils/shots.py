"""Story end-to-end helpers: small copies of the screenshots for the visual review, and camera probes run in the game."""
from __future__ import annotations

import copy
import itertools
import json
import shutil
from pathlib import Path
from typing import Iterable

from . import story

#: Acts a probe may run again for every candidate camera without changing the scene (Model Workshop).
REPEATABLE = ("ui.", "model.", "data.values", "cluster.matrix", "cluster.cut", "cluster.dendrogram", "guide.", "measure.")


def e2e_shot_dirs(package: str | Path) -> list[Path]:
    """The folders holding a package's e2e screenshots: e2e/shots if it has any, else e2e_start's e2e/<viewport>/shots."""
    e2e = Path(package) / story.E2E_DIR
    if any((e2e / "shots").glob("*.png")):
        return [e2e / "shots"]
    return [d for d in (e2e / "desktop" / "shots", e2e / "phone" / "shots") if d.is_dir()]


def review_copies(package: str | Path, patterns: Iterable[str] = ("*.png",), *, desktop: int = 512, phone: int = 700,
                  quality: int = 72, out: str | Path | None = None,
                  shot_dirs: Iterable[str | Path] | None = None) -> list[Path]:
    """JPEG copies of a package's e2e screenshots small enough to open (desktop and phone shots capped at their own long side): the paths."""
    from PIL import Image

    dirs = [Path(d) for d in shot_dirs] if shot_dirs is not None else e2e_shot_dirs(package)
    dest = Path(out) if out else Path(package) / story.E2E_DIR / "small"
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for pat in patterns:
        for png in sorted(p for d in dirs for p in d.glob(pat)):
            img = Image.open(png).convert("RGB")
            side = desktop if png.name.startswith("desktop") else phone
            img.thumbnail((side, side))
            path = dest / (png.stem + ".jpg")
            img.save(path, quality=quality)
            written.append(path)
    return written


def candidates(grid: dict) -> list[dict]:
    """Every story camera in a grid {"x", "z", "distance", "pitch", "yaw": sequences}, in product order."""
    keys = ("x", "z", "distance", "pitch", "yaw")
    return [{"target": [x, z], "distance": d, "pitch": p, "yaw": y}
            for x, z, d, p, y in itertools.product(*(grid[k] for k in keys))]


def _plain(text: str) -> str:
    # Reads and set braces outside $$…$$ become parentheses: a probe has none of the lesson's variables.
    parts = str(text).split("$$")
    return "$$".join(p if i % 2 else p.replace("{", "(").replace("}", ")") for i, p in enumerate(parts))


def probe_story(story_dict: dict, beat_ids: Iterable[str], grid: dict, *, repeatable: Iterable[str] = REPEATABLE,
                duration: float = 0.6) -> dict:
    """A throwaway story replaying `story_dict`'s main line (the beats before its first `end`) that repeats each named beat under every camera in `grid` (ids `<beat>--<n>`)."""
    wanted, rep = set(beat_ids), tuple(repeatable)
    cams = candidates(grid)
    out, pending = [], []
    for b in story_dict["beats"]:
        if b.get("end"):
            break
        acts = [{k: v for k, v in a.items() if k != "as"} for a in story.act_entries(b.get("act"))]
        stage = {"spawn": b.get("spawn", []), "remove": b.get("remove", [])}
        if b["id"] not in wanted:
            out.append({"id": f"{b['id']}--replay", "say": "Probe: replaying the lesson.", **stage,
                        **({"act": acts} if acts else {}), "wait": "next"})
            continue
        once = [a for a in acts if not a["action"].startswith(rep)]
        again = [a for a in acts if a["action"].startswith(rep)]
        out.append({"id": f"{b['id']}--setup", "say": "Probe: setting up.", **stage, **({"act": once} if once else {}), "wait": "next"})
        for i, cam in enumerate(cams):
            c = {k: copy.deepcopy(b[k]) for k in ("say", "highlight", "wait") if k in b}
            c["say"] = _plain(c["say"])
            w = c.get("wait")
            if isinstance(w, dict) and "input" in w:
                w["input"].update(prompt="Probe: type the answer.", right="Right.", wrong="Again.")
                w["input"].pop("as", None)
            elif isinstance(w, dict) and "choice" in w:
                w["choice"]["prompt"] = _plain(w["choice"]["prompt"])
                w["choice"].pop("as", None)
                for o in w["choice"]["options"]:
                    for k in ("goto", "set"):
                        o.pop(k, None)
                    o["label"], o["feedback"] = _plain(o["label"]), _plain(o.get("feedback", ""))
            elif w != "next":
                c["wait"] = "next"
            if again:
                c["act"] = again
            c.update(id=f"{b['id']}--{i}", camera={**cam, "duration": duration})
            out.append(c)
    out.append({"id": "end", "say": "Probe done.", "wait": "next", "end": True})
    return {"schema": 2, "id": "probe", "title": "Camera probe", "summary": "Scratch: camera candidates.",
            "algorithm": story_dict["algorithm"], "level": story_dict.get("level", "easy"),
            "assets": story_dict.get("assets", []), "beats": out}


def probe_cameras(story_dict: dict, beat_ids: Iterable[str], grid: dict, game_root: str | Path, work_dir: str | Path, *,
                  keep: bool = False, port: int | None = None, repeatable: Iterable[str] = REPEATABLE) -> dict:
    """Run probe_story in the game's end-to-end test: {"passing": {beat: [camera, …]}, "candidates": [camera, …], "passed": bool, "probe": dir if kept else None}."""
    ids = list(beat_ids)
    probe = probe_story(story_dict, ids, grid, repeatable=repeatable)
    pkg = Path(work_dir) / "probe"
    pkg.mkdir(parents=True, exist_ok=True)
    try:
        story.write_story(pkg, probe, name="probe", title="Camera probe")
        (pkg / story.E2E_FILE).write_text(json.dumps({"schema": 1, "beats": {}}))
        run = story.e2e(pkg, game_root, **({"port": port} if port else {}))
        report = json.loads((pkg / story.E2E_DIR / "report.json").read_text())
        ok: dict[str, list[bool]] = {}
        for r in report.get("runs", []):
            if r.get("route") != "main":
                continue
            for b in r["beats"]:
                ok.setdefault(b["id"], []).append(all(c["ok"] for c in b["checks"] if c["name"] == "highlights in view"))
        cams = candidates(grid)
        passing = {}
        for beat in ids:
            passing[beat] = [cams[i] for i in range(len(cams))
                             if (flags := ok.get(f"{beat}--{i}")) and len(flags) >= 2 and all(flags)]
        return {"passing": passing, "candidates": cams, "passed": bool(run.get("passed")), "probe": pkg if keep else None}
    finally:
        if not keep:
            shutil.rmtree(pkg, ignore_errors=True)
