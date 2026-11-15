"""Game-asset conventions, package manifests, validation, and installing into a game.

An **asset package** is one task's output directory: the files it made plus a
`manifest.json` saying what each one is. A validated package in dex is the
deliverable. Games **pull** packages from dex: each game's own tooling (see its
`AGENTS.md`) calls `export(package, game_root)`, which copies the shipped files
into that game's `public/assets/<kind>/<name>/` and rebuilds the game-wide
`index.json` its loader reads. Nothing here knows which games exist, so
`game_root` is always given.

Kinds and what each must carry:

| kind        | files                       | manifest fields                         |
|-------------|-----------------------------|-----------------------------------------|
| block       | `<name>.glb`                | footprint, height, sockets              |
| landmark    | `<name>.glb`                | footprint, height, stages               |
| character   | `<name>.glb`                | height, clips, faction                  |
| environment | `<name>.glb` / textures     | footprint (tiles) or `tileable: true`   |
| sfx         | `<name>.ogg` + `.wav`       | duration, loop, category                |
| music       | `<name>.ogg` + `.wav`       | duration, loop, bpm, mood               |

    from utils import catalog
    catalog.write_manifest(out, name="tokyo-tower", kind="landmark",
                           files=["tokyo-tower.glb"], footprint=[2, 2])
    report = catalog.validate_package(out)      # {"ok": ..., "errors": [...]}
    catalog.export(out, game_root)              # done by a game's pull, not by the task
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Sequence

from . import gltf as _gltf

#: World units per map tile. Every model is authored at this scale.
UNITS_PER_TILE = 1.0
#: The game's current 2D renderer draws heights in pixels; this many pixels is one unit.
#: Use it to carry an existing landmark's proportions across (`px(92)`).
PIXELS_PER_UNIT = 32.0
#: Where `export()` puts things, under the game root it is given.
EXPORT_SUBDIR = Path("public") / "assets"

KINDS = ("block", "landmark", "character", "environment", "sfx", "music")
#: Clip names the game looks up on characters. Others are allowed; these are expected.
CHARACTER_CLIPS = ("idle", "walk", "run", "work", "attack", "hit", "die", "cheer")
#: Triangle budgets per kind — the world shows hundreds of characters at once.
TRIANGLE_BUDGET = {"block": 2000, "landmark": 20000, "character": 1500, "environment": 3000}
#: Audio is shipped at this rate.
SAMPLE_RATE = 44100

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
#: Manifest fields each kind must carry (beyond name, kind, files).
REQUIRED_FIELDS = {
    "block": ("footprint",),
    "landmark": ("footprint", "stages"),
    "character": ("height", "clips", "faction"),
    "environment": (),
    "sfx": ("duration", "loop", "category"),
    "music": ("duration", "loop", "bpm", "mood"),
}
MODEL_KINDS = ("block", "landmark", "character", "environment")


def px(pixels: float) -> float:
    """A height from the 2D renderer's pixels, in world units."""
    return pixels / PIXELS_PER_UNIT


def write_manifest(package: str | Path, *, name: str, kind: str, files: Sequence[str],
                   title: str = "", **fields: Any) -> Path:
    """Write `manifest.json` for a package; `files` are paths relative to it."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not NAME.match(name):
        raise ValueError(f"name {name!r} must be kebab-case: lowercase letters, digits, hyphens")
    pkg = Path(package)
    data: dict[str, Any] = {"name": name, "kind": kind, "title": title or name.replace("-", " ").title(),
                            "files": list(files)}
    if kind in MODEL_KINDS:  # axes and scale mean nothing for sound
        data.update({"units_per_tile": UNITS_PER_TILE, "up": "+Y", "forward": "+Z"})
    data.update({"created": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields})
    path = pkg / "manifest.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def read_manifest(package: str | Path) -> dict:
    """The package's manifest."""
    return json.loads((Path(package) / "manifest.json").read_text())


def wav_duration(path: str | Path) -> float:
    """Length of a WAV file in seconds."""
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def validate_package(package: str | Path) -> dict:
    """Check a package against its kind's contract; `ok` is what "done" means.

    Every listed file must exist; every `.glb` must pass `gltf.validate` within
    the kind's triangle budget, sit on the ground (min y ~ 0) and fit its
    footprint; characters must have the expected clips; landmarks must have
    `stage_*` nodes matching `stages`; audio must be readable with the
    declared duration. Previews (`previews/*.png`) are required for models.
    """
    pkg = Path(package)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        man = read_manifest(pkg)
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"manifest.json: {exc}"], "warnings": [], "models": {}}
    kind = man.get("kind")
    if kind not in KINDS:
        errors.append(f"unknown kind {kind!r}")
    if not NAME.match(str(man.get("name", ""))):
        errors.append(f"name {man.get('name')!r} is not kebab-case")
    missing_fields = [f for f in REQUIRED_FIELDS.get(kind, ()) if f not in man]
    if missing_fields:
        errors.append(f"manifest is missing {missing_fields} required for kind {kind!r}")
    models: dict[str, dict] = {}
    for rel in man.get("files", []):
        f = pkg / rel
        if not f.is_file():
            errors.append(f"{rel}: listed but missing")
            continue
        if f.suffix == ".glb":
            rep = _gltf.validate(f, max_triangles=TRIANGLE_BUDGET.get(kind))
            models[rel] = rep
            errors += [f"{rel}: {e}" for e in rep["errors"]]
            b = rep.get("bounds")
            if b:
                if abs(b["min"][1]) > 0.05 and kind in ("block", "landmark", "character"):
                    errors.append(f"{rel}: lowest point is y={b['min'][1]}, expected ~0 (origin at ground)")
                fp = man.get("footprint")
                if fp and kind in ("block", "landmark"):
                    w, d = fp
                    if b["max"][0] - b["min"][0] > w + 0.6 or b["max"][2] - b["min"][2] > d + 0.6:
                        warnings.append(f"{rel}: spans {b['max'][0]-b['min'][0]:.2f}x{b['max'][2]-b['min'][2]:.2f}, "
                                        f"footprint is {w}x{d}")
            if kind == "character":
                missing = [c for c in CHARACTER_CLIPS if c not in rep["clips"]]
                if missing:
                    errors.append(f"{rel}: missing clips {missing}")
            if kind == "landmark":
                stages = [n for n in rep["node_names"] if n and n.startswith("stage_")]
                if not stages:
                    errors.append(f"{rel}: no stage_* nodes for construction progress")
                elif man.get("stages") not in (None, len(stages)):
                    errors.append(f"{rel}: manifest says {man.get('stages')} stages, file has {len(stages)}")
        elif f.suffix == ".wav":
            try:
                with wave.open(str(f)) as w:
                    rate, channels = w.getframerate(), w.getnchannels()
                    d = w.getnframes() / rate
                if rate != SAMPLE_RATE:
                    errors.append(f"{rel}: {rate} Hz, expected {SAMPLE_RATE}")
                if channels not in (1, 2):
                    errors.append(f"{rel}: {channels} channels")
                # `durations` gives one per WAV, as {file: seconds} or a list in WAV order;
                # a single `duration` applies to a one-sound package.
                wavs = [x for x in man.get("files", []) if x.endswith(".wav")]
                durs = man.get("durations")
                if isinstance(durs, dict):
                    expected = durs.get(rel)
                elif isinstance(durs, list) and len(durs) == len(wavs):
                    expected = durs[wavs.index(rel)]
                else:
                    expected = man.get("duration") if len(wavs) == 1 else None
                if expected and abs(d - float(expected)) > 0.05:
                    warnings.append(f"{rel}: {d:.2f}s, manifest says {expected}")
            except Exception as exc:  # noqa: BLE001 — any unreadable WAV is the error
                errors.append(f"{rel}: unreadable WAV ({exc})")
        elif f.suffix == ".ogg" and f.stat().st_size < 200:
            errors.append(f"{rel}: suspiciously small OGG")
    if kind in ("block", "landmark", "character", "environment") and models:
        if not list((pkg / "previews").glob("*.png")):
            errors.append("no previews/*.png — render them with utils.preview and look at them")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "kind": kind,
            "name": man.get("name"), "models": {k: {x: v[x] for x in ("triangles", "clips", "bounds", "bytes")}
                                                 for k, v in models.items()}}


def export(package: str | Path, game_root: str | Path) -> dict:
    """Copy a valid package's files into the game at `game_root` and register it in its `public/assets/index.json`.

    Refuses an invalid package: the game should never load something the
    checks have not passed. Safe to run from several tasks at once: each
    package lands in its own directory by rename, and the index is rebuilt
    from every exported manifest rather than edited in place, so concurrent
    exports cannot drop each other's entries. Returns the entry written.
    """
    pkg = Path(package)
    rep = validate_package(pkg)
    if not rep["ok"]:
        raise ValueError("package does not validate: " + "; ".join(rep["errors"]))
    man = read_manifest(pkg)
    root = Path(game_root) / EXPORT_SUBDIR
    dest = root / man["kind"] / man["name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{man['name']}.{uuid.uuid4().hex}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    for rel in man["files"]:
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg / rel, target)
    entry = {k: v for k, v in man.items() if k not in ("created",)}
    entry["base"] = f"assets/{man['kind']}/{man['name']}/"
    entry["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (staging / "manifest.json").write_text(json.dumps(entry, indent=2) + "\n")
    if dest.exists():
        old = dest.parent / f".{man['name']}.{uuid.uuid4().hex}.old"
        dest.rename(old)
        staging.rename(dest)
        shutil.rmtree(old, ignore_errors=True)
    else:
        staging.rename(dest)
    rebuild_index(game_root)
    return entry


def packages(directory: str | Path) -> list[Path]:
    """Every asset package (a directory with a manifest.json) at or below `directory`, sets excluded."""
    d = Path(directory)
    out = []
    for m in sorted(d.rglob("manifest.json")):
        if "previews" in m.parts:
            continue
        try:
            if json.loads(m.read_text()).get("kind") == "set":
                continue
        except ValueError:
            pass
        out.append(m.parent)
    return out


def write_set_manifest(directory: str | Path, *, name: str, tags: Sequence[str], title: str = "",
                       **fields: Any) -> Path:
    """Write the top-level manifest of a task that made several packages (one per subdirectory).

    dex reads a task directory's `tags` from this file, so a set carries the
    tags its packages share; each package keeps its own manifest as well.
    """
    d = Path(directory)
    members = [str(p.relative_to(d)) for p in packages(d)]
    data = {"name": name, "kind": "set", "title": title or name.replace("-", " ").title(),
            "tags": list(tags), "packages": members,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}
    path = d / "manifest.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def export_all(directory: str | Path, game_root: str | Path) -> list[dict]:
    """Export every package under `directory` (a task that made a set of them)."""
    return [export(p, game_root) for p in packages(directory)]


def rebuild_index(game_root: str | Path) -> dict:
    """Regenerate `public/assets/index.json` from the exported manifests, written atomically."""
    root = Path(game_root) / EXPORT_SUBDIR
    assets = {}
    for m in sorted(root.glob("*/*/manifest.json")):
        if m.parent.name.startswith("."):  # another export's staging copy
            continue
        try:
            entry = json.loads(m.read_text())
        except (OSError, ValueError):  # replaced while we looked
            continue
        assets[f"{entry['kind']}/{entry['name']}"] = entry
    index = {"version": 1, "assets": assets}
    tmp = root / f".index.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, root / "index.json")
    return index
