"""Stories: lesson scripts the game's story runner plays, packaged as kind "story".

A story is data, not code: a list of beats, each a short cue plus what the
camera, the scene and the player do. The game's engine implements the same
schema (schema 1); `validate_story` is the contract both sides keep.

A story package is a directory holding `story.json` and `manifest.json`.
`export_story` copies it to `<game_root>/public/assets/story/<name>/` and
rebuilds the game's `public/assets/index.json` with game-mesh's
`catalog.rebuild_index`, so stories sit in the same index as every model and
sound they reference.

Every station in the game is a **lab machine** (the engine's
`@games/engine/lab`): states, the inputs each accepts and what they do. A
story's beats are inputs to it, exactly as the player's tools, buttons and
sliders are, so a story must only ask for what the lab can do at that point.
`check_lab(story, game_root)` plays the story through the game's own machine
and handlers and reports what it refuses; `validate_story(..., lab=game_root)`
makes that part of "valid"; `lab_machine(algorithm, game_root)` describes a
machine for the writer.
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
#: Schema 2 (the engine's design/14_story_v2.md): values, expressions, branches,
#: several acts per beat, number inputs, richer highlights. Checked and played by
#: the game's own engine (storycheck.mjs, storysim.mjs), so it needs a game root.
SCHEMAS = (1, 2)
#: Longest highlight callout.
CALLOUT_MAX = 40
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
#: A data point the game made (`data.scatter` names them p0, p1, ...): a highlight may aim at one.
DATA_POINT = re.compile(r"^p\d+$")

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
    data_ids: set[str] = set()
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
                # A point a story adds under its own id can be highlighted like a p<N> point.
                pid = (act.get("args") or {}).get("id") if name in ("data.add", "data.outlier") else None
                if isinstance(pid, str) and pid:
                    data_ids.add(pid)
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
            elif ("entity" in hl and hl["entity"] not in alive and hl["entity"] not in data_ids
                  and not DATA_POINT.match(str(hl["entity"]))):
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
                   packages_dir: str | Path | None = None,
                   lab: str | Path | None = None) -> dict:
    """Check a story (a package directory or the story dict), schema 1 or 2.

    Schema 2 is checked by the game's own engine (`check_with_engine`), so it
    needs `lab` (or `game_root`); schema 1 gets this module's checks, and the
    engine's too when a game is given.

    Asset references are checked when `game_root` or `known` is given: each
    must be in that game's `public/assets/index.json` or in the project's own
    packages (`packages_dir`, default this project). The story is also played
    through its station's lab machine (`check_lab`) in the game at `lab`
    (default `game_root`), when that game has lab machines. Returns
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
    lab_root = lab if lab is not None else game_root
    v2 = isinstance(story, dict) and story.get("schema") == 2
    if v2:
        # Schema 2: the game's engine is the validator (exact parity with what the game plays).
        e, w, stats = [], [], story_stats(story)
        if lab_root is None:
            e.append("schema 2 is checked by the game's engine: pass lab=<game root> (or game_root)")
        else:
            eng = check_with_engine(story, lab_root, known=known_set)
            e += eng["errors"]
            w += eng["warnings"]
    else:
        e, w, stats = _check_story(story, known_set)
        if lab_root is not None and not e and has_engine(lab_root):
            eng = check_with_engine(story, lab_root, known=known_set)
            e += eng["errors"]
            w += [x for x in eng["warnings"] if x not in w]
    errors += e
    warnings += w
    if lab_root is not None and not e and has_lab_machines(lab_root):
        rep = check_lab(story, lab_root)
        errors += rep["errors"]
        warnings += rep["warnings"]
        stats["lab_states"] = rep["states"]
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def act_entries(act: Any) -> list[dict]:
    """A beat's acts as a flat list (schema 2: one act, a list, `parallel` groups)."""
    out: list[dict] = []
    for e in act if isinstance(act, list) else ([act] if act else []):
        if isinstance(e, dict) and isinstance(e.get("parallel"), list):
            out += [x for x in e["parallel"] if isinstance(x, dict)]
        elif isinstance(e, dict):
            out.append(e)
    return out


def story_stats(story: dict) -> dict:
    """Beats, choices, inputs, actions used and level of a story (either schema): what the manifest records."""
    beats = [b for b in story.get("beats") or [] if isinstance(b, dict)]
    acts = sorted({e.get("action") for b in beats for e in act_entries(b.get("act")) if e.get("action")})
    waits = [b.get("wait") for b in beats if isinstance(b.get("wait"), dict)]
    return {"beats": len(beats), "choices": sum(1 for w in waits if "choice" in w),
            "inputs": sum(1 for w in waits if "input" in w), "assets": len(story.get("assets") or []),
            "level": story.get("level", "easy"), "actions": acts}


def has_engine(game_root: str | Path) -> bool:
    """Whether a game has the engine's story module and node to run it (for validation parity)."""
    import shutil as _shutil
    root = Path(game_root).expanduser()
    return bool(_shutil.which("node")) and (root / "src" / "actions" / "index.js").is_file() and \
        any((p / "@games" / "engine" / "src" / "story" / "validate.js").exists() or (p / "@games" / "engine").exists()
            for p in [root / "node_modules", root.parent / "node_modules"])


def check_with_engine(package_or_dict: str | Path | dict, game_root: str | Path, *,
                      known: Iterable[str] | None = None) -> dict:
    """Validate a story with the game's own engine (`validateStory`, via storycheck.mjs): {ok, errors, warnings}.

    The game supplies its actions, its data point ids (p0, p1, …) and its `$`
    reads; `known` adds the assets that may be spawned.
    """
    story = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / STORY_FILE
        path.write_text(json.dumps(story, ensure_ascii=False))
        cmd = ["node", str(Path(__file__).with_name("storycheck.mjs")), str(path), str(Path(game_root).expanduser().resolve())]
        if known is not None:
            kp = Path(tmp) / "known.json"
            kp.write_text(json.dumps(sorted(known)))
            cmd.append(str(kp))
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or "").strip().splitlines()[-3:]
            return {"ok": False, "errors": ["the game's engine could not check the story: " + " / ".join(tail)], "warnings": []}
    rep = json.loads(out)
    return {"ok": rep["ok"], "errors": [f"engine: {x}" for x in rep["errors"]], "warnings": rep.get("warnings", [])}


def reads(game_root: str | Path) -> list[dict]:
    """The `$` paths the game can read (its src/reads.js READS: path, what, example)."""
    out = subprocess.run(["node", "--input-type=module", "-e",
                          f"const m = await import({json.dumps((Path(game_root).expanduser().resolve() / 'src' / 'reads.js').as_uri())});"
                          "console.log(JSON.stringify(m.READS || []));"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


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
    `type:story`, `story` and `level:<level>` (plus `tags`); the story records
    its `level`, `easy` when none is given. Refuses a story with structural errors
    (asset existence is not checked here: run `validate_story` with
    `game_root` for that). Returns the manifest path.
    """
    if not NAME.match(name):
        raise ValueError(f"name {name!r} must be kebab-case")
    story = {"schema": SCHEMA, **story, "id": name}
    if title:
        story["title"] = title
    if story.get("schema") == 2:
        # Schema 2 is the engine's to check (validate_story with a game root);
        # here only what the manifest needs.
        errors = [f"missing {f!r}" for f in ("title", "summary", "algorithm", "beats") if not story.get(f)]
        stats = story_stats(story)
    else:
        errors, _, stats = _check_story(story, None)
    if errors:
        raise ValueError("story does not validate: " + "; ".join(errors))
    pkg = Path(package)
    pkg.mkdir(parents=True, exist_ok=True)
    level = story.setdefault("level", "easy")
    (pkg / STORY_FILE).write_text(json.dumps(story, indent=2, ensure_ascii=False) + "\n")
    tag_list = list(dict.fromkeys(["type:story", "story", *tags, f"level:{level}"]))
    man = {
        "name": name,
        "kind": KIND,
        "title": story["title"],
        "files": [STORY_FILE],
        "schema": story.get("schema", SCHEMA),
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
    """Play a story through the game's own action handlers (storysim.mjs, needs node): one snapshot dict per beat.

    In a game with lab machines each act goes through the station's machine,
    and each snapshot has `lab`: {machine, state, refused?, waitRefused?, player?}.
    """
    story = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / STORY_FILE
        path.write_text(json.dumps(story, ensure_ascii=False))
        out = subprocess.run(["node", str(Path(__file__).with_name("storysim.mjs")), str(path),
                              str(Path(game_root).expanduser().resolve())],
                             capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def has_lab_machines(game_root: str | Path) -> bool:
    """Whether a game runs its stations as lab machines (its src/actions/index.js exports machineFor)."""
    index = Path(game_root).expanduser() / "src" / "actions" / "index.js"
    try:
        return "export function machineFor" in index.read_text()
    except OSError:
        return False


def check_lab(package_or_dict: str | Path | dict, game_root: str | Path) -> dict:
    """Play a story through the game (its engine's runner, its lab machines, its handlers) and report what goes wrong.

    The story plays as in the game (storysim.mjs): every beat's acts go through
    the station's lab machine, `$` reads come from the game, inputs are
    answered with their expression's value, and every choice option that has a
    `goto` (other than the main line's) gets a playthrough of its own.

    An error, in any playthrough, when:
    - the lab refuses a beat's act (it does not run in the game: the beats
      after it would play over a scene that is not there);
    - the player cannot do what a `wait: {action}` asks;
    - a cue, title, prompt, option or feedback shows "–" (a value that is not
      there, e.g. a `{…}` over a read that is null);
    - an input's answer is not a number;
    - the story does not finish (a loop, or a branch that never comes back).

    A `wait: {until}` that is still false when its beat shows is only a warning
    (the player's task makes it true). Returns {ok, errors, warnings, states,
    paths}: states[i] the lab's state after each beat of the main line, paths
    the beat ids each playthrough took.
    """
    import shutil as _shutil
    if not has_lab_machines(game_root):
        return {"ok": True, "errors": [], "warnings": [f"{game_root}: no lab machines to check against"], "states": [], "paths": []}
    if not _shutil.which("node"):
        return {"ok": True, "errors": [], "warnings": ["node is not installed: the lab machine was not checked"], "states": [], "paths": []}
    story = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    try:
        runs = explore(story, game_root)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "").strip().splitlines()[-3:]
        return {"ok": False, "errors": ["the story could not be played through the game: " + " / ".join(tail)],
                "warnings": [], "states": [], "paths": []}
    errors: list[str] = []
    warnings: list[str] = []
    for run in runs:
        pick = run.get("pick")
        tag = f" (choosing option {pick['option']} at {pick['beat']!r})" if pick else ""
        for snap in run["trace"]:
            lab = snap.get("lab") or {}
            bid = snap.get("id")
            if lab.get("refused"):
                errors.append(f"beat {bid!r}{tag}: the {lab.get('machine')} lab refuses it: {lab['refused']}")
            if lab.get("waitRefused"):
                errors.append(f"beat {bid!r}{tag}: waits for {lab['waitRefused']}")
            for t in snap.get("missing") or []:
                errors.append(f"beat {bid!r}{tag}: shows a value that is not there (–): {t!r}")
            if snap.get("inputFailed"):
                errors.append(f"beat {bid!r}{tag}: {snap['inputFailed']}")
            if snap.get("untilUnmet") and not pick:
                warnings.append(f"beat {bid!r}: {snap['untilUnmet']}")
        if not run.get("finished"):
            errors.append(f"the story does not finish{tag}: it stopped after {run['path'][-12:]}")
    errors = list(dict.fromkeys(errors))
    main = runs[0]["trace"] if runs else []
    return {"ok": not errors, "errors": errors, "warnings": list(dict.fromkeys(warnings)),
            "states": [(snap.get("lab") or {}).get("state") for snap in main],
            "paths": [run["path"] for run in runs]}


def explore(package_or_dict: str | Path | dict, game_root: str | Path) -> list[dict]:
    """Every playthrough check_lab makes: [{pick, trace, finished, path}], the main line first."""
    story = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / STORY_FILE
        path.write_text(json.dumps(story, ensure_ascii=False))
        out = subprocess.run(["node", str(Path(__file__).with_name("storysim.mjs")), str(path),
                              str(Path(game_root).expanduser().resolve()), "--explore"],
                             capture_output=True, text=True, check=True).stdout
    return json.loads(out)["runs"]


def lab_machine(algorithm: str, game_root: str | Path) -> str:
    """A station's lab machine as Markdown (its states, and what each accepts and does): read it before writing beats."""
    if not has_lab_machines(game_root):
        return ""
    out = subprocess.run(["node", str(Path(__file__).with_name("storysim.mjs")), "--machine", algorithm,
                          str(Path(game_root).expanduser().resolve())],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    note = "" if data.get("own") else \
        "This station has no machine of its own yet: the default one accepts every action and stops what is running first.\n\n"
    return note + data.get("markdown", "")


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
    def act_text(e: dict) -> str:
        bits = f"{e.get('action')} {json.dumps(e.get('args', {}), ensure_ascii=False)}"
        return bits + (f" → {e['as']}" if e.get("as") else "")
    for i, b in enumerate(story.get("beats", []), 1):
        if b.get("section"):
            lines.append(f"### {b['section']}")
        lines.append(f"{i}. **{b.get('id')}** — {b.get('say', '')}")
        bits = []
        if b.get("when") is not None:
            bits.append(f"only when {b['when']}")
        if b.get("camera"):
            c = b["camera"]
            bits.append(f"camera frames {c['frame']}" if c.get("frame") else f"camera → {c.get('target')}")
        if b.get("spawn"):
            bits.append("spawn " + ", ".join(f"{s.get('id')}={s.get('asset')}" for s in b["spawn"]))
        if b.get("remove"):
            bits.append("remove " + ", ".join(b["remove"]))
        if b.get("act"):
            parts = []
            for e in b["act"] if isinstance(b["act"], list) else [b["act"]]:
                if isinstance(e, dict) and isinstance(e.get("parallel"), list):
                    parts.append("together(" + " | ".join(act_text(x) for x in e["parallel"]) + ")")
                elif isinstance(e, dict):
                    parts.append(act_text(e))
            bits.append("act " + " then ".join(parts))
        if b.get("set"):
            bits.append("set " + ", ".join(f"{k} = {v}" for k, v in b["set"].items()))
        if b.get("highlight"):
            hl = b["highlight"] if isinstance(b["highlight"], list) else [b["highlight"]]
            bits.append("highlight " + ", ".join(
                (h.get("entity") or ",".join(h.get("entities") or []) or f"ui:{h.get('ui')}") + (f" “{h['callout']}”" if h.get("callout") else "")
                for h in hl if isinstance(h, dict)))
        if b.get("sfx"):
            bits.append(f"sfx {b['sfx']}")
        if b.get("music"):
            bits.append(f"music {b['music']}")
        if bits:
            lines.append("   - " + "; ".join(bits))
        w = b.get("wait")
        if isinstance(w, dict) and "choice" in w:
            c = w["choice"]
            lines.append(f"   - **choice:** {c.get('prompt')}" + (f" → {c['as']}" if c.get("as") else ""))
            for o in c.get("options", []):
                mark = "✔" if o.get("correct") else "✘"
                go = f" → goto {o['goto']}" if o.get("goto") else ""
                lines.append(f"     - {mark} {o.get('label')} — {o.get('feedback')}{go}")
        elif isinstance(w, dict) and "input" in w:
            q = w["input"]
            lines.append(f"   - **input:** {q.get('prompt')} = `{q.get('answer')}` (± {q.get('tolerance', 'auto')}); wrong: {q.get('wrong', '')}")
        else:
            lines.append(f"   - wait: {json.dumps(w, ensure_ascii=False)}")
        route = []
        for br in b.get("branch") or []:
            route.append(f"if {br.get('if')} → {br.get('goto')}")
        if b.get("goto"):
            route.append(f"goto {b['goto']}")
        if b.get("end"):
            route.append("end")
        if route:
            lines.append("   - then: " + "; else ".join(route))
    return "\n".join(lines) + "\n"
