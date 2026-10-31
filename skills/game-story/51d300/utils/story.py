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
from typing import Any, Callable, Iterable, Sequence

SCHEMA = 1
#: Schema 2 (the engine's design/16_story_v2.md): values, expressions, branches,
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
#: A beat's explanation (`more`, behind the card's ? button; engine design/16):
#: text or a list of paragraphs, each at most MORE_MAX characters ($$…$$ counts
#: half its source), at most MORE_PARAS of them. `more` on the beat is the only
#: hint field the game reads; these other names are refused wherever they appear.
MORE_MAX = 400
MORE_PARAS = 8
HINT_KEYS = ("hint", "hints", "explain", "explanation", "explanations", "details", "help", "tip", "tips")
#: Math: LaTeX between double dollar signs, typeset by the engine (design/18_math.md).
MATH = re.compile(r"\$\$(.*?)\$\$", re.S)


def text_length(text: str) -> int:
    """A text's length as the limits count it: a $$…$$ formula counts half its LaTeX source (at least 1)."""
    n, pos = 0, 0
    for m in MATH.finditer(text):
        n += m.start() - pos + max(1, -(-len(m.group(1)) // 2))
        pos = m.end()
    return n + len(text) - pos


def unclosed_math(text: str) -> str | None:
    """The text after a $$ that does not close, or None."""
    return text[text.rfind("$$"):] if text.count("$$") % 2 else None


#: Unicode maths characters that belong in a $$…$$ formula, written as LaTeX.
MATH_UNICODE = "≤≥≠≈±×÷·∙²³⁰¹⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ₖₙᵢⱼΣ∑√∂∇∞‖→⇒∈ŷȳ"
# Keys whose strings are expressions, ids or settings, never text a player reads.
_NOT_SHOWN = {"answer", "set", "when", "branch", "if", "as", "goto", "id", "action", "color", "asset",
              "clip", "entity", "entities", "ui", "camera", "spawn", "node", "feature", "dataset", "fields",
              "algorithm", "tools", "sliders", "measure", "kind", "tool", "param", "metric", "linkage",
              "method", "track", "intensity", "sfx", "music", "tone", "points"}
# {expr} and \val{expr} are the game's values, not text (literal braces are {{ }}).
_EXPR = re.compile(r"\\val\{[^{}]*\}|(?<!\{)\{[^{}]*\}(?!\})")


def _shown_texts(v, key=None):
    if key in _NOT_SHOWN:
        return
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _shown_texts(x, k)
    elif isinstance(v, list):
        for x in v:
            yield from _shown_texts(x, key)


def math_problems(story_or_beats: dict | list) -> list[dict]:
    """Every spot in a story's shown text where maths is not LaTeX: [{beat, text, problem}], empty when clean."""
    beats = story_or_beats.get("beats", []) if isinstance(story_or_beats, dict) else story_or_beats
    out = []
    for b in beats:
        for text in _shown_texts(b):
            for i, part in enumerate(text.split("$$")):
                bare = _EXPR.sub(" ", part)
                found = []
                if i % 2:
                    if "−" in bare:
                        found.append("a real minus inside a formula: write -")
                    if "/" in bare:
                        found.append("a fraction with / inside a formula: write \\frac")
                else:
                    bad = "".join(sorted(set(bare) & set(MATH_UNICODE)))
                    if bad:
                        found.append(f"Unicode maths outside $$: {bad}")
                    if re.search(r"\d\s*[−+=<>]\s*\d", bare):
                        found.append("arithmetic or a comparison outside $$")
                out += [{"beat": b.get("id"), "text": text, "problem": p} for p in found]
    return out


#: A story should ask the player something at least this often (beats).
CHOICE_EVERY = 5
#: Beats: a story aims for at most `BEATS_SOFT` (a warning above it) and may
#: never exceed `BEATS_HARD` (an error), for a deep lesson that needs the room.
BEATS_SOFT = 28
BEATS_HARD = 100
#: A story's level; absent means easy. Medium works the formulas on small real
#: numbers, hard shows failure modes with a visual scenario, extreme writes the
#: algorithm's core methods as Python on the code board beside its 3D lesson.
LEVELS = ("easy", "medium", "hard", "extreme")

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
        elif text_length(say) > SAY_MAX:
            errors.append(f"{where}: say is {text_length(say)} chars, max {SAY_MAX}")
        if isinstance(say, str) and unclosed_math(say):
            errors.append(f"{where}: say has a $$ that does not close: {unclosed_math(say)!r}")
        errors += [f"{where}: {x}" for x in more_problems(beat.get("more"))]

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
                    if isinstance(note, str) and text_length(note) > SAY_MAX:
                        errors.append(f"{where}: ui.note text is {text_length(note)} chars, max {SAY_MAX}")

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
            elif text_length(c["prompt"]) > SAY_MAX:
                errors.append(f"{where}: choice prompt is {text_length(c['prompt'])} chars, max {SAY_MAX}")
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
                elif text_length(fb) > SAY_MAX:
                    errors.append(f"{where}: option {j} feedback is {text_length(fb)} chars, max {SAY_MAX}")
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


def more_problems(more: Any) -> list[str]:
    """What is wrong with a beat's `more` (None when absent): the engine's rules, in Python."""
    if more is None:
        return []
    paras = [more] if isinstance(more, str) else more
    if not isinstance(paras, list) or not paras or not all(isinstance(t, str) and t.strip() for t in paras):
        return ["more must be text, or a list of paragraphs (no empty ones)"]
    out = []
    if len(paras) > MORE_PARAS:
        out.append(f"more has {len(paras)} paragraphs (max {MORE_PARAS})")
    for k, t in enumerate(paras):
        if text_length(t) > MORE_MAX:
            out.append(f"more[{k}] is {text_length(t)} chars (max {MORE_MAX})")
        if unclosed_math(t):
            out.append(f"more[{k}] has a $$ that does not close")
    return out


def hint_key_problems(story: dict) -> list[str]:
    """Hint fields the game does not read: any of HINT_KEYS anywhere, and `more` anywhere but on a beat."""
    out: list[str] = []

    def walk(v: Any, where: str, top: bool) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ("set", "args", "vars"):  # story variables and action arguments, not fields
                    continue
                if k in HINT_KEYS:
                    out.append(f"{where}: {k!r} is not read by the game; a beat's explanation goes in its `more`")
                elif k == "more" and not top:
                    out.append(f"{where}: `more` here is not read; it belongs on the beat itself, next to `say`")
                walk(x, f"{where}.{k}", False)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{where}[{i}]", False)

    for b in story.get("beats") or []:
        if isinstance(b, dict):
            walk(b, f"beat {b.get('id')!r}", True)
    return out


def asks(beat: dict) -> bool:
    """Whether a beat asks the player something: a choice, an input, an action or an `until`."""
    w = beat.get("wait")
    return isinstance(w, dict) and any(k in w for k in ("choice", "input", "action", "until"))


def missing_more(story: dict) -> list[str]:
    """The ids of beats that ask something and have no `more` yet: each needs its explanation."""
    return [b.get("id") for b in story.get("beats") or [] if isinstance(b, dict) and asks(b) and b.get("more") is None]


def more_outline(package_or_dict: str | Path | dict) -> str:
    """Every beat's `more` paragraphs in story order, as Markdown to append to the outline (which does not show them)."""
    story = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    out = ["", "## Explanations (`more`, the card's ? button)", ""]
    for b in story.get("beats") or []:
        if not isinstance(b, dict):
            continue
        more = b.get("more")
        paras = [more] if isinstance(more, str) else list(more or [])
        out.append(f"**{b.get('id')}** — {b.get('say', '')}")
        out += [f"> {p}" for p in paras] if paras else ["> (no `more`)"]
        out.append("")
    return "\n".join(out) + "\n"


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
        # Packages written before the `group:value` convention also carry a
        # bare `story`; it is still accepted, only `type:story` is required.
        if "type:story" not in (man.get("tags") or []):
            errors.append("manifest tags must include 'type:story'")
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
    if isinstance(story, dict):
        e += hint_key_problems(story)
    errors += e
    warnings += w
    if lab_root is not None and not e and has_lab_machines(lab_root):
        rep = check_lab(story, lab_root)
        errors += rep["errors"]
        warnings += rep["warnings"]
        stats["lab_states"] = rep["states"]
        stats["max_objects"] = rep.get("max_objects")
    if not isinstance(package_or_dict, dict):
        why = e2e_problem(Path(package_or_dict))
        if why:
            warnings.append(f"not done: {why} (story.done(pkg, game_root) is the full check)")
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
    `type:story` and `level:<level>` (plus `tags`, each `group:value`); the story records
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
    tag_list = list(dict.fromkeys(["type:story", *tags, f"level:{level}"]))
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
    paths, max_objects}: states[i] the lab's state after each beat of the main
    line, paths the beat ids each playthrough took, max_objects the most live
    stage entities after any beat of any playthrough (information only).
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
    peak = 0
    for run in runs:
        pick = run.get("pick")
        tag = f" (choosing option {pick['option']} at {pick['beat']!r})" if pick else ""
        for snap in run["trace"]:
            lab = snap.get("lab") or {}
            bid = snap.get("id")
            peak = max(peak, len(snap.get("pos") or {}))
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
            "paths": [run["path"] for run in runs], "max_objects": peak}


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


# ------------------------------------------------------------ end-to-end in the game

E2E_FILE = "e2e.json"
E2E_DIR = "e2e"
#: What the visual review must end with (e2e/review.md).
REVIEWED = "reviewed: desktop, phone"


def _e2e_tool(game_root: str | Path) -> Path:
    """The engine's story-e2e.mjs next to the game (games share one engine)."""
    root = Path(game_root).expanduser().resolve()
    for base in (root.parent, root):
        tool = base / "engine" / "tools" / "story-e2e.mjs"
        if tool.is_file():
            return tool
    raise FileNotFoundError(f"no engine/tools/story-e2e.mjs beside {root}")


def e2e(package: str | Path, game_root: str | Path, *, viewports: str | Sequence[str] | None = None,
        port: int | None = None, timeout: int = 1800, out: str | Path | None = None,
        parallel: bool = True) -> dict:
    """Play a story package in the game itself, in a real browser (the engine's story-e2e.mjs).

    The game runs on its own dev server (a free port from 5300, the story
    added in memory, the game's files untouched) in headless Chromium; every
    route is played (the main one, each choice option with a goto, the routes
    e2e.json lists), each beat checked and screenshotted.

    `viewports` picks the sizes ("desktop", "phone", or both, the default). With
    several and `parallel`, each size runs as its own process at the same time
    (into `e2e/<size>/`) and the reports are merged into `e2e/report.json`: about
    half the wall time of one run that plays both. A run of one size writes
    `e2e/report.json` directly, but only a report with both sizes counts as done.
    Returns {passed, report, output}.
    """
    pkg = Path(package).expanduser().resolve()
    vps = [v.strip() for v in (viewports.split(",") if isinstance(viewports, str) else (viewports or ["desktop", "phone"])) if v.strip()]
    if len(vps) > 1 and parallel and out is None:
        # Each size on its own leased port: two runs picking "the first free port" at once would collide.
        leases = [_lease_port(f"story-e2e {pkg.name} {v}") for v in vps]
        try:
            procs = [_e2e_spawn(pkg, game_root, v, pkg / E2E_DIR / v, port=port) for v, (port, _) in zip(vps, leases)]
            outs = []
            for proc, v in procs:
                try:
                    o, e = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    o, e = proc.communicate()
                outs.append(f"[{v}] " + "\n".join(((o or "") + (e or "")).strip().splitlines()[-12:]))
        finally:
            for _, give_back in leases:
                give_back()
        report = e2e_merge(pkg, vps)
        return {"passed": bool(report and report.get("passed")), "report": report, "output": "\n".join(outs)}
    leased = None if port else _lease_port(f"story-e2e {pkg.name}")
    try:
        proc, _ = _e2e_spawn(pkg, game_root, ",".join(vps), Path(out) if out else None, port=port or leased[0])
        try:
            o, e = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            o, e = proc.communicate()
    finally:
        if leased:
            leased[1]()
    report_path = (Path(out) if out else pkg / E2E_DIR) / "report.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else None
    tail = "\n".join(((o or "") + (e or "")).strip().splitlines()[-25:])
    return {"passed": proc.returncode == 0 and bool(report and report.get("passed")), "report": report, "output": tail}


def _lease_port(owner: str) -> tuple[int, Callable[[], None]]:
    """A port for one run's dev server, and how to give it back.

    Leased from dex (`dex.ports`: lock, read the lease table, pick, record,
    release), so two runs started at once never get the same port. Without dex,
    a random free port above the tool's own range.
    """
    try:
        from dex import ports
        port = ports.lease(owner=owner)
        return port, lambda: ports.release(None, port)
    except Exception:
        import random
        import socket
        for _ in range(200):
            port = random.randint(5400, 5999)
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                    return port, lambda: None
                except OSError:
                    continue
        raise RuntimeError("no free port for the end-to-end test")


def _e2e_spawn(pkg: Path, game_root: str | Path, viewports: str, out: Path | None, port: int | None = None):
    cmd = ["node", str(_e2e_tool(game_root)), "--game", str(Path(game_root).expanduser().resolve()), "--story", str(pkg),
           "--viewports", viewports]
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        cmd += ["--out", str(out)]
    if port:
        cmd += ["--port", str(port)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True), viewports


def e2e_merge(package: str | Path, viewports: Sequence[str] = ("desktop", "phone")) -> dict | None:
    """Merge per-size reports (`e2e/<size>/report.json`) into `e2e/report.json`, which the game's pull reads.

    Passed only when every size's report passed, for the same story.json and
    e2e.json. Returns the merged report, or None when a size has no report.
    """
    pkg = Path(package)
    parts = []
    for v in viewports:
        f = pkg / E2E_DIR / v / "report.json"
        if not f.is_file():
            return None
        parts.append(json.loads(f.read_text()))
    first = parts[0]
    same = all(p.get("story", {}).get("sha256") == first.get("story", {}).get("sha256")
               and (p.get("tests") or {}).get("sha256") == (first.get("tests") or {}).get("sha256") for p in parts)
    merged = {**first, "passed": same and all(p.get("passed") for p in parts),
              "runs": [r for p in parts for r in p.get("runs", [])],
              "parts": [f"{v}/report.html" for v in viewports]}
    (pkg / E2E_DIR / "report.json").write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    return merged


def e2e_start(package: str | Path, game_root: str | Path, *, viewports: str = "desktop,phone",
              timeout: int = 1800) -> dict:
    """Start the end-to-end test in the background and return at once: {pid, log}.

    For a long lesson whose run outlasts one shell command. Then call
    `e2e_wait(pkg)` (blocks up to its timeout; call it again until `done`).
    Never write your own sleep loops around the tool. `timeout` (seconds) is
    passed to `e2e`: a run still going then is killed without a report, so a
    lesson with many routes (about 90 s per route per size) needs more.
    """
    pkg = Path(package).expanduser().resolve()
    (pkg / E2E_DIR).mkdir(parents=True, exist_ok=True)
    for f in ("e2e.done", "e2e.log"):
        (pkg / E2E_DIR / f).unlink(missing_ok=True)
    script = (f"import json, sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r}); "
              f"from utils import story; r = story.e2e({str(pkg)!r}, {str(Path(game_root).expanduser())!r}, viewports={viewports!r}, timeout={int(timeout)}); "
              f"print(r['output']); open({str(pkg / E2E_DIR / 'e2e.done')!r}, 'w').write(json.dumps({{'passed': r['passed']}}))")
    log = open(pkg / E2E_DIR / "e2e.log", "w")
    import sys as _sys
    proc = subprocess.Popen([_sys.executable, "-c", script], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"pid": proc.pid, "log": str(pkg / E2E_DIR / "e2e.log")}


def e2e_wait(package: str | Path, timeout: float = 540) -> dict:
    """Wait (up to `timeout` s, under a shell command's limit) for a run e2e_start began: {done, passed, output}."""
    import time as _time
    pkg = Path(package).expanduser().resolve()
    done = pkg / E2E_DIR / "e2e.done"
    t0 = _time.time()
    while not done.is_file() and _time.time() - t0 < timeout:
        _time.sleep(3)
    log = pkg / E2E_DIR / "e2e.log"
    tail = "\n".join(log.read_text().splitlines()[-25:]) if log.is_file() else ""
    if not done.is_file():
        return {"done": False, "passed": None, "output": tail}
    return {"done": True, "passed": json.loads(done.read_text()).get("passed"), "output": tail}


def e2e_segment(package: str | Path, game_root: str | Path, until: str, *, viewports: str = "desktop",
                timeout: int = 1800) -> dict:
    """Test the lesson only up to beat `until`, to iterate on one section without replaying the rest.

    Plays a cut copy of the story (beats up to and including `until`, which
    ends it; gotos and options leading past it are dropped; e2e.json entries
    for the cut beats removed) in `e2e/segments/<until>/`, at one size by
    default. Fast feedback while fixing a section; it is never the report the
    game's pull or `done` reads; the final run is `e2e(pkg, game_root)` on the
    whole story, both sizes. Returns {passed, report, output, beats}.
    """
    pkg = Path(package).expanduser().resolve()
    story = json.loads((pkg / STORY_FILE).read_text())
    ids = [b.get("id") for b in story.get("beats") or []]
    if until not in ids:
        raise ValueError(f"no beat {until!r}")
    keep = ids[: ids.index(until) + 1]
    kept = set(keep)
    beats = []
    for b in story["beats"][: len(keep)]:
        b = json.loads(json.dumps(b))
        if b.get("goto") not in (None, *kept):
            b.pop("goto")
        if b.get("branch"):
            b["branch"] = [x for x in b["branch"] if x.get("goto") in kept] or None
            if not b["branch"]:
                b.pop("branch")
        w = b.get("wait")
        if isinstance(w, dict) and "choice" in w:
            for o in w["choice"].get("options", []):
                if o.get("goto") not in (None, *kept):
                    o.pop("goto")
        beats.append(b)
    beats[-1]["end"] = True
    seg = pkg / E2E_DIR / "segments" / until
    if seg.exists():
        shutil.rmtree(seg)
    seg.mkdir(parents=True)
    (seg / STORY_FILE).write_text(json.dumps({**story, "beats": beats}, ensure_ascii=False, indent=2))
    tests = pkg / E2E_FILE
    if tests.is_file():
        t = json.loads(tests.read_text())
        t["beats"] = {k: v for k, v in (t.get("beats") or {}).items() if k in kept}
        t["routes"] = [r for r in t.get("routes") or [] if set((r.get("choose") or {})) <= kept]
        (seg / E2E_FILE).write_text(json.dumps(t, ensure_ascii=False, indent=2))
    r = e2e(seg, game_root, viewports=viewports, timeout=timeout, out=seg / E2E_DIR)
    return {**r, "beats": len(beats)}


def e2e_problem(package: str | Path) -> str | None:
    """Why a package has no passing end-to-end run for its current content, or None (the game's pull gate, exactly)."""
    import hashlib
    pkg = Path(package)
    report = pkg / E2E_DIR / "report.json"
    if not report.is_file():
        return "no end-to-end test report (e2e/report.json): run story.e2e(pkg, game_root)"
    try:
        rep = json.loads(report.read_text())
    except ValueError:
        return "e2e/report.json is not JSON"
    if not rep.get("passed"):
        return "its end-to-end test failed (see e2e/report.html)"
    sha256 = lambda f: hashlib.sha256(f.read_bytes()).hexdigest()  # noqa: E731
    if rep.get("story", {}).get("sha256") != sha256(pkg / STORY_FILE):
        return "story.json changed since its end-to-end test passed: run it again"
    tests = pkg / E2E_FILE
    if tests.is_file() and (rep.get("tests") or {}).get("sha256") != sha256(tests):
        return "e2e.json changed since its end-to-end test passed: run it again"
    return None


def e2e_skeleton(package_or_dict: str | Path | dict) -> dict:
    """A starting e2e.json for a story: every beat that needs an assertion or a player move, with what to fill in.

    Beats that compute (an act with `as`, a `set`, a `{…}` in the cue, an
    input) get an `expect` to fill with the cue's claims; beats that wait on
    `until` get a `do` to fill with the player's move (a wait on an action is
    played through the game's own control without one).
    The TODO markers make `e2e_tests_problems` fail until they are replaced.
    """
    story = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / STORY_FILE).read_text())
    beats: dict[str, dict] = {}
    for b in story.get("beats") or []:
        if not isinstance(b, dict) or not b.get("id"):
            continue
        entry: dict = {}
        computes = any(e.get("as") for e in act_entries(b.get("act"))) or b.get("set") or \
            "{" in str(b.get("say", "")).replace("{{", "") or (isinstance(b.get("wait"), dict) and "input" in b["wait"])
        if computes:
            entry["expect"] = ["TODO: what the cue claims, e.g. vote.cls == 1"]
        # A wait on an action is played through the game's own control by itself; `until` needs the move.
        w = b.get("wait")
        if isinstance(w, dict) and "until" in w:
            entry["do"] = [{"TODO": f"the player's move that makes {w['until']!r} true"}]
        if entry:
            beats[b["id"]] = entry
    return {"schema": 1, "beats": beats}


def e2e_tests_problems(package: str | Path) -> list[str]:
    """What is missing from a package's e2e.json for the definition of done (empty when it is complete)."""
    pkg = Path(package)
    path = pkg / E2E_FILE
    if not path.is_file():
        return ["no e2e.json: write the story's own assertions (story.e2e_skeleton(pkg) is a start)"]
    try:
        tests = json.loads(path.read_text())
    except ValueError as exc:
        return [f"e2e.json is not JSON: {exc}"]
    story = json.loads((pkg / STORY_FILE).read_text())
    out: list[str] = []
    if "TODO" in json.dumps(tests):
        out.append("e2e.json still has TODO entries")
    entries = tests.get("beats") or {}
    ids = {b.get("id") for b in story.get("beats") or [] if isinstance(b, dict)}
    for bid in entries:
        if bid not in ids:
            out.append(f"e2e.json beat {bid!r} is not in the story")
    expects = sum(len(e.get("expect") or []) for e in entries.values())
    if not expects:
        out.append("e2e.json has no expect: assert the numbers and states the cues claim")
    for b in story.get("beats") or []:
        w = b.get("wait") if isinstance(b, dict) else None
        if isinstance(w, dict) and "until" in w and not (entries.get(b.get("id")) or {}).get("do"):
            out.append(f"beat {b.get('id')!r} waits until {w['until']!r}: e2e.json needs its `do` (the player's move that makes it true)")
    return out


SCREENSHOT_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def clear_screenshots(package: str | Path) -> dict:
    """Delete a package's end-to-end screenshots, keeping the reports: {files, bytes}.

    Removes every image under e2e/ (the runs' and segments' shots, the small
    review copies) and the review sheets made from them (.review/), then the
    folders that leaves empty. report.json, e2e.done, the logs and review.md
    stay: the game's pull gate needs only report.json. `done` calls this once
    a story passes, so screenshots never pile up (a few hundred MB a story).
    """
    pkg = Path(package)
    files = size = 0
    for base in (pkg / E2E_DIR, pkg / ".review"):
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.is_file() and f.suffix.lower() in SCREENSHOT_SUFFIXES:
                size += f.stat().st_size
                f.unlink()
                files += 1
        for d in sorted((d for d in base.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)) + [base]:
            if d != pkg / E2E_DIR and not any(d.iterdir()):
                d.rmdir()
    return {"files": files, "bytes": size}


def done(package: str | Path, game_root: str | Path, *, known: Iterable[str] | None = None,
         keep_screenshots: bool = False) -> dict:
    """The definition of done for a story package: {ok, errors, warnings, cleared}.

    1. `validate_story(pkg, known=…, lab=game_root)` is ok (format, the
       game's engine, its lab machines, every route);
    2. e2e.json holds real assertions (`e2e_tests_problems`);
    3. a passing end-to-end run for exactly this story.json and e2e.json
       (`e2e_problem`, the game's pull gate);
    4. the visual review is recorded: e2e/review.md ends with
       "reviewed: desktop, phone".

    Once all four hold, the screenshots are deleted (`clear_screenshots`;
    `cleared` says how many): they were only for the review, which is done.
    `keep_screenshots=True` keeps them.
    """
    pkg = Path(package)
    known_set = set(known) if known is not None else known_assets(game_root)
    rep = validate_story(pkg, known=known_set, lab=game_root)
    errors = list(rep["errors"])
    errors += e2e_tests_problems(pkg)
    why = e2e_problem(pkg)
    if why:
        errors.append(why)
    else:
        runs = (json.loads((pkg / E2E_DIR / "report.json").read_text()).get("runs") or [])
        for v in ("desktop", "phone"):
            if not any(r.get("viewport") == v and r.get("route") == "main" for r in runs):
                errors.append(f"the end-to-end report has no {v} run of the main route: the final run plays both sizes")
    review = pkg / E2E_DIR / "review.md"
    if not review.is_file() or REVIEWED not in review.read_text():
        errors.append(f"no visual review: look at every screenshot (both sizes) and write e2e/review.md ending with {REVIEWED!r}")
    ok = not errors
    cleared = clear_screenshots(pkg) if ok and not keep_screenshots else {"files": 0, "bytes": 0}
    return {"ok": ok, "errors": errors, "warnings": rep["warnings"], "cleared": cleared}


def outline(package_or_dict: str | Path | dict, *, more: bool = False) -> str:
    """The story as a readable playthrough (Markdown): the review a model's previews give; more=True lists each beat's `more` paragraphs ("   - ? …") under its header.

    Write it to `previews/outline.md` and read it end to end before calling a
    story done: does each beat show one idea, does every choice follow from
    what the player has just seen? Pass `more=True` to read the explanations
    (the card's ? button) in place too.
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
        if more and b.get("more"):
            paras = b["more"] if isinstance(b["more"], list) else [b["more"]]
            lines.extend(f"   - ? {p}" for p in paras)
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
