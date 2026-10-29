"""Spoken captions for a game's stories: one clip per story text, in one `voice` package.

A story's card shows a line (`say`), sometimes a question (a choice's or a
typed answer's `prompt`), paragraphs behind its ? (`more`) and, after an
answer, a green or red hint (an option's `feedback`, an input's `right` /
`wrong`). The game speaks each of them when it shows (the engine's
story/captions.js), from clips made here with a local text-to-speech model:

    ollama run phonon --voice Preston "<text>" > clip.wav

Clips are keyed by `text_key` of the text *exactly as the story writes it*
(FNV-1a 64 of its UTF-8 bytes; the engine computes the same), so the stories
themselves never change: their end-to-end reports stay valid, and a story
task can rewrite its story without knowing about captions. A text that
changes simply has no clip until the captions are made again.

What is spoken is not always the text as written:
  * markdown and LaTeX are read out as words (`speakable`, `latex_words`);
  * a text that fills in values at runtime (`{…}` outside $$…$$, `\\val{…}`
    inside) is spoken with the values a play-through of the story in the game
    gives (`filled_texts`, from the game-story skill's simulator: the line,
    the question and the right answer's feedback of every beat on the main
    path), or with values the task infers from the beat's context, written to
    the package's `spoken.json` (`{key: words}`); one with neither is left
    unspoken and listed.

The package (kind "voice", named after the game):
    manifest.json   kind, name, files ["captions.json"], voice, model, clips, captions (sha256)
    captions.json   {schema, voice, model, clips: {key: "voice/<file>.ogg"}}: all the game loads
    texts.json      [{key, text, words, how, clip, uses}]: what is said for every text, for review
    spoken.json     optional: {key: words} written by the task (inferred numbers, better readings)
    voice/*.ogg     mono Opus; a clip's file name is a hash of the voice, model and words,
                    so a clip is made once and never changes under its name
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

KIND = "voice"
CAPTIONS_FILE = "captions.json"
SPOKEN_FILE = "spoken.json"
TEXTS_FILE = "texts.json"
CLIPS_DIR = "voice"
SCHEMA = 1
DEFAULT_VOICE = "Preston"
DEFAULT_MODEL = "phonon"
OPUS_KBPS = 10
EXPORT_SUBDIR = Path("public") / "assets"

# ---------------------------------------------------------------- keys and texts

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3


def text_key(text: str) -> str:
    """A story text's caption key: FNV-1a 64 of its UTF-8 bytes, 16 hex digits (the engine's textKey)."""
    h = _FNV_OFFSET
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


# The engine's story/expr.js: {expr} outside $$…$$ ({{ and }} are braces), \val{expr} inside.
_MATH = re.compile(r"\$\$([\s\S]+?)\$\$")
_PART = re.compile(r"\{\{|\}\}|\{([^{}]+)\}")
_VAL = re.compile(r"\\val\{((?:[^{}]|\{[^{}]*\})+)\}")


def _math_parts(text: str) -> list[tuple[bool, str]]:
    """Text outside $$…$$ and the LaTeX inside (without the $$), in order: [(is_math, piece)]."""
    out, last = [], 0
    for m in _MATH.finditer(text):
        if m.start() > last:
            out.append((False, text[last:m.start()]))
        out.append((True, m.group(1)))
        last = m.end()
    if last < len(text):
        out.append((False, text[last:]))
    return out


def is_runtime(text: str) -> bool:
    """Whether the game fills values into this text when it shows it."""
    for math, piece in _math_parts(text):
        if math and _VAL.search(piece):
            return True
        if not math and any(m.group(1) for m in _PART.finditer(piece)):
            return True
    return False


def story_texts(story: dict) -> list[dict]:
    """Every text a story's cards show and the game speaks: [{beat, field, text}].

    Fields: `say`, `prompt` (a choice's or an input's question), `more.<i>`,
    `feedback.<option>`, `right`, `wrong`.
    """
    out: list[dict] = []

    def add(beat: str, field: str, text: Any) -> None:
        if isinstance(text, str) and text.strip():
            out.append({"beat": beat, "field": field, "text": text})

    for b in story.get("beats", []):
        bid = str(b.get("id", ""))
        add(bid, "say", b.get("say"))
        w = b.get("wait")
        if isinstance(w, dict) and isinstance(w.get("choice"), dict):
            c = w["choice"]
            add(bid, "prompt", c.get("prompt"))
            for k, opt in enumerate(c.get("options") or []):
                if isinstance(opt, dict):
                    add(bid, f"feedback.{k}", opt.get("feedback"))
        if isinstance(w, dict) and isinstance(w.get("input"), dict):
            q = w["input"]
            add(bid, "prompt", q.get("prompt"))
            add(bid, "right", q.get("right"))
            add(bid, "wrong", q.get("wrong"))
        more = b.get("more")
        for k, para in enumerate(more if isinstance(more, list) else [more] if more else []):
            add(bid, f"more.{k}", para)
    return out


# ---------------------------------------------------------------- words

GREEK = {
    "alpha": "alpha", "beta": "beta", "gamma": "gamma", "delta": "delta", "epsilon": "epsilon",
    "varepsilon": "epsilon", "zeta": "zeta", "eta": "eta", "theta": "theta", "iota": "iota",
    "kappa": "kappa", "lambda": "lambda", "mu": "mu", "nu": "nu", "xi": "xi", "pi": "pi",
    "rho": "rho", "sigma": "sigma", "tau": "tau", "phi": "phi", "varphi": "phi", "chi": "chi",
    "psi": "psi", "omega": "omega", "Gamma": "gamma", "Delta": "delta", "Theta": "theta",
    "Lambda": "lambda", "Sigma": "sigma", "Phi": "phi", "Omega": "omega", "Pi": "pi",
}
# LaTeX commands read as words (anything else: its name, without the backslash).
COMMANDS = {
    "cdot": " times ", "times": " times ", "div": " divided by ", "pm": " plus or minus ",
    "le": " is at most ", "leq": " is at most ", "ge": " is at least ", "geq": " is at least ",
    "lt": " is less than ", "gt": " is greater than ", "ne": " is not ", "neq": " is not ",
    "approx": " is about ", "sim": " is about ", "equiv": " is the same as ", "propto": " grows with ",
    "to": " to ", "rightarrow": " to ", "leftarrow": " from ", "Rightarrow": " so ", "implies": " so ",
    "in": " in ", "notin": " not in ", "subset": " in ", "cup": " union ", "cap": " intersect ",
    "infty": "infinity", "sum": " the sum of ", "prod": " the product of ", "int": " the integral of ",
    "log": " log ", "ln": " natural log ", "exp": " e to the ", "max": " the max of ", "min": " the min of ",
    "argmax": " the arg max of ", "argmin": " the arg min of ", "lim": " the limit of ",
    "partial": " partial ", "nabla": " the gradient of ", "ldots": ", and so on, ", "dots": ", and so on, ",
    "cdots": ", and so on, ", "quad": ", ", "qquad": ", ", "mid": " given ", "vert": " ", "lvert": " ",
    "rvert": " ", "lfloor": " the floor of ", "rfloor": " ", "lceil": " the ceiling of ", "rceil": " ",
    "neg": " not ", "land": " and ", "lor": " or ", "forall": " for every ", "exists": " there is ",
    "circ": " degrees", "percent": " percent", "%": " percent",
}
_ORDINAL_POWER = {"2": " squared", "3": " cubed", "T": " transposed", "\\top": " transposed", "-1": " inverse"}


def _group(src: str, i: int) -> tuple[str, int]:
    """The {…} group (or single character / command) at src[i:], and where it ends."""
    while i < len(src) and src[i] == " ":
        i += 1
    if i >= len(src):
        return "", i
    if src[i] == "{":
        depth, j = 0, i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[i + 1:j], j + 1
            j += 1
        return src[i + 1:], len(src)
    if src[i] == "\\":
        m = re.match(r"\\[A-Za-z]+", src[i:])
        if m:
            return m.group(0), i + m.end()
    return src[i], i + 1


def latex_words(src: str) -> str:
    """LaTeX (the inside of $$…$$) read out as words, roughly as a teacher would say it."""
    out: list[str] = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", src[i:])
            name = m.group(1)
            i += m.end()
            if name in ("frac", "dfrac", "tfrac"):
                a, i = _group(src, i)
                b, i = _group(src, i)
                wa, wb = latex_words(a), latex_words(b)
                # A sum on top: "a plus b, all over 2", so the grouping is heard.
                out.append(f" {wa}, all over {wb} " if re.search(r" (plus|minus) ", f" {wa} ") else f" {wa} over {wb} ")
            elif name == "sqrt":
                a, i = _group(src, i)
                out.append(f" the square root of {latex_words(a)} ")
            elif name in ("hat", "bar", "overline", "tilde", "vec", "dot"):
                a, i = _group(src, i)
                word = {"overline": "bar", "vec": ""}.get(name, name)
                out.append(f" {latex_words(a)} {word} ")
            elif name in ("text", "mathrm", "mathbf", "mathit", "mathcal", "operatorname", "textbf", "boldsymbol", "mathbb", "texttt"):
                a, i = _group(src, i)
                out.append(f" {latex_words(a) if name not in ('text', 'texttt') else a} ")
            elif name in ("left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "limits"):
                pass
            elif name in (",", ";", ":", "!", " "):
                out.append(" ")
            elif name in ("{", "}"):
                pass
            elif name in GREEK:
                out.append(f" {GREEK[name]} ")
            elif name in COMMANDS:
                out.append(COMMANDS[name])
            else:
                out.append(f" {name} ")
        elif ch in "^_":
            a, i = _group(src, i + 1)
            if ch == "^":
                out.append(_ORDINAL_POWER.get(a.strip(), f" to the power {latex_words(a)} "))
            else:
                out.append(f" {latex_words(a)} ")
        elif ch in "{}":
            i += 1
        elif ch == "=":
            out.append(" equals ")
            i += 1
        elif ch == "+":
            out.append(" plus ")
            i += 1
        elif ch == "-":
            out.append(" minus ")
            i += 1
        elif ch == "<":
            out.append(" is less than ")
            i += 1
        elif ch == ">":
            out.append(" is greater than ")
            i += 1
        elif ch == "|":
            i += 1
        elif ch == "&":
            out.append(" ")
            i += 1
        elif ch == "O" and src[i + 1:i + 2] in ("(", "\\") and re.match(r"O\s*(\\left)?\(", src[i:]):
            out.append(" O of ")
            m = re.match(r"O\s*(\\left)?\(", src[i:])
            i += m.end()
        elif ch in "()[]":
            out.append(" ")
            i += 1
        else:
            out.append(ch)
            i += 1
    return " ".join("".join(out).split())


def _plain(text: str) -> str:
    """Markdown and symbols outside math, as words."""
    t = text.replace("{{", "{").replace("}}", "}")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)

    def code(m: re.Match) -> str:
        c = m.group(1)
        for a, b in (("==", " equals "), ("!=", " is not "), ("<=", " is at most "), (">=", " is at least "),
                     ("+=", " plus equals "), ("-=", " minus equals "), ("->", " to "), ("[", " "), ("]", " "),
                     ("(", " "), (")", " "), ("_", " "), (".", " dot "), (":", " ")):
            c = c.replace(a, b)
        return c

    t = re.sub(r"`([^`]+)`", code, t)
    t = re.sub(r"(?<=\S)\s+>\s+(?=\S)", " is greater than ", re.sub(r"(?<=\S)\s+<\s+(?=\S)", " is less than ", t))
    for a, b in (("→", " to "), ("←", " from "), ("↑", " up "), ("↓", " down "), ("×", " times "),
                 ("≈", " about "), ("≤", " at most "), ("≥", " at least "), ("≠", " is not "), ("−", "minus "),
                 ("…", "..."), ("·", ", "), ("%", " percent"), ("&", " and "), ("#", " number ")):
        t = t.replace(a, b)
    return t


def speakable(text: str) -> str:
    """A story text as words to speak: markdown dropped, $$…$$ math read out."""
    words = " ".join((f" {latex_words(p)} " if math else _plain(p)) for math, p in _math_parts(text))
    words = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(words.split()))
    return words.strip()


# ---------------------------------------------------------------- filled values

def filled_texts(package: str | Path, game_root: str | Path) -> dict[str, str]:
    """{key: the text as the game shows it} for the runtime texts a play-through fills in.

    Plays the story through the game (the game-story skill's `story.simulate`,
    the game's own seeds, so the values are the ones a player on the main path
    sees): each beat's line, question, and the right answer's feedback.
    """
    from . import story as story_mod  # game-story, enabled alongside this skill

    pkg = Path(package)
    st = json.loads((pkg / "story.json").read_text())
    by_beat = {str(b.get("id")): b for b in st.get("beats", [])}
    out: dict[str, str] = {}
    for snap in story_mod.simulate(pkg, game_root):
        beat = by_beat.get(str(snap.get("beat") or snap.get("id")))
        shown = (snap.get("shown") or {})
        if not beat:
            continue
        raw_texts = {e["field"]: e["text"] for e in story_texts({"beats": [beat]})}
        pairs = [("say", shown.get("say")), ("prompt", shown.get("prompt"))]
        w = beat.get("wait") if isinstance(beat.get("wait"), dict) else {}
        if isinstance(w.get("choice"), dict):
            picked = snap.get("picked")
            opts = w["choice"].get("options") or []
            k = picked if isinstance(picked, int) else next((i for i, o in enumerate(opts) if o.get("correct")), 0)
            pairs.append((f"feedback.{k}", shown.get("feedback")))
        elif isinstance(w.get("input"), dict):
            pairs.append(("right", shown.get("feedback")))
        for field, shown_text in pairs:
            raw = raw_texts.get(field)
            if raw and shown_text and is_runtime(raw) and "–" not in shown_text:
                out[text_key(raw)] = shown_text
    return out


# ---------------------------------------------------------------- the plan

def story_packages(project_dir: str | Path) -> list[Path]:
    """Every story package in a dex project (its task directories), newest manifest per story name."""
    newest: dict[str, Path] = {}
    for m in sorted(Path(project_dir).glob("*/**/manifest.json")):
        if any(p.startswith(".") or p in ("e2e", "previews", "utils") for p in m.relative_to(project_dir).parts):
            continue
        try:
            man = json.loads(m.read_text())
        except (OSError, ValueError):
            continue
        if man.get("kind") != "story" or not (m.parent / "story.json").is_file():
            continue
        name = man.get("name") or m.parent.name
        if name not in newest or m.stat().st_mtime > (newest[name] / "manifest.json").stat().st_mtime:
            newest[name] = m.parent
    return [newest[k] for k in sorted(newest)]


def plan(packages: Iterable[str | Path], *, game_root: str | Path | None = None,
         spoken: dict[str, str] | None = None) -> list[dict]:
    """What to speak for every text of these story packages.

    One item per distinct text: {key, text, words, how, uses: [{story, beat, field}]}, where
    `how` is "override" (spoken.json), "filled" (a play-through's values), "plain" (no
    runtime values) or "skip" (runtime values and neither of the others: `words` is None).
    """
    spoken = spoken or {}
    items: dict[str, dict] = {}
    for pkg in packages:
        pkg = Path(pkg)
        st = json.loads((pkg / "story.json").read_text())
        name = json.loads((pkg / "manifest.json").read_text()).get("name") or pkg.name
        texts = story_texts(st)
        filled: dict[str, str] = {}
        if game_root is not None and any(is_runtime(e["text"]) for e in texts):
            try:
                filled = filled_texts(pkg, game_root)
            except Exception as exc:  # noqa: BLE001 — a story the simulator cannot play is spoken without values
                print(f"warn  {name}: no play-through ({str(exc).splitlines()[0][:160]})")
        for e in texts:
            key = text_key(e["text"])
            item = items.get(key)
            if item is None:
                if key in spoken:
                    words, how = spoken[key], "override"
                elif not is_runtime(e["text"]):
                    words, how = speakable(e["text"]), "plain"
                elif key in filled:
                    words, how = speakable(filled[key]), "filled"
                else:
                    words, how = None, "skip"
                item = items[key] = {"key": key, "text": e["text"], "words": words or None, "how": how, "uses": []}
            item["uses"].append({"story": name, "beat": e["beat"], "field": e["field"]})
    return list(items.values())


def summary(items: list[dict]) -> dict:
    """How many texts are spoken each way, and how many are left unspoken."""
    out: dict[str, int] = {}
    for it in items:
        out[it["how"]] = out.get(it["how"], 0) + 1
    out["total"] = len(items)
    return out


# ---------------------------------------------------------------- clips

def clip_name(words: str, voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL) -> str:
    """A clip's file name: a hash of what makes it (voice, model, words)."""
    digest = hashlib.sha1("\n".join((model, voice, words)).encode()).hexdigest()[:16]
    return f"{CLIPS_DIR}/{digest}.ogg"


def synthesize_one(words: str, out: Path, *, voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL,
                   kbps: int = OPUS_KBPS, timeout: float = 120) -> Path:
    """Speak `words` with the local TTS model (ollama) and write them to `out` as mono Opus."""
    wav = subprocess.run(["ollama", "run", model, "--voice", voice, words],
                         capture_output=True, timeout=timeout, check=True).stdout
    if wav[:4] != b"RIFF":
        raise RuntimeError(f"ollama gave no WAV for {words[:60]!r}: {wav[:80]!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.ogg")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0",
                    "-ac", "1", "-c:a", "libopus", "-b:a", f"{kbps}k", "-application", "voip",
                    "-frame_duration", "60", "-compression_level", "10", str(tmp)],
                   input=wav, capture_output=True, timeout=timeout, check=True)
    tmp.replace(out)
    return out


def synthesize(items: list[dict], package: str | Path, *, voice: str = DEFAULT_VOICE,
               model: str = DEFAULT_MODEL, workers: int = 8, max_seconds: float | None = None) -> dict:
    """Make the clips these items need that the package does not have yet.

    Several at once (`workers`); stops starting new ones after `max_seconds`
    (a tool call has a time limit: call again until `left` is 0). Returns
    {made, had, failed: [{key, error}], left}.
    """
    pkg = Path(package)
    todo, had = [], 0
    for it in items:
        if not it.get("words"):
            continue
        rel = clip_name(it["words"], voice, model)
        if (pkg / rel).is_file():
            had += 1
        else:
            todo.append((it, rel))
    start = time.monotonic()
    made, failed = 0, []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        queue = iter(todo)
        def submit_next() -> bool:
            if max_seconds is not None and time.monotonic() - start > max_seconds:
                return False
            nxt = next(queue, None)
            if nxt is None:
                return False
            it, rel = nxt
            futures[pool.submit(synthesize_one, it["words"], pkg / rel, voice=voice, model=model)] = it
            return True
        for _ in range(workers * 2):
            if not submit_next():
                break
        while futures:
            done, _ = cf.wait(futures, return_when=cf.FIRST_COMPLETED)
            for f in done:
                it = futures.pop(f)
                try:
                    f.result()
                    made += 1
                except Exception as exc:  # noqa: BLE001 — reported, the rest go on
                    failed.append({"key": it["key"], "error": str(exc)[:200]})
                submit_next()
    left = sum(1 for it, rel in todo if not (pkg / rel).is_file()) - len(failed)
    return {"made": made, "had": had, "failed": failed, "left": max(0, left)}


# ---------------------------------------------------------------- the package

def read_spoken(package: str | Path) -> dict[str, str]:
    """The package's spoken.json ({key: words}), or {}."""
    p = Path(package) / SPOKEN_FILE
    return json.loads(p.read_text()) if p.is_file() else {}


def write_package(package: str | Path, *, name: str, items: list[dict], voice: str = DEFAULT_VOICE,
                  model: str = DEFAULT_MODEL, tags: Iterable[str] = ("type:voice",), prune: bool = True) -> dict:
    """Write captions.json, texts.json and manifest.json for the clips the package has; returns the manifest.

    captions.json is only the lookup the game downloads on every start
    (key -> clip); the review list of every text (texts.json) stays in dex.

    An item whose clip is missing is left out (and reported by `validate_voice`
    as nothing: a text without a clip is simply not spoken). With `prune`,
    clip files no item uses any more are deleted.
    """
    pkg = Path(package)
    clips, texts = {}, []
    for it in items:
        rel = clip_name(it["words"], voice, model) if it.get("words") else None
        if rel and (pkg / rel).is_file():
            clips[it["key"]] = rel
        texts.append({"key": it["key"], "text": it["text"], "words": it.get("words"), "how": it["how"],
                      "clip": clips.get(it["key"]), "uses": it["uses"]})
    if prune and (pkg / CLIPS_DIR).is_dir():
        used = set(clips.values())
        for f in (pkg / CLIPS_DIR).glob("*.ogg"):
            if f"{CLIPS_DIR}/{f.name}" not in used:
                f.unlink()
    captions = {"schema": SCHEMA, "voice": voice, "model": model, "clips": dict(sorted(clips.items()))}
    (pkg / CAPTIONS_FILE).write_text(json.dumps(captions, separators=(",", ":"), ensure_ascii=False) + "\n")
    (pkg / TEXTS_FILE).write_text(json.dumps(texts, indent=1, ensure_ascii=False) + "\n")
    man = {
        "name": name, "kind": KIND, "title": f"Spoken captions — {name}", "files": [CAPTIONS_FILE],
        "voice": voice, "model": model, "clips": len(clips), "unspoken": sum(1 for t in texts if not t["clip"]),
        "captions": hashlib.sha256((pkg / CAPTIONS_FILE).read_bytes()).hexdigest(),
        "tags": sorted(set(tags)), "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (pkg / "manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
    return man


def validate_voice(package: str | Path) -> dict:
    """{ok, errors, warnings}: a voice package the game can load."""
    pkg = Path(package)
    errors, warnings = [], []
    try:
        man = json.loads((pkg / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"manifest.json: {exc}"], "warnings": []}
    if man.get("kind") != KIND:
        errors.append(f"kind is {man.get('kind')!r}, not {KIND!r}")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(man.get("name", ""))):
        errors.append("name must be kebab-case")
    if not any(str(t).startswith("type:") for t in man.get("tags", [])):
        errors.append("tags need a type: tag (type:voice)")
    try:
        cap = json.loads((pkg / CAPTIONS_FILE).read_text())
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": errors + [f"{CAPTIONS_FILE}: {exc}"], "warnings": warnings}
    if man.get("captions") != hashlib.sha256((pkg / CAPTIONS_FILE).read_bytes()).hexdigest():
        errors.append("captions.json changed since manifest.json was written (write_package again)")
    for key, rel in (cap.get("clips") or {}).items():
        if not re.fullmatch(r"[0-9a-f]{16}", key):
            errors.append(f"bad key {key!r}")
            continue
        f = pkg / rel
        if not rel.startswith(f"{CLIPS_DIR}/") or not f.is_file():
            errors.append(f"{key}: clip {rel} is missing")
        elif f.read_bytes()[:4] != b"OggS":
            errors.append(f"{key}: {rel} is not an Ogg file")
        if len(errors) > 20:
            break
    if "texts" in cap:
        errors.append("captions.json carries the texts list: the game downloads it on every start (write_package again)")
    try:
        texts = json.loads((pkg / TEXTS_FILE).read_text())
    except (OSError, ValueError):
        texts = []
    unspoken = sum(1 for t in texts if not t.get("clip"))
    if unspoken:
        warnings.append(f"{unspoken} texts have no clip (runtime values not inferred, or not made yet)")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def export_voice(package: str | Path, game_root: str | Path) -> dict:
    """Install a voice package into `<game_root>/public/assets/voice/<name>/` and rebuild the index.

    Clips are named by their content, so only new ones are copied and ones no
    longer used are removed; captions.json and the manifest go in last.
    """
    pkg = Path(package)
    rep = validate_voice(pkg)
    if not rep["ok"]:
        raise ValueError("voice package does not validate: " + "; ".join(rep["errors"][:5]))
    man = json.loads((pkg / "manifest.json").read_text())
    cap = json.loads((pkg / CAPTIONS_FILE).read_text())
    dest = Path(game_root) / EXPORT_SUBDIR / KIND / man["name"]
    (dest / CLIPS_DIR).mkdir(parents=True, exist_ok=True)
    wanted = set(cap["clips"].values())
    for rel in wanted:
        if not (dest / rel).is_file():
            shutil.copy2(pkg / rel, dest / rel)
    for f in (dest / CLIPS_DIR).glob("*.ogg"):
        if f"{CLIPS_DIR}/{f.name}" not in wanted:
            f.unlink()
    shutil.copy2(pkg / CAPTIONS_FILE, dest / CAPTIONS_FILE)
    entry = {k: v for k, v in man.items() if k != "created"}
    entry["base"] = f"assets/{KIND}/{man['name']}/"
    # captions.json's version, for its URL; a clip's version is its file name (a content hash).
    entry["hashes"] = {CAPTIONS_FILE: man["captions"][:12]}
    entry["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = dest / f".manifest.{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(dest / "manifest.json")
    from . import catalog  # game-mesh, enabled alongside this skill

    catalog.rebuild_index(game_root)
    return entry


def make_captions(project_dir: str | Path, package: str | Path, *, name: str, game_root: str | Path | None = None,
                  voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL, workers: int = 8,
                  max_seconds: float | None = None) -> dict:
    """The whole job, resumable: plan every story in the project, make the missing clips, write the package.

    Call again until `left` is 0 (each call makes what it can in `max_seconds`).
    Returns {summary, synth, manifest, skipped: [{key, text, uses}]}.
    """
    pkg = Path(package)
    pkg.mkdir(parents=True, exist_ok=True)
    items = plan(story_packages(project_dir), game_root=game_root, spoken=read_spoken(pkg))
    synth = synthesize(items, pkg, voice=voice, model=model, workers=workers, max_seconds=max_seconds)
    man = write_package(pkg, name=name, items=items, voice=voice, model=model, prune=synth["left"] == 0)
    skipped = [{"key": it["key"], "text": it["text"], "uses": it["uses"]} for it in items if it["how"] == "skip"]
    return {"summary": summary(items), "synth": synth, "manifest": man, "skipped": skipped}


__all__ = [
    "KIND", "CAPTIONS_FILE", "SPOKEN_FILE", "CLIPS_DIR", "DEFAULT_VOICE", "DEFAULT_MODEL",
    "text_key", "is_runtime", "story_texts", "latex_words", "speakable", "filled_texts", "story_packages",
    "plan", "summary", "clip_name", "synthesize_one", "synthesize", "read_spoken", "write_package",
    "validate_voice", "export_voice", "make_captions",
]
