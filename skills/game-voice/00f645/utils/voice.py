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
  * a maths symbol's letter is said in uppercase ("Y hat", "slide K"), so the model names it;
  * a text that fills in values at runtime (`{…}` outside $$…$$, `\\val{…}`
    inside) is spoken with the values a play-through of the story in the game
    gives (`filled_texts`, from the game-story skill's simulator: the line,
    the question and the right answer's feedback of every beat on the main
    path), or with values the task infers from the beat's context, written to
    the package's `spoken.json` (`{key: words}`); one with neither is left
    unspoken and listed.

A game speaks either live (text-to-speech in the browser, from the words) or from clips;
for live speech a runtime text also has `live` words, its values kept as `{…}` for the
game to fill in as the card does (`live_words`).

The package (kind "voice", named after the story, or the game for the older game-wide one):
    manifest.json   kind, name, files ["captions.json"], voice, model, words, live, clips, captions (sha256)
    captions.json   {schema 2, voice, model, words: {key: words}, live: {key: words with {…}},
                    clips: {key: "voice/<file>.ogg"}}: all the game loads (a game without clips
                    gets it without `clips`: export_voice(…, clips=False))
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
SCHEMA = 2
DEFAULT_VOICE = "Preston"
DEFAULT_MODEL = "phonon"
OPUS_KBPS = 24
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

    Fields: `say`, `sayTouch` (the line shown instead on touch screens),
    `prompt` (a choice's or an input's question), `more.<i>`,
    `feedback.<option>`, `right`, `wrong`.
    """
    out: list[dict] = []

    def add(beat: str, field: str, text: Any) -> None:
        if isinstance(text, str) and text.strip():
            out.append({"beat": beat, "field": field, "text": text})

    for b in story.get("beats", []):
        bid = str(b.get("id", ""))
        add(bid, "say", b.get("say"))
        add(bid, "sayTouch", b.get("sayTouch"))
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


# A maths symbol's letter is spoken in uppercase: the TTS model says "Y", "W", "X" as letter
# names, but reads a lowercase y, w, x as a word or a fragment of one. Only the words change:
# the text, and so its key, stay as the story writes them.
def lone_letter(src: str, i: int) -> bool:
    """Whether src[i] is a letter with no letter on either side: in maths, a symbol."""
    ch = src[i]
    return ("a" <= ch <= "z" or "A" <= ch <= "Z") and not (i and src[i - 1].isalpha()) \
        and not src[i + 1:i + 2].isalpha()


# A lone lowercase letter in prose: not after an apostrophe (can't), not part of a dotted
# name (e.g., node.p, a.next), not the plural in bag(s).
_PROSE_LETTER = re.compile(r"(?<![^\W_])(?<!['’])(?<!\w\.)(?<![^\W\d_]\()([a-z])"
                           r"(?![^\W_])(?!['’](?!s\b))(?!\.\w)")
# The article "a" always has a noun after it. So "a" is a variable with an operator, an index
# or a call beside it, with punctuation or a verb or conjunction after it ("take a, then b",
# "a has digits"), or after another lone letter ("b b a").
_OPS = "=+*/<>≤≥≠^|&%−~"
_A_AFTER = re.compile(rf"(?:[{_OPS}]|\s-)\s*$|(?<![\w'’])[b-z],\s*$")
_A_BEFORE = re.compile(rf"\s*(?:[{_OPS}_]|-\s*(?:[\d(]|[A-Za-z]\b))|[\[(]|\s*(?:[,.;:?!)]|$)"
                       r"|\s+(?:and|or|is|are|was|has|had|can|will|would|equals|then|if|but|to|of|in|at|on|by"
                       r"|with|from|gets|becomes|hits|holds|stays|starts|ends)\b(?!-)")
# Only a letter followed by more prose can be the article after another letter ("k a count").
_A_RUN = re.compile(r"(?<![\w'’])[b-z]\s+$")
_A_NOUN = re.compile(r"\s+[^\W\d_]{2,}")


def prose_letters(text: str) -> str:
    """Plain text (no $$…$$) with its single-letter variables in uppercase: "slide k to 4" -> "slide K to 4".

    A lone lowercase letter is a variable (English writes I in capitals), except "a", which
    is one only where it cannot be the article ("a = []", "b + a", "a[i]", "take a, then b",
    "a has digits", "b b a"). Inside `code` every lone letter is a variable; a runtime `{…}`
    is left as it is.
    """
    def is_variable_a(part: str, start: int, end: int) -> bool:
        if _A_BEFORE.match(part, end):
            return True
        if _A_RUN.search(part, 0, start):
            return not _A_NOUN.match(part, end)
        return bool(_A_AFTER.search(part, 0, start))

    def one(part: str, code: bool) -> str:
        if part.startswith("{"):
            return part
        return _PROSE_LETTER.sub(lambda m: m.group(1).upper() if m.group(1) != "a" or code
                                 or is_variable_a(part, m.start(), m.end()) else "a", part)

    pieces = re.split(r"(`[^`]+`|\{[^{}]*\})", text)
    return "".join(one(p, p.startswith("`")) for p in pieces)


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
            out.append(ch.upper() if lone_letter(src, i) else ch)
            i += 1
    return " ".join("".join(out).split())


def _plain(text: str) -> str:
    """Markdown and symbols outside math, as words."""
    t = prose_letters(text.replace("{{", "{").replace("}}", "}"))
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


# ---------------------------------------------------------------- live words

# While a runtime text is read, each of its values stands in as a number the readers pass
# through whole ("93710000", "93710001", …): a value is a number to them, so the words
# around it come out as they would around the number the card shows ("minus {x}", "{p}
# percent"). The stand-ins then go back as `{expr}`, exactly as the story writes them.
_STAND_IN = "93710{:03d}"
_STAND_IN_ANY = re.compile(r"93710\d{3}")
# A value said as it is: a number, or a word or two ("left", "class B").
_NUMBER = re.compile(r"(?:minus |[-−])?\d[\d,]*(?:\.\d+)?(?: percent|%| degrees?|°)?")
_SHORT = re.compile(r"[A-Za-z][\w'’-]*(?: [A-Za-z][\w'’-]*)?")
# An expression whose value is a number, when no play-through says what it is: a format
# (`:2`, `:%`; `_FORMATTED`), arithmetic, a numeric function, or a read the games keep numbers under.
_FORMATTED = re.compile(r":\s*(?:\d+|%)\s*$")
_NUMERIC_EXPR = re.compile(
    r"[\w)]\s*[+*/%]\s*[\w($.]|\s-\s*[\w($.]|\b(?:round|abs|floor|ceil|sqrt|exp|ln|pow|len|min|max)\("
    r"|^\s*\$(?:model\.(?:loss|param)|data\.count|code\.hits|code\.depth)\b")


def _stand_ins(text: str) -> tuple[str, list[str]] | None:
    """The text with each runtime value as a numeric stand-in, and its placeholders (`{expr}`) in order.

    None when a `\\val{…}` holds braces of its own, which a `{…}` outside maths cannot.
    """
    slots: list[str] = []

    def stand(expr: str) -> str:
        slots.append("{" + expr + "}")
        return _STAND_IN.format(len(slots) - 1)

    out = []
    for math, piece in _math_parts(text):
        if math:
            if any("{" in m.group(1) or "}" in m.group(1) for m in _VAL.finditer(piece)):
                return None
            out.append("$$" + _VAL.sub(lambda m: f" {stand(m.group(1))} ", piece) + "$$")
        else:
            out.append(_PART.sub(lambda m: stand(m.group(1)) if m.group(1) else m.group(0), piece))
    return "".join(out), slots


def _readers() -> list:
    """The skill's readers, best first: speech's (brackets, limits, code), then this module's."""
    try:
        from .speech import speakable_code
    except ImportError:  # voice.py on its own
        return [speakable]
    return [speakable_code, speakable]


def _sayable(value: str) -> bool:
    """Whether a value, as read, is a number or a short word the voice can say in the sentence."""
    value = value.strip(" ,.;:")
    return bool(_NUMBER.fullmatch(value) or (len(value) <= 20 and _SHORT.fullmatch(value)))


def _template(reading: str, slots: list[str]) -> str | None:
    """A reading with its stand-ins back as `{expr}`, other braces doubled; None if a stand-in was split or lost."""
    found = _STAND_IN_ANY.findall(reading)
    if sorted(found) != [_STAND_IN.format(i) for i in range(len(slots))] or "$$" in reading:
        return None
    reading = reading.replace("{", "{{").replace("}", "}}")
    return _STAND_IN_ANY.sub(lambda m: slots[int(m.group(0)[5:])], reading)


def _is_template(said: str) -> bool:
    """Whether a live reading is words with `{…}` placeholders the engine fills: braces otherwise doubled, no $$."""
    rest = _PART.sub("", said)
    return any(m.group(1) for m in _PART.finditer(said)) and "{" not in rest and "}" not in rest and "$$" not in said


def _loose(s: str) -> str:
    """Words compared without case or most punctuation: a package's words may come from an older reading.

    Commas stay (a list's value has them; two values side by side are told apart by
    them), and `=` is "equals".
    """
    s = s.lower().replace("=", " equals ")
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    s = re.sub(r"(?<!\d),|,(?!\d)", " , ", s)
    return " ".join(re.sub(r"[^\w.,−-]+", " ", s).split())


def _values(reading: str, words: str) -> list[str] | None:
    """What `words` says where `reading` has its stand-ins, in stand-in order; None if the rest differs."""
    reading, words = _loose(reading), _loose(words)
    parts = _STAND_IN_ANY.split(reading)
    order = [int(s[5:]) for s in _STAND_IN_ANY.findall(reading)]
    m = re.fullmatch("(.+?)".join(re.escape(p) for p in parts), words, re.S)
    if not m:
        return None
    got = dict(zip(order, m.groups()))
    return [got[i] for i in sorted(got)]


def _matched(text: str, words: str | None) -> tuple[str | None, list[str] | None, list[str]]:
    """(template, values, slots): the template of the reader `words` matches, with the values it says; else the first reader's."""
    made = _stand_ins(text) if is_runtime(text) else None
    if made is None:
        return None, None, []
    src, slots = made
    first = None
    for read in _readers():
        reading = read(src)
        template = _template(reading, slots)
        if template is None:
            continue
        first = first or template
        values = _values(reading, words) if words else None
        if values is not None:
            return template, values, slots
    return first, None, slots


def slot_values(text: str, words: str | None) -> dict[str, bool]:
    """{placeholder: whether `words` says a number or short word for it}, for a runtime text a reader matches; else {}."""
    _, values, slots = _matched(text, words)
    out: dict[str, bool] = {}
    for slot, x in zip(slots, values or []):
        out[slot] = out.get(slot, True) and _sayable(x)
    return out


def live_words(text: str, words: str | None = None, *, known: dict[str, bool] | None = None) -> str | None:
    """A runtime text's words with its values kept as `{expr}`, for the game to fill in as the card does.

    `{expr}` stays exactly as written (format suffix kept) and `\\val{expr}` inside maths
    becomes `{expr}`; everything around them is read as the skill reads it (speech's
    `speakable_code`, or `speakable`: maths and markdown read out, maths letters in
    uppercase). Braces the reading says aloud are doubled, as the engine's `interpolate` wants.

    A placeholder must hold a number or a word or two, not a list or a sentence. `words`
    (what is said for the text with one set of values in) shows what it holds: the reader
    whose reading matches `words` outside the placeholders is used, and the values in
    `words` decide. A placeholder `words` does not show is trusted when `known`
    ({placeholder: sayable}, from the package's other texts) says so, or else when it is
    plainly numeric: a format (`:2`, `:%`), arithmetic, a numeric function, a model,
    data-count or hit read. A placeholder with a format is a number whatever `words` says.

    None for a text without runtime values, or one whose live reading cannot be made sensibly.
    """
    template, values, slots = _matched(text, words)
    if template is None:
        return None
    known = known or {}
    shown: dict[str, bool] = {}
    for slot, x in zip(slots, values or []):
        shown[slot] = shown.get(slot, True) and _sayable(x)
    for slot in dict.fromkeys(slots):
        expr = slot[1:-1]
        ok = shown.get(slot, known.get(slot))
        if _FORMATTED.search(expr):
            ok = True
        elif ok is None:
            ok = bool(_NUMERIC_EXPR.search(expr))
        if not ok:
            return None
    return template


def spoken_lookups(texts: Iterable[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """captions.json's `words` and `live` from texts.json's items ({key, text, words}).

    What a placeholder holds, where one text's words show it, decides for the package's
    other texts that use it.
    """
    texts = list(texts)
    known: dict[str, bool] = {}
    for t in texts:
        for slot, ok in slot_values(t["text"], t.get("words")).items():
            known[slot] = known.get(slot, True) and ok
    words, live = {}, {}
    for t in texts:
        if t.get("words"):
            words[t["key"]] = t["words"]
        said = live_words(t["text"], t.get("words"), known=known)
        if said:
            live[t["key"]] = said
    return dict(sorted(words.items())), dict(sorted(live.items()))


_WIKI_LINK = re.compile(r"\[\[(?:[\w-]+/)?([^|\]]+)(?:\|([^\]]+))?\]\]")


def link_words(text: str, *, prefix: str = "llm-") -> str:
    """Story links `[[story/x|label]]` replaced by their label, a bare `[[story/x]]` by "the lesson on x"."""
    def one(m: re.Match) -> str:
        if m.group(2):
            return m.group(2)
        return "the lesson on " + m.group(1).removeprefix(prefix).replace("-", " ")
    return _WIKI_LINK.sub(one, text)


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
                    "-ac", "1", "-c:a", "libopus", "-b:a", f"{kbps}k", "-application", "voip", str(tmp)],
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
    """Write captions.json, texts.json and manifest.json for the package's texts and clips; returns the manifest.

    captions.json (schema 2) is only the lookups the game downloads on every
    start: `words` (key -> what to say), `live` (key -> those words with the
    runtime values kept as `{…}`: `live_words`) and `clips` (key -> clip); the
    review list of every text (texts.json) stays in dex.

    An item whose clip is missing is left out of `clips` (a game on clips does
    not speak it; one speaking live still has its words). With `prune`,
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
    words, live = spoken_lookups(texts)
    captions = {"schema": SCHEMA, "voice": voice, "model": model, "words": words, "live": live,
                "clips": dict(sorted(clips.items()))}
    (pkg / CAPTIONS_FILE).write_text(json.dumps(captions, separators=(",", ":"), ensure_ascii=False) + "\n")
    (pkg / TEXTS_FILE).write_text(json.dumps(texts, indent=1, ensure_ascii=False) + "\n")
    man = {
        "name": name, "kind": KIND, "title": f"Spoken captions — {name}", "files": [CAPTIONS_FILE],
        "voice": voice, "model": model, "words": len(words), "live": len(live), "clips": len(clips),
        "unspoken": sum(1 for t in texts if not t["clip"]),
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
    if cap.get("schema") not in (1, SCHEMA):
        errors.append(f"captions.json schema {cap.get('schema')!r} is not 1 or {SCHEMA}")
    for field in ("words", "live"):
        if field not in cap:
            if cap.get("schema") == SCHEMA:
                errors.append(f"captions.json (schema {SCHEMA}) has no {field}")
            continue
        for key, said in (cap.get(field) or {}).items():
            if not re.fullmatch(r"[0-9a-f]{16}", key):
                errors.append(f"{field}: bad key {key!r}")
            elif not isinstance(said, str) or not said.strip():
                errors.append(f"{field}: {key} is empty")
            elif field == "live" and not _is_template(said):
                errors.append(f"live: {key} is not words with {{…}} placeholders: {said[:60]!r}")
            if len(errors) > 20:
                break
    if "words" not in cap:
        warnings.append(f"captions.json is schema {cap.get('schema')}: export_voice makes its words from {TEXTS_FILE}")
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


def game_captions(package: str | Path, *, clips: bool = True) -> dict:
    """The captions.json a game gets for this package (schema 2): {schema, voice, model, words, live[, clips]}.

    A package written before schema 2 has only `clips`: its `words` come from its
    texts.json, and `live` is made from each runtime text there (`spoken_lookups`),
    so it needs no remaking. Without `clips`, the lookup names no clip.
    """
    pkg = Path(package)
    cap = json.loads((pkg / CAPTIONS_FILE).read_text())
    if "words" in cap:
        words, live = cap.get("words") or {}, cap.get("live") or {}
    else:
        try:
            texts = json.loads((pkg / TEXTS_FILE).read_text())
        except (OSError, ValueError):
            texts = []
        words, live = spoken_lookups(texts)
    out = {"schema": SCHEMA, "voice": cap.get("voice", DEFAULT_VOICE), "model": cap.get("model", DEFAULT_MODEL),
           "words": words, "live": live}
    if clips:
        out["clips"] = dict(sorted((cap.get("clips") or {}).items()))
    return out


def export_voice(package: str | Path, game_root: str | Path, *, clips: bool = True) -> dict:
    """Install a voice package into `<game_root>/public/assets/voice/<name>/` and rebuild the index.

    captions.json goes in as schema 2 (`game_captions`): the words to say and the
    live words of every text, and with `clips` its clips. Clips are named by their
    content, so only new ones are copied and ones no longer used are removed;
    without `clips` none are installed, and any an earlier export left are removed
    with their emptied voice/ folder (a game that speaks live). captions.json and
    the manifest go in last. The installed manifest describes what is installed:
    its `captions` sha256 and `hashes` are those of the captions.json written here,
    which versions its URL.
    """
    pkg = Path(package)
    rep = validate_voice(pkg)
    if not rep["ok"]:
        raise ValueError("voice package does not validate: " + "; ".join(rep["errors"][:5]))
    man = json.loads((pkg / "manifest.json").read_text())
    cap = game_captions(pkg, clips=clips)
    dest = Path(game_root) / EXPORT_SUBDIR / KIND / man["name"]
    dest.mkdir(parents=True, exist_ok=True)
    wanted = set((cap.get("clips") or {}).values())
    clip_dir = dest / CLIPS_DIR
    if wanted:
        clip_dir.mkdir(exist_ok=True)
    for rel in wanted:
        if not (dest / rel).is_file():
            shutil.copy2(pkg / rel, dest / rel)
    if clip_dir.is_dir():
        for f in clip_dir.glob("*.ogg"):
            if f"{CLIPS_DIR}/{f.name}" not in wanted:
                f.unlink()
        if not wanted and not any(clip_dir.iterdir()):
            clip_dir.rmdir()
    body = (json.dumps(cap, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    tmp = dest / f".captions.{time.time_ns()}.tmp"
    tmp.write_bytes(body)
    tmp.replace(dest / CAPTIONS_FILE)
    digest = hashlib.sha256(body).hexdigest()
    entry = {k: v for k, v in man.items() if k != "created"}
    entry.update({"files": [CAPTIONS_FILE], "schema": SCHEMA, "captions": digest, "words": len(cap["words"]),
                  "live": len(cap["live"]), "clips": len(wanted)})
    if not clips:
        entry.pop("unspoken", None)  # texts without a clip: a game without clips has none
    entry["base"] = f"assets/{KIND}/{man['name']}/"
    # captions.json's version, for its URL; a clip's version is its file name (a content hash).
    entry["hashes"] = {CAPTIONS_FILE: digest[:12]}
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



def reuse_clips(items: list[dict], package: str | Path, project_dir: str | Path, *,
                voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL) -> int:
    """Copy into `package` the clips it lacks that another voice package of the project has.

    A clip's file name is a hash of its voice, model and words, so the same
    name is the same sound: copying it is exact, and saves the model's time.
    Returns how many were copied.
    """
    pkg, copied = Path(package), 0
    for it in items:
        if not it.get("words"):
            continue
        rel = clip_name(it["words"], voice, model)
        if (pkg / rel).is_file():
            continue
        for other in sorted(Path(project_dir).glob(f"*/{rel}")):
            if other.parent.parent.resolve() == pkg.resolve() or other.read_bytes()[:4] != b"OggS":
                continue
            (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(other, pkg / rel)
            copied += 1
            break
    return copied


def make_story_captions(story_package: str | Path, package: str | Path, *, game_root: str | Path | None = None,
                        project_dir: str | Path | None = None, voice: str = DEFAULT_VOICE,
                        model: str = DEFAULT_MODEL, workers: int = 8, max_seconds: float | None = None,
                        tags: Iterable[str] = ()) -> dict:
    """One story's captions, in a voice package of its own, named after the story; resumable.

    Plans only that story's texts, copies the clips other voice packages of the
    project already have (`reuse_clips`), makes the rest, and writes the
    package (pruned once nothing is left). Call again until `left` is 0.
    `tags` (`group:value`) are added to the manifest's type:voice and story:<name>.
    Returns {name, summary, synth, reused, manifest, skipped}.
    """
    story_pkg, pkg = Path(story_package), Path(package)
    pkg.mkdir(parents=True, exist_ok=True)
    name = json.loads((story_pkg / "manifest.json").read_text()).get("name") or story_pkg.name
    items = plan([story_pkg], game_root=game_root, spoken=read_spoken(pkg))
    reused = reuse_clips(items, pkg, project_dir or story_pkg.parent, voice=voice, model=model)
    synth = synthesize(items, pkg, voice=voice, model=model, workers=workers, max_seconds=max_seconds)
    man = write_package(pkg, name=name, items=items, voice=voice, model=model,
                        tags=("type:voice", f"story:{name}", *tags), prune=synth["left"] == 0)
    skipped = [{"key": it["key"], "text": it["text"], "uses": it["uses"]} for it in items if it["how"] == "skip"]
    return {"name": name, "summary": summary(items), "synth": synth, "reused": reused, "manifest": man,
            "skipped": skipped}


__all__ = [
    "KIND", "CAPTIONS_FILE", "SPOKEN_FILE", "CLIPS_DIR", "DEFAULT_VOICE", "DEFAULT_MODEL",
    "text_key", "is_runtime", "story_texts", "latex_words", "prose_letters", "speakable", "filled_texts",
    "live_words", "slot_values", "spoken_lookups", "story_packages",
    "plan", "summary", "clip_name", "synthesize_one", "synthesize", "read_spoken", "write_package",
    "validate_voice", "game_captions", "export_voice", "make_captions", "reuse_clips", "make_story_captions",
]
