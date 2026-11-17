"""Spoken captions for a game's stories: the words to say for every story text, in one `voice` package.

A story's card shows a line (`say`), sometimes a question (a choice's or a
typed answer's `prompt`), paragraphs behind its ? (`more`) and, after an
answer, a green or red hint (an option's `feedback`, an input's `right` /
`wrong`). The game speaks each of them when it shows (the engine's
story/captions.js), live in the player's browser and in the voice the player
picks, from the words made here.

Words are keyed by `text_key` of the text *exactly as the story writes it*
(FNV-1a 64 of its UTF-8 bytes; the engine computes the same), so the stories
themselves never change: their end-to-end reports stay valid, and a story
task can rewrite its story without knowing about captions. A text that
changes simply has no words until the captions are made again.

What is spoken is not always the text as written:
  * markdown and LaTeX are read out as words (`speakable`, `latex_words`);
  * a maths symbol's letter is said in uppercase ("Y hat", "slide K"), so the speech names it;
  * a text that fills in values at runtime (`{…}` outside $$…$$, `\\val{…}`
    inside) is spoken with the values a play-through of the story in the game
    gives (`filled_texts`, from the game-story skill's simulator: the line,
    the question and the right answer's feedback of every beat on the main
    path), or with values the task infers from the beat's context, written to
    the package's `spoken.json` (`{key: words}`); one with neither is left
    unspoken and listed.

A runtime text also has `live` words, its values kept as `{…}` for the game to fill
in as the card does (`live_words`).

The package (kind "voice", named after the story, or the game for the older game-wide one):
    manifest.json   kind, name, files ["captions.json"], schema, words, live, unspoken, captions (sha256)
    captions.json   {schema 2, words: {key: words}, live: {key: words with {…}}}: all the game loads
    texts.json      [{key, text, words, how, uses}]: what is said for every text, for review
    spoken.json     optional: {key: words} written by the task (inferred numbers, better readings)

There are no recorded clips. A package made when there were (voice/*.ogg, `clips`)
fails `validate_voice` until `drop_clips` makes it words only.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import warnings
from pathlib import Path
from typing import Any, Iterable

KIND = "voice"
CAPTIONS_FILE = "captions.json"
SPOKEN_FILE = "spoken.json"
TEXTS_FILE = "texts.json"
CLIPS_DIR = "voice"  # where older packages kept their clips; drop_clips removes it
SCHEMA = 2
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
        m = re.match(r"\\([A-Za-z]+|.)", src[i:])
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
            if not m:
                out.append(ch)
                i += 1
                continue
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


# ---------------------------------------------------------------- the package

def read_spoken(package: str | Path) -> dict[str, str]:
    """The package's spoken.json ({key: words}), or {}."""
    p = Path(package) / SPOKEN_FILE
    return json.loads(p.read_text()) if p.is_file() else {}


def _clip_files(pkg: Path) -> list[Path]:
    """The recorded clips a package still carries (voice/*.ogg, from before words only)."""
    d = pkg / CLIPS_DIR
    return sorted(d.glob("*.ogg")) if d.is_dir() else []


def _remove_clips(pkg: Path) -> dict:
    """Delete voice/*.ogg and the emptied voice/ folder: {files, bytes}."""
    files = _clip_files(pkg)
    size = 0
    for f in files:
        size += f.stat().st_size
        f.unlink()
    d = pkg / CLIPS_DIR
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()
    return {"files": len(files), "bytes": size}


def _write_lookups(pkg: Path, *, name: str, texts: list[dict], words: dict[str, str], live: dict[str, str],
                   tags: Iterable[str], keep: dict | None = None) -> dict:
    """captions.json, texts.json and manifest.json of a words-only package; returns the manifest."""
    captions = {"schema": SCHEMA, "words": words, "live": live}
    (pkg / CAPTIONS_FILE).write_text(json.dumps(captions, separators=(",", ":"), ensure_ascii=False) + "\n")
    (pkg / TEXTS_FILE).write_text(json.dumps(texts, indent=1, ensure_ascii=False) + "\n")
    man = {k: v for k, v in (keep or {}).items() if k not in _OLD_MANIFEST}
    man.update({
        "name": name, "kind": KIND, "title": man.get("title") or f"Spoken captions — {name}", "files": [CAPTIONS_FILE],
        "schema": SCHEMA, "words": len(words), "live": len(live),
        "unspoken": sum(1 for t in texts if not t.get("words")),
        "captions": hashlib.sha256((pkg / CAPTIONS_FILE).read_bytes()).hexdigest(),
        "tags": sorted(set(tags)),
    })
    man.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%S"))
    (pkg / "manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
    return man


#: Manifest fields of a package with clips that a words-only package does not have.
_OLD_MANIFEST = ("voice", "model", "clips")


def write_package(package: str | Path, *, name: str, items: list[dict],
                  tags: Iterable[str] = ("type:voice",)) -> dict:
    """Write captions.json, texts.json and manifest.json for the package's texts; returns the manifest.

    captions.json (schema 2) is only the lookups the game downloads on every
    start: `words` (key -> what to say) and `live` (key -> those words with the
    runtime values kept as `{…}`: `live_words`); the review list of every text
    (texts.json) stays in dex. Clips an older package still carries are deleted:
    a package is words only.
    """
    pkg = Path(package)
    pkg.mkdir(parents=True, exist_ok=True)
    texts = [{"key": it["key"], "text": it["text"], "words": it.get("words"), "how": it["how"], "uses": it["uses"]}
             for it in items]
    _remove_clips(pkg)
    words, live = spoken_lookups(texts)
    return _write_lookups(pkg, name=name, texts=texts, words=words, live=live, tags=tags)


def validate_voice(package: str | Path) -> dict:
    """{ok, errors, warnings}: a words-only voice package the game can load.

    Clips in a package (captions.json `clips`, voice/*.ogg, texts.json `clip`)
    are an error: `drop_clips(package)` makes it words only.
    """
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
    try:
        texts = json.loads((pkg / TEXTS_FILE).read_text())
    except (OSError, ValueError):
        texts = []
    clips = [what for what, has in (
        ("captions.json has clips", "clips" in cap),
        (f"{len(_clip_files(pkg))} .ogg files in {CLIPS_DIR}/", bool(_clip_files(pkg))),
        ("texts.json names clips", any("clip" in t for t in texts)),
        ("manifest.json counts clips", "clips" in man),
    ) if has]
    if clips:
        errors.append(f"the package carries recorded clips ({', '.join(clips)}): the game speaks the words itself; "
                      "make it words only with voice.drop_clips(pkg)")
    if cap.get("schema") != SCHEMA:
        errors.append(f"captions.json schema {cap.get('schema')!r} is not {SCHEMA} (voice.drop_clips(pkg) rewrites it)")
    for field in ("words", "live"):
        if field not in cap:
            errors.append(f"captions.json has no {field}")
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
    if "texts" in cap:
        errors.append("captions.json carries the texts list: the game downloads it on every start (write_package again)")
    unspoken = sum(1 for t in texts if not t.get("words"))
    if unspoken:
        warnings.append(f"{unspoken} texts have no words (runtime values not inferred yet: spoken.json)")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def missing_words(story: str | Path | dict, package: str | Path) -> list[dict]:
    """The texts of a story (`story_texts`) the voice package has no `words` for: [{beat, field, text, key}].

    Empty means the story's captions are complete. `story` is its package
    directory or its story.json dict; a package with no captions.json misses
    every text.
    """
    st = story if isinstance(story, dict) else json.loads((Path(story) / "story.json").read_text())
    try:
        words = json.loads((Path(package) / CAPTIONS_FILE).read_text()).get("words") or {}
    except (OSError, ValueError):
        words = {}
    out = []
    for e in story_texts(st):
        key = text_key(e["text"])
        if not words.get(key):
            out.append({**e, "key": key})
    return out


def game_captions(package: str | Path) -> dict:
    """The captions.json a game gets for this package (schema 2): {schema, words, live}.

    A package written before schema 2 has no `words`: they come from its
    texts.json, and `live` is made from each runtime text there
    (`spoken_lookups`). Clips are never part of it.
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
    return {"schema": SCHEMA, "words": dict(sorted(words.items())), "live": dict(sorted(live.items()))}


def drop_clips(package: str | Path) -> dict:
    """Make a package with recorded clips (schema 1 or 2) words only, in place.

    `words` and `live` are what `game_captions` gives (a schema 1 package's from
    its texts.json); voice/*.ogg and the voice/ folder are deleted; captions.json,
    texts.json (no `clip`) and manifest.json (no voice, model or clips) are
    rewritten. Safe to run again: a words-only package is rewritten as it is.
    Returns {name, schema (before), files, bytes (the clips removed), words, live, ok}.
    """
    pkg = Path(package)
    man = json.loads((pkg / "manifest.json").read_text())
    schema = json.loads((pkg / CAPTIONS_FILE).read_text()).get("schema")
    cap = game_captions(pkg)
    try:
        texts = json.loads((pkg / TEXTS_FILE).read_text())
    except (OSError, ValueError):
        texts = []
    texts = [{k: v for k, v in t.items() if k != "clip"} for t in texts]
    removed = _remove_clips(pkg)
    name = man.get("name") or pkg.name
    _write_lookups(pkg, name=name, texts=texts, words=cap["words"], live=cap["live"],
                   tags=man.get("tags") or ("type:voice",), keep=man)
    return {"name": name, "schema": schema, **removed, "words": len(cap["words"]), "live": len(cap["live"]),
            "ok": validate_voice(pkg)["ok"]}


def export_voice(package: str | Path, game_root: str | Path, *, clips: bool = False) -> dict:
    """Install a voice package into `<game_root>/public/assets/voice/<name>/` and rebuild the index.

    captions.json goes in as schema 2 (`game_captions`): the words to say and the
    live words of every text. A package has no clips any more, so `clips=True`
    only warns; any clips an earlier export left in the game are removed with
    their emptied voice/ folder. captions.json and the manifest go in last. The
    installed manifest's `captions` sha256 and `hashes` are those of the
    captions.json written here, which versions its URL.
    """
    if clips:
        warnings.warn("game-voice packages carry no clips any more: export_voice(clips=True) installs the words only",
                      stacklevel=2)
    pkg = Path(package)
    rep = validate_voice(pkg)
    if not rep["ok"]:
        raise ValueError("voice package does not validate: " + "; ".join(rep["errors"][:5]))
    man = json.loads((pkg / "manifest.json").read_text())
    cap = game_captions(pkg)
    dest = Path(game_root) / EXPORT_SUBDIR / KIND / man["name"]
    dest.mkdir(parents=True, exist_ok=True)
    _remove_clips(dest)
    body = (json.dumps(cap, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    tmp = dest / f".captions.{time.time_ns()}.tmp"
    tmp.write_bytes(body)
    tmp.replace(dest / CAPTIONS_FILE)
    digest = hashlib.sha256(body).hexdigest()
    entry = {k: v for k, v in man.items() if k != "created"}
    entry.update({"files": [CAPTIONS_FILE], "schema": SCHEMA, "captions": digest, "words": len(cap["words"]),
                  "live": len(cap["live"])})
    entry["base"] = f"assets/{KIND}/{man['name']}/"
    # captions.json's version, for its URL.
    entry["hashes"] = {CAPTIONS_FILE: digest[:12]}
    entry["exported"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = dest / f".manifest.{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(dest / "manifest.json")
    from . import catalog  # game-mesh, enabled alongside this skill

    catalog.rebuild_index(game_root)
    return entry


def _legacy(kw: dict) -> None:
    """Arguments of the clip-making version (voice, model, workers, max_seconds): accepted and ignored."""
    if kw:
        warnings.warn(f"ignored (no clips are made any more): {', '.join(sorted(kw))}", stacklevel=3)


def make_captions(project_dir: str | Path, package: str | Path, *, name: str, game_root: str | Path | None = None,
                  **legacy: Any) -> dict:
    """The game-wide package: plan every story in the project and write its words.

    Returns {summary, manifest, skipped: [{key, text, uses}]}.
    """
    _legacy(legacy)
    pkg = Path(package)
    items = plan(story_packages(project_dir), game_root=game_root, spoken=read_spoken(pkg))
    man = write_package(pkg, name=name, items=items)
    skipped = [{"key": it["key"], "text": it["text"], "uses": it["uses"]} for it in items if it["how"] == "skip"]
    return {"summary": summary(items), "manifest": man, "skipped": skipped}


def make_story_captions(story_package: str | Path, package: str | Path, *, game_root: str | Path | None = None,
                        tags: Iterable[str] = (), **legacy: Any) -> dict:
    """One story's spoken words, in a voice package of its own, named after the story.

    Plans only that story's texts and writes the package. `tags` (`group:value`)
    are added to the manifest's type:voice and story:<name>. `skipped` lists the
    runtime texts nothing filled in: infer their words into spoken.json and call
    again. `missing` is every text of the story still without words
    (`missing_words`); the captions are done when it is empty and
    `validate_voice` is ok.
    Returns {name, summary, manifest, skipped, missing}.
    """
    _legacy({k: v for k, v in legacy.items() if k != "project_dir"})
    story_pkg, pkg = Path(story_package), Path(package)
    name = json.loads((story_pkg / "manifest.json").read_text()).get("name") or story_pkg.name
    items = plan([story_pkg], game_root=game_root, spoken=read_spoken(pkg))
    man = write_package(pkg, name=name, items=items, tags=("type:voice", f"story:{name}", *tags))
    skipped = [{"key": it["key"], "text": it["text"], "uses": it["uses"]} for it in items if it["how"] == "skip"]
    return {"name": name, "summary": summary(items), "manifest": man, "skipped": skipped,
            "missing": missing_words(story_pkg, pkg)}


__all__ = [
    "KIND", "CAPTIONS_FILE", "SPOKEN_FILE", "TEXTS_FILE",
    "text_key", "is_runtime", "story_texts", "latex_words", "prose_letters", "speakable", "filled_texts",
    "live_words", "slot_values", "spoken_lookups", "story_packages",
    "plan", "summary", "read_spoken", "write_package",
    "validate_voice", "missing_words", "game_captions", "drop_clips", "export_voice", "make_captions",
    "make_story_captions",
]
