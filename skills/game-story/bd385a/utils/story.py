"""Stories: lesson scripts the game's story runner plays, packaged as kind "story".

A story is data, not code: a list of beats, each a short cue plus what the
camera, the scene and the player do. The game's engine implements the same
schema (schema 1); `validate_story` is the contract both sides keep.

A story package is a directory holding `story.json` and `manifest.json`.
`export_story` copies it to `<game_root>/public/assets/story/<name>/` and
rebuilds the game's `public/assets/index.json` with game-mesh's
`catalog.rebuild_index`, so stories sit in the same index as every model and
sound they reference.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = 1
KIND = "story"
STORY_FILE = "story.json"
EXPORT_SUBDIR = Path("public") / "assets"
#: Longest cue a beat may show. Two short lines on screen.
SAY_MAX = 140
#: A story should ask the player something at least this often (beats).
CHOICE_EVERY = 5
#: Beats: a story aims for at most `BEATS_SOFT` (a warning above it) and may
#: never exceed `BEATS_HARD` (an error), for a deep lesson that needs the room.
BEATS_SOFT = 28
BEATS_HARD = 100
#: A story's level; absent means easy. Medium works the formulas on small real
#: numbers, hard shows failure modes with a visual scenario.
LEVELS = ("easy", "medium", "hard")

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
#: Ids of beats and spawned entities: letters, digits, `-` and `_`.
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
ASSET = re.compile(r"^[a-z]+/[a-z0-9]+(-[a-z0-9]+)*$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

#: The data palette (Okabe–Ito, colour-blind safe). Every asset and story uses
#: these names; a spawn's `color` is one of them or a `#rrggbb` hex.
PALETTE = {
    "class-0": "#0072B2",   # blue        — sphere markers
    "class-1": "#E69F00",   # orange      — cube markers
    "class-2": "#009E73",   # green       — cone markers
    "class-3": "#CC79A7",   # pink-purple — octahedron markers
    "positive": "#56B4E9",  # sky blue
    "negative": "#D55E00",  # vermillion
    "highlight": "#F0E442",  # yellow: selection, query, support vectors
    "neutral": "#9A9A9A",   # unlabelled data
    "ink": "#1F2328",       # axes, lines, text
}
#: Music stems a beat may ask for, besides a 0–1 `intensity`.
STEMS = ("calm", "focus", "intensity")

_ACTIONS_FILE = Path(__file__).with_name("actions.json")
#: The project this module was materialised into (`assets/<project>/utils/`).
PROJECT_DIR = Path(__file__).resolve().parents[1]


def actions() -> dict[str, dict]:
    """The `act` catalogue the game implements: `{name: {algorithm, summary, args}}`."""
    return json.loads(_ACTIONS_FILE.read_text())["actions"]


def known_assets(game_root: str | Path | None = None,
                 packages_dir: str | Path | None = None) -> set[str]:
    """`kind/name` of every asset in the game's index and in a project's packages.

    `packages_dir` defaults to the project this module lives in, so a task in
    `ml` sees assets other tasks built but have not exported yet.
    """
    found: set[str] = set()
    if game_root is not None:
        index = Path(game_root) / EXPORT_SUBDIR / "index.json"
        if index.is_file():
            try:
                found |= set(json.loads(index.read_text()).get("assets", {}))
            except ValueError:
                pass
    pdir = Path(packages_dir) if packages_dir is not None else PROJECT_DIR
    if pdir.is_dir():
        for m in pdir.rglob("manifest.json"):
            if "previews" in m.parts or "utils" in m.relative_to(pdir).parts:
                continue
            try:
                man = json.loads(m.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(man, dict) and man.get("kind") not in (None, "set") and man.get("name"):
                found.add(f"{man['kind']}/{man['name']}")
    return found


def fmt_number(value: Any, digits: int = 2) -> str:
    """A number as the game prints it: whole as is, else JS toFixed(digits), real minus "−", no "−0"."""
    from decimal import ROUND_HALF_UP, Decimal
    v = float(value)
    if v.is_integer():
        s = str(int(v))
    else:
        # toFixed rounds the exact binary value half-up (0.275 → "0.28").
        q = Decimal(abs(v)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        s = ("-" if v < 0 else "") + format(q, "f")
    if s.startswith("-"):
        s = s[1:] if float(s[1:]) == 0 else "−" + s[1:]
    return s


def fmt_fixed(value: Any, digits: int = 2) -> str:
    """A number as the game's num() prints it (model.params notes, model.explain): always `digits` decimals, real minus, no "−0.00"."""
    from decimal import ROUND_HALF_UP, Decimal
    v = float(value)
    s = format(Decimal(abs(v)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP), "f")
    return "−" + s if v < 0 and float(s) != 0 else s


def fmt_sig(value: Any) -> str:
    """A density as the game's sig() prints it (naive Bayes model.explain): 3 decimals ≥ 0.1, 3 significant digits ≥ 1e−4, else "1.58e−266"."""
    import math
    from decimal import ROUND_HALF_UP, Decimal
    v = float(value)
    if v == 0:
        return "0"
    a = abs(v)

    def fixed(x: float, d: int) -> str:
        return format(Decimal(x).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP), "f")

    if a >= 0.1:
        s = fixed(a, 3)
    elif a >= 1e-4:
        s = fixed(a, 2 - math.floor(math.log10(a)))       # JS toPrecision(3)
    else:                                                # JS toExponential(2): exponent unpadded
        e = math.floor(math.log10(a))
        m = (Decimal(a) / Decimal(10) ** e).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if m >= 10:
            m, e = (m / 10).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), e + 1
        s = f"{m}e{'−' if e < 0 else '+'}{abs(e)}"
    return "−" + s if v < 0 else s


def fmt_score(value: Any) -> str:
    """A score as Model Workshop's evaluation labels print it (fmt in evaluation.js: eval.score MSE, CV means, AUC): 2 decimals below 10, 1 below 100, else none; "–" if not finite."""
    import math
    from decimal import ROUND_HALF_UP, Decimal
    v = float(value)
    if not math.isfinite(v):
        return "–"
    a = abs(v)
    d = 0 if a >= 100 else 1 if a >= 10 else 2
    s = format(Decimal(a).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP), "f")
    return "-" + s if v < 0 else s          # JS toFixed: an ASCII minus, as the game shows it


def fmt_percent(value: Any) -> str:
    """A share as Model Workshop's pct() prints it (accuracy, recall, precision, F1 labels and meters): Math.round(value × 100) then "%"."""
    import math
    return f"{math.floor(float(value) * 100 + 0.5)}%"


# ------------------------------------------------------------------ validate


def _xz(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_story(story: Any, known: set[str] | None) -> tuple[list[str], list[str], dict]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(story, dict):
        return ["story must be a JSON object"], [], {}
    if story.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    for field in ("id", "title", "summary", "algorithm"):
        if not isinstance(story.get(field), str) or not story.get(field, "").strip():
            errors.append(f"missing {field!r}")
    if isinstance(story.get("id"), str) and not NAME.match(story["id"]):
        errors.append(f"id {story['id']!r} must be kebab-case")
    level = story.get("level", "easy")
    if level not in LEVELS:
        errors.append(f"level must be one of {LEVELS}, not {level!r}")
    elif level != "easy" and isinstance(story.get("id"), str) and not story["id"].endswith(f"-{level}"):
        warnings.append(f"a {level} story is usually named '<algorithm>-{level}', not {story['id']!r}")
    assets = story.get("assets")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        errors.append("assets must be a list of '<kind>/<name>' strings")
        assets = []
    for a in assets:
        if not ASSET.match(a):
            errors.append(f"asset {a!r} is not '<kind>/<name>'")
    listed = set(assets)
    beats = story.get("beats")
    if not isinstance(beats, list) or not beats:
        errors.append("beats must be a non-empty list")
        beats = []

    catalogue = actions()
    seen_beats: set[str] = set()
    alive: set[str] = set()
    spawned_ever: set[str] = set()
    used_assets: set[str] = set()
    choices = 0
    since_choice = 0
    acts: list[str] = []

    def need_known(ref: str, where: str) -> None:
        if known is not None and ref not in known:
            errors.append(f"{where}: {ref} is not in the game's index or the project's packages")

    for i, beat in enumerate(beats):
        where = f"beat {i}"
        if not isinstance(beat, dict):
            errors.append(f"{where}: must be an object")
            continue
        bid = beat.get("id")
        if not isinstance(bid, str) or not ID.match(bid):
            errors.append(f"{where}: id missing or not [A-Za-z0-9_-]")
        else:
            where = f"beat {bid!r}"
            if bid in seen_beats:
                errors.append(f"{where}: duplicate beat id")
            seen_beats.add(bid)
        say = beat.get("say")
        if not isinstance(say, str) or not say.strip():
            errors.append(f"{where}: say is empty")
        elif len(say) > SAY_MAX:
            errors.append(f"{where}: say is {len(say)} chars, max {SAY_MAX}")

        cam = beat.get("camera")
        if cam is not None:
            if not isinstance(cam, dict) or not _xz(cam.get("target")):
                errors.append(f"{where}: camera needs target [x, z]")
            else:
                for k in ("distance", "yaw", "pitch", "duration"):
                    if k in cam and not _num(cam[k]):
                        errors.append(f"{where}: camera.{k} must be a number")
                if _num(cam.get("distance")) and cam["distance"] <= 0:
                    errors.append(f"{where}: camera.distance must be > 0")

        for rid in beat.get("remove") or []:
            if rid not in alive:
                errors.append(f"{where}: remove {rid!r} was never spawned (or already removed)")
            alive.discard(rid)
        if "remove" in beat and not isinstance(beat["remove"], list):
            errors.append(f"{where}: remove must be a list of ids")

        spawn = beat.get("spawn")
        if spawn is not None and not isinstance(spawn, list):
            errors.append(f"{where}: spawn must be a list")
            spawn = []
        for s in spawn or []:
            if not isinstance(s, dict):
                errors.append(f"{where}: spawn entries must be objects")
                continue
            sid, asset = s.get("id"), s.get("asset")
            if not isinstance(sid, str) or not ID.match(sid):
                errors.append(f"{where}: spawn id missing or not [A-Za-z0-9_-]")
            elif sid in alive:
                errors.append(f"{where}: spawn id {sid!r} is already in the scene")
            else:
                alive.add(sid)
                spawned_ever.add(sid)
            if not isinstance(asset, str) or not ASSET.match(asset):
                errors.append(f"{where}: spawn {sid!r} asset must be '<kind>/<name>'")
            else:
                used_assets.add(asset)
                if asset not in listed:
                    errors.append(f"{where}: spawn {sid!r} uses {asset}, which is not in assets")
            if not _xz(s.get("at")):
                errors.append(f"{where}: spawn {sid!r} needs at [x, z]")
            if "scale" in s and not (_num(s["scale"]) and s["scale"] > 0):
                errors.append(f"{where}: spawn {sid!r} scale must be > 0")
            if "clip" in s and not isinstance(s["clip"], str):
                errors.append(f"{where}: spawn {sid!r} clip must be a name")
            if "color" in s and not (s["color"] in PALETTE or (isinstance(s["color"], str) and HEX.match(s["color"]))):
                errors.append(f"{where}: spawn {sid!r} color must be a palette name or #rrggbb")

        act = beat.get("act")
        if act is not None:
            if not isinstance(act, dict) or not isinstance(act.get("action"), str):
                errors.append(f"{where}: act needs an action name")
            else:
                name = act["action"]
                acts.append(name)
                if name not in catalogue:
                    errors.append(f"{where}: unknown action {name!r} (see actions.json)")
                if "args" in act and not isinstance(act["args"], dict):
                    errors.append(f"{where}: act.args must be an object")
                else:
                    unknown = set(act.get("args") or {}) - set(catalogue.get(name, {}).get("args", {}))
                    if name in catalogue and unknown:
                        warnings.append(f"{where}: {name} does not document args {sorted(unknown)}")
                    pts = (act.get("args") or {}).get("points") if name == "data.scatter" else None
                    if pts is not None:
                        bad = [r for r in pts if not (isinstance(r, list) and 2 <= len(r) <= 4
                                                      and all(_num(v) or v is None for v in r))] \
                            if isinstance(pts, list) else [pts]
                        if bad:
                            errors.append(f"{where}: data.scatter points are rows [x, z, class, y]; "
                                          f"the game drops {bad[0]!r}")
                    note = (act.get("args") or {}).get("text") if name == "ui.note" else None
                    if isinstance(note, str) and len(note) > SAY_MAX:
                        errors.append(f"{where}: ui.note text is {len(note)} chars, max {SAY_MAX}")

        hl = beat.get("highlight")
        if hl is not None:
            if not isinstance(hl, dict) or not (("entity" in hl) ^ ("ui" in hl)):
                errors.append(f"{where}: highlight is {{entity: id}} or {{ui: selector}}")
            elif "entity" in hl and hl["entity"] not in alive:
                errors.append(f"{where}: highlight {hl['entity']!r} is not in the scene")
            elif "ui" in hl and not (isinstance(hl["ui"], str) and hl["ui"].strip()):
                errors.append(f"{where}: highlight.ui must be a selector")

        sfx = beat.get("sfx")
        if sfx is not None:
            if not isinstance(sfx, str) or not NAME.match(sfx):
                errors.append(f"{where}: sfx must be a sound package name")
            else:
                need_known(f"sfx/{sfx}", where)
        music = beat.get("music")
        if music is not None:
            if not isinstance(music, dict) or not ({"track", "intensity"} & set(music)):
                errors.append(f"{where}: music is {{track?, intensity?}}")
            else:
                if "track" in music:
                    if not isinstance(music["track"], str) or not NAME.match(music["track"]):
                        errors.append(f"{where}: music.track must be a track package name")
                    else:
                        need_known(f"music/{music['track']}", where)
                if "intensity" in music:
                    v = music["intensity"]
                    if not ((_num(v) and 0 <= v <= 1) or v in STEMS):
                        errors.append(f"{where}: music.intensity is 0–1 or one of {STEMS}")

        wait = beat.get("wait")
        since_choice += 1
        if wait == "next":
            pass
        elif isinstance(wait, dict) and len(wait) == 1 and "action" in wait:
            if wait["action"] not in catalogue:
                errors.append(f"{where}: wait for unknown action {wait['action']!r}")
        elif isinstance(wait, dict) and len(wait) == 1 and "time" in wait:
            if not (_num(wait["time"]) and wait["time"] > 0):
                errors.append(f"{where}: wait.time must be seconds > 0")
        elif isinstance(wait, dict) and len(wait) == 1 and "choice" in wait:
            choices += 1
            since_choice = 0
            c = wait["choice"]
            opts = c.get("options") if isinstance(c, dict) else None
            if not isinstance(c, dict) or not isinstance(c.get("prompt"), str) or not c["prompt"].strip():
                errors.append(f"{where}: choice needs a prompt")
            elif len(c["prompt"]) > SAY_MAX:
                errors.append(f"{where}: choice prompt is {len(c['prompt'])} chars, max {SAY_MAX}")
            if not isinstance(opts, list) or len(opts) < 2:
                errors.append(f"{where}: choice needs at least 2 options")
                opts = []
            correct = 0
            for j, o in enumerate(opts):
                if not isinstance(o, dict):
                    errors.append(f"{where}: option {j} must be an object")
                    continue
                if not isinstance(o.get("label"), str) or not o["label"].strip():
                    errors.append(f"{where}: option {j} needs a label")
                if not isinstance(o.get("correct"), bool):
                    errors.append(f"{where}: option {j} needs correct: true|false")
                correct += o.get("correct") is True
                fb = o.get("feedback")
                if not isinstance(fb, str) or not fb.strip():
                    errors.append(f"{where}: option {j} needs feedback")
                elif len(fb) > SAY_MAX:
                    errors.append(f"{where}: option {j} feedback is {len(fb)} chars, max {SAY_MAX}")
            if opts and correct < 1:
                errors.append(f"{where}: choice has no correct option")
        else:
            errors.append(f"{where}: wait is 'next', {{action}}, {{time}} or {{choice}}")
        if since_choice > CHOICE_EVERY + 1:
            warnings.append(f"{where}: {since_choice} beats since the last choice")
            since_choice = 0

    for a in listed:
        need_known(a, "assets")
    unused = listed - used_assets
    if unused:
        warnings.append(f"assets listed but never spawned: {sorted(unused)}")
    if len(beats) > BEATS_HARD:
        errors.append(f"{len(beats)} beats: the hard limit is {BEATS_HARD}")
    elif len(beats) > BEATS_SOFT:
        warnings.append(f"{len(beats)} beats: aim for at most {BEATS_SOFT} (hard limit {BEATS_HARD})")
    if beats and choices == 0:
        errors.append("no choice beats: a story builds the algorithm through choices")
    if beats and isinstance(beats[-1], dict):
        last = beats[-1].get("act") or {}
        if last.get("action") != "sandbox.open":
            warnings.append("last beat does not hand off with sandbox.open")
    stats = {"beats": len(beats), "choices": choices, "assets": len(listed), "level": level,
             "actions": sorted(set(acts))}
    return errors, warnings, stats


def validate_story(package_or_dict: str | Path | dict, game_root: str | Path | None = None, *,
                   known: Iterable[str] | None = None,
                   packages_dir: str | Path | None = None) -> dict:
    """Check a story (a package directory or the story dict) against schema 1.

    Asset references are checked when `game_root` or `known` is given: each
    must be in that game's `public/assets/index.json` or in the project's own
    packages (`packages_dir`, default this project). Returns
    `{ok, errors, warnings, stats}`; `ok` is what "done" means.
    """
    errors: list[str] = []
    warnings: list[str] = []
    known_set: set[str] | None = None
    if known is not None or game_root is not None:
        known_set = set(known or ())
        if game_root is not None:
            known_set |= known_assets(game_root, packages_dir)
    if isinstance(package_or_dict, dict):
        story = package_or_dict
    else:
        pkg = Path(package_or_dict)
        try:
            man = json.loads((pkg / "manifest.json").read_text())
        except (OSError, ValueError) as exc:
            return {"ok": False, "errors": [f"manifest.json: {exc}"], "warnings": [], "stats": {}}
        if man.get("kind") != KIND:
            errors.append(f"manifest kind is {man.get('kind')!r}, expected {KIND!r}")
        if not NAME.match(str(man.get("name", ""))):
            errors.append(f"manifest name {man.get('name')!r} is not kebab-case")
        missing_tags = [t for t in ("type:story", "story") if t not in (man.get("tags") or [])]
        if missing_tags:
            errors.append(f"manifest tags must include {missing_tags}")
        files = man.get("files") or []
        for rel in files:
            if not (pkg / rel).is_file():
                errors.append(f"{rel}: listed but missing")
        if STORY_FILE not in files:
            errors.append(f"manifest files must list {STORY_FILE}")
        try:
            story = json.loads((pkg / STORY_FILE).read_text())
        except (OSError, ValueError) as exc:
            return {"ok": False, "errors": errors + [f"{STORY_FILE}: {exc}"], "warnings": [], "stats": {}}
        if isinstance(story, dict) and story.get("id") != man.get("name"):
            errors.append(f"story id {story.get('id')!r} differs from package name {man.get('name')!r}")
    e, w, stats = _check_story(story, known_set)
    errors += e
    warnings += w
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


# --------------------------------------------------------------- write/export


def spawned_assets(beats: Iterable[dict]) -> list[str]:
    """Every `kind/name` the beats spawn, once each in first-seen order: a story's `assets` list."""
    seen: list[str] = []
    for beat in beats:
        for spawn in beat.get("spawn", []):
            if spawn["asset"] not in seen:
                seen.append(spawn["asset"])
    return seen


def write_story(package: str | Path, story: dict, *, name: str, title: str = "",
                tags: Sequence[str] = ()) -> Path:
    """Write `story.json` and `manifest.json` into `package`.

    The story's `id` is set to `name`, and the manifest is always tagged
    `type:story` and `story`, plus `level:<level>` for a medium or hard story
    (and `tags`). Refuses a story with structural errors
    (asset existence is not checked here: run `validate_story` with
    `game_root` for that). Returns the manifest path.
    """
    if not NAME.match(name):
        raise ValueError(f"name {name!r} must be kebab-case")
    story = {"schema": SCHEMA, **story, "id": name}
    if title:
        story["title"] = title
    errors, _, stats = _check_story(story, None)
    if errors:
        raise ValueError("story does not validate: " + "; ".join(errors))
    pkg = Path(package)
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / STORY_FILE).write_text(json.dumps(story, indent=2, ensure_ascii=False) + "\n")
    level = story.get("level", "easy")
    level_tag = [f"level:{level}"] if level != "easy" else []
    tag_list = list(dict.fromkeys(["type:story", "story", *tags, *level_tag]))
    man = {
        "name": name,
        "kind": KIND,
        "title": story["title"],
        "files": [STORY_FILE],
        "schema": SCHEMA,
        "algorithm": story["algorithm"],
        "level": level,
        "summary": story["summary"],
        "assets": list(story["assets"]),
        "beats": stats["beats"],
        "choices": stats["choices"],
        "actions": stats["actions"],
        "tags": tag_list,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = pkg / "manifest.json"
    path.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
    return path


def export_story(package: str | Path, game_root: str | Path) -> dict:
    """Copy a valid story package into `<game_root>/public/assets/story/<name>/`.

    Refuses a story that fails `validate_story(package, game_root)`, so a story
    never reaches the game pointing at an asset it cannot load. Atomic like
    `catalog.export`: staged, then swapped in by rename; the index is rebuilt
    from every exported manifest. Returns the manifest entry written.
    """
    pkg = Path(package)
    rep = validate_story(pkg, game_root)
    if not rep["ok"]:
        raise ValueError("story does not validate: " + "; ".join(rep["errors"]))
    man = json.loads((pkg / "manifest.json").read_text())
    root = Path(game_root) / EXPORT_SUBDIR
    dest = root / KIND / man["name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{man['name']}.{uuid.uuid4().hex}.tmp"
    staging.mkdir()
    for rel in man["files"]:
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg / rel, target)
    entry = {k: v for k, v in man.items() if k != "created"}
    entry["base"] = f"assets/{KIND}/{man['name']}/"
    entry["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (staging / "manifest.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
    if dest.exists():
        old = dest.parent / f".{man['name']}.{uuid.uuid4().hex}.old"
        dest.rename(old)
        staging.rename(dest)
        shutil.rmtree(old, ignore_errors=True)
    else:
        staging.rename(dest)
    from . import catalog  # game-mesh, enabled alongside this skill

    catalog.rebuild_index(game_root)
    return entry


def story_packages(directory: str | Path) -> list[Path]:
    """Every story package at or below `directory`."""
    out = []
    for m in sorted(Path(directory).rglob("manifest.json")):
        try:
            if json.loads(m.read_text()).get("kind") == KIND:
                out.append(m.parent)
        except (OSError, ValueError):
            continue
    return out


def export_all_stories(directory: str | Path, game_root: str | Path) -> list[dict]:
    """Export every story package under `directory`."""
    return [export_story(p, game_root) for p in story_packages(directory)]


def simulate(package_or_dict: str | Path | dict, game_root: str | Path) -> list[dict]:
    """Play a story through the game's own action handlers (storysim.mjs, needs node): one snapshot dict per beat."""
    story = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / STORY_FILE
        path.write_text(json.dumps(story, ensure_ascii=False))
        out = subprocess.run(["node", str(Path(__file__).with_name("storysim.mjs")), str(path), str(game_root)],
                             capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def outline(package_or_dict: str | Path | dict) -> str:
    """The story as a readable playthrough (Markdown): the review a model's previews give.

    Write it to `previews/outline.md` and read it end to end before calling a
    story done: does each beat show one idea, does every choice follow from
    what the player has just seen?
    """
    if isinstance(package_or_dict, dict):
        story = package_or_dict
    else:
        story = json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    lines = [f"# {story.get('title', '')}", "", f"_{story.get('summary', '')}_", ""]
    for i, b in enumerate(story.get("beats", []), 1):
        lines.append(f"{i}. **{b.get('id')}** — {b.get('say', '')}")
        bits = []
        if b.get("camera"):
            bits.append(f"camera → {b['camera'].get('target')}")
        if b.get("spawn"):
            bits.append("spawn " + ", ".join(f"{s.get('id')}={s.get('asset')}" for s in b["spawn"]))
        if b.get("remove"):
            bits.append("remove " + ", ".join(b["remove"]))
        if b.get("act"):
            bits.append(f"act {b['act'].get('action')} {json.dumps(b['act'].get('args', {}), ensure_ascii=False)}")
        if b.get("highlight"):
            bits.append(f"highlight {b['highlight']}")
        if b.get("sfx"):
            bits.append(f"sfx {b['sfx']}")
        if b.get("music"):
            bits.append(f"music {b['music']}")
        if bits:
            lines.append("   - " + "; ".join(bits))
        w = b.get("wait")
        if isinstance(w, dict) and "choice" in w:
            c = w["choice"]
            lines.append(f"   - **choice:** {c.get('prompt')}")
            for o in c.get("options", []):
                mark = "✔" if o.get("correct") else "✘"
                lines.append(f"     - {mark} {o.get('label')} — {o.get('feedback')}")
        else:
            lines.append(f"   - wait: {json.dumps(w, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"
