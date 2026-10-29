"""Story text read out as words with brackets, limits and code read as a teacher would, and every runtime text filled from the game.

speakable(text) is a drop-in for voice.speakable (a make_captions run puts its words
in spoken.json where they differ); rest_filled_texts(package, game_root) fills the
texts voice.filled_texts leaves out (`more` paragraphs, other options' feedback,
an input's `wrong`) with the values the game itself renders.

Fixes over utils.voice.speakable / latex_words:
  * brackets carry meaning: `f(x)` "f of x", `n(n-1)` "n times the quantity n minus 1",
    `(1-p)^2` "the quantity 1 minus p, squared";
  * `\\sum_{i=1}^{n}` "the sum from i equals 1 to n of", `\\min_{c \\le a}` "the min over c is at most a of",
    `\\log_2 n` "log base 2 of n", `\\lVert w \\rVert` "the norm of w", `\\bmod` "mod", `\\binom`, `\\det`, `n!`;
  * the Unicode minus the game prints in filled values (−0.42) is "minus" inside math too;
  * code in plain text: `arr[i]` "arr at i", `dp[i][j]` "dp at i, j", `==`, `!=`, `<=`, `>=`, `=`, `+`, `*`,
    ` % `, ` - ` between operands, a leading `-1`, `snake_case` names, `ways(1)` "ways of 1", `= ?` "equals what?".
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .voice import COMMANDS, GREEK, _math_parts, is_runtime, story_texts, text_key

_POWER = {"2": " squared", "3": " cubed", "T": " transposed", "\\top": " transposed", "-1": " inverse",
          "*": " star", "\\ast": " star", "\\star": " star", "\\prime": " prime", "'": " prime"}
_EXTRA = {"bmod": " mod ", "mod": " mod ", "pmod": " mod ", "lVert": " the norm of ", "rVert": " ", "Vert": " ",
          "det": " the determinant of ", "gg": " is much greater than ", "ll": " is much less than ",
          "ell": " ell ", "cos": " cos ", "sin": " sine ", "tan": " tan ", "mathcal": ""}
_FUNC_LETTERS = set("fghdkjcwtsDFGHJKLMNPSRITCEVWQ")  # a letter before ( that names a function
_LIMIT_OPS = {"sum": "the sum", "prod": "the product", "min": "the min", "max": "the max",
              "argmin": "the arg min", "argmax": "the arg max", "lim": "the limit"}


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
    if src[i] in "-−" and i + 1 < len(src) and src[i + 1].isalnum():  # ^-1
        return src[i:i + 2], i + 2
    return src[i], i + 1


def _paren_end(src: str, i: int) -> int:
    """Index just past the ) matching the ( at src[i] (or the end)."""
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(src)


def _power_words(a: str) -> str:
    a = a.strip()
    return _POWER.get(a, f" to the power {latex_words(a)} ")


def _has_op(words: str) -> bool:
    return bool(re.search(r"\b(plus|minus|times|over|equals)\b", words)) or "," in words


def latex_words(src: str) -> str:
    """LaTeX (the inside of $$…$$) read out as words, roughly as a teacher would say it."""
    out: list[str] = []
    last = ""  # what came before: "name" (a letter or symbol), "func" (names a function), "num", "close", "sub", ""
    i = 0
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", src[i:])
            name = m.group(1)
            i += m.end()
            kind = "name"
            if name in ("frac", "dfrac", "tfrac"):
                a, i = _group(src, i)
                b, i = _group(src, i)
                wa, wb = latex_words(a), latex_words(b)
                out.append(f" {wa}, all over {wb} " if _has_op(wa) and not re.fullmatch(r"[\w.]+", wa)
                           else f" {wa} over {wb} ")
                kind = "close"
            elif name == "binom":
                a, i = _group(src, i)
                b, i = _group(src, i)
                out.append(f" {latex_words(a)} choose {latex_words(b)} ")
                kind = "close"
            elif name == "sqrt":
                a, i = _group(src, i)
                out.append(f" the square root of {latex_words(a)} ")
                kind = "close"
            elif name in ("hat", "bar", "overline", "tilde", "vec", "dot"):
                a, i = _group(src, i)
                word = {"overline": "bar", "vec": ""}.get(name, name)
                out.append(f" {latex_words(a)} {word} ")
            elif name in ("text", "mathrm", "mathbf", "mathit", "mathcal", "operatorname", "textbf", "boldsymbol",
                          "mathbb", "texttt"):
                a, i = _group(src, i)
                out.append(f" {latex_words(a) if name not in ('text', 'texttt') else a} ")
                kind = "func" if name in ("operatorname", "mathrm", "text") and len(a.strip()) > 1 else "name"
            elif name in _LIMIT_OPS and src[i:i + 1] in ("_", "^") or name in ("sum", "prod"):
                words = f" {_LIMIT_OPS[name]}"
                lo = hi = None
                for _ in range(2):
                    j = i
                    while j < len(src) and src[j] == " ":
                        j += 1
                    if src[j:j + 1] == "_" and lo is None:
                        lo, i = _group(src, j + 1)
                    elif src[j:j + 1] == "^" and hi is None:
                        hi, i = _group(src, j + 1)
                if lo is not None:
                    wl = latex_words(lo)
                    words += f" from {wl}" if hi is not None and " equals " in f" {wl} " else f" over {wl}"
                if hi is not None:
                    words += f" to {latex_words(hi)}"
                out.append(words + " of ")
                kind = ""
            elif name == "log" and src[i:i + 1] == "_":
                b, i = _group(src, i + 1)
                out.append(f" log base {latex_words(b)} of ")
                kind = ""
            elif name in ("left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "limits"):
                kind = last
            elif name in (",", ";", ":", "!", " "):
                out.append(" ")
                kind = last
            elif name in ("{", "}"):
                kind = last
            elif name == "|":
                out.append(" ")
                kind = last
            elif name in GREEK:
                out.append(f" {GREEK[name]} ")
                kind = "func" if name in ("sigma", "phi", "ell") else "name"
            elif name in _EXTRA:
                out.append(_EXTRA[name])
                kind = "" if name in ("lVert", "det", "bmod", "mod", "pmod", "gg", "ll") else "func"
            elif name in COMMANDS:
                out.append(COMMANDS[name])
                kind = "func" if name in ("log", "ln", "exp") else ""
            else:
                out.append(f" {name} ")
                kind = "func"
            last = kind
        elif ch in "^_":
            a, i = _group(src, i + 1)
            if ch == "^":
                out.append(_power_words(a))
                last = "close"
            else:
                out.append(f" {latex_words(a)} ")
                last = "sub"
        elif ch in "{}":
            i += 1
        elif ch == "=":
            out.append(" equals ")
            i += 1
            last = ""
        elif ch == "+":
            out.append(" plus ")
            i += 1
            last = ""
        elif ch in "-−":
            out.append(" minus ")
            i += 1
            last = ""
        elif ch == "<":
            out.append(" is less than ")
            i += 1
            last = ""
        elif ch == ">":
            out.append(" is greater than ")
            i += 1
            last = ""
        elif ch == "!":
            out.append(" factorial ")
            i += 1
            last = "close"
        elif ch == "|":
            i += 1
        elif ch == "&":
            out.append(" ")
            i += 1
        elif ch == "O" and re.match(r"O\s*(\\left)?\(", src[i:]):
            m = re.match(r"O\s*(\\left)?", src[i:])
            end = _paren_end(src, i + m.end())
            inner = src[i + m.end() + 1:end - 1]
            out.append(f" O of {latex_words(re.sub(r'\\right$', '', inner.strip()))} ")
            i = end
            last = "close"
        elif ch == "(":
            end = _paren_end(src, i)
            inner = src[i + 1:end - 1]
            inner = re.sub(r"\\right\s*$", "", inner.strip())
            w = latex_words(inner)
            j = end
            while j < len(src) and src[j] == " ":
                j += 1
            power = None
            if src[j:j + 1] == "^":
                power, j2 = _group(src, j + 1)
                end = j2
            if last in ("func", "sub"):
                lead = " of "
            elif last in ("num", "close") or (last == "name" and (_has_op(w) or power is not None)):
                lead = " times "
            elif last == "name":
                lead = " of "
            else:
                lead = " "
            if power is not None:
                out.append(f"{lead} the quantity {w},{_power_words(power)}, ")
            elif _has_op(w) and lead == " times ":
                out.append(f"{lead} the quantity {w}, ")
            else:
                out.append(f"{lead} {w} ")
            i = end
            last = "close"
        elif ch in ")[]":
            out.append(" ")
            i += 1
            last = "close" if ch != "[" else ""
        else:
            out.append(ch)
            if ch.isdigit() or ch == ".":
                last = "num"
            elif ch.isalpha():
                # a lone letter that usually names a function (f, g, P, …); else a variable
                prev = src[i - 1] if i else " "
                last = "func" if ch in _FUNC_LETTERS and not prev.isalpha() and not src[i + 1:i + 2].isalpha() \
                    else ("func" if prev.isalpha() else "name")
            elif ch != " ":
                last = ""
            i += 1
    words = " ".join("".join(out).split())
    return re.sub(r"\s+,", ",", re.sub(r",\s*,", ",", words)).strip(" ,")


def _code(t: str) -> str:
    """Code-like tokens in plain text, as words."""
    operand = r"(?:\d[\d.]*|[A-Za-z]|\)|\w+\[)"
    t = re.sub(rf"(?<=[\d\)\]])\s+-\s+(?=[\w(])|(?<=\b[A-Za-z])\s+-\s+(?={operand})", " minus ", t)
    t = re.sub(r"(^|(?<=[\s(,\[]))-(?=[\d.]+|[A-Za-z_])", "minus ", t)  # -1, -low -> minus …
    t = re.sub(r"(?<![\w\]])\[\](?!\w)", "an empty list", t)
    t = re.sub(r"(?<![\w}])\{\}(?!\w)", "an empty dict", t)
    t = re.sub(r"(?<=[\s,\"'])\{(?=[\s,.\"'])|^\{(?=\s)", "a curly brace", t)   # a lone { or [ as a value
    t = re.sub(r"(?<=[\s,\"'])\[(?=[\s,.\"'])|^\[(?=\s)", "a square bracket", t)
    t = re.sub(r"(?<=[\w\)])\[([^\[\]:]+):\]", r" from \1 on", t)      # left[3:] -> left from 3 on
    t = re.sub(r"(?<=[\w\)])\[:([^\[\]:]+)\]", r" up to \1", t)        # a[:k] -> a up to k
    t = re.sub(r"(?<=[\w\)])\[([^\[\]:]+):([^\[\]:]+)\]", r" from \1 to \2", t)
    t = re.sub(r"\]\s*\[", ", ", t)                                     # dp[i][j] -> dp[i, j]
    for _ in range(3):
        t = re.sub(r"(?<=[\w\)])\[([^\[\]]+)\]", r" at \1", t)       # arr[i] -> arr at i
    t = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", " ", t)          # remove_dups -> remove dups
    t = re.sub(r"\b([A-Za-z]\w*)\(\)", r"\1", t)                      # f() -> f
    for _ in range(3):
        t = re.sub(r"\b([A-Za-z]\w*)\((?!s\))([^()]+)\)", r"\1 of \2", t)  # ways(1) -> ways of 1
    for a, b in (("==", " equals "), ("!=", " is not equal to "), ("<=", " less than or equal to "),
                 (">=", " greater than or equal to "), ("+=", " plus equals "), ("-=", " minus equals "),
                 (" // ", " floor divided by ")):
        t = t.replace(a, b)
    t = re.sub(r"\s*=\s*\?", " equals what?", t)
    t = re.sub(r"(?<=\S)\s+=(?=\s|$)", " equals", t)
    t = re.sub(r"(?<=\S)\s+\+\s+(?=\S)", " plus ", t)
    t = re.sub(r"(?<=\S)\s+\*\s+(?=\S)", " times ", t)
    t = re.sub(r"(?<=\S)\s+%\s+(?=\S)", " mod ", t)
    t = re.sub(r"(?<![\w)\]])\+(?=\d)", "plus ", t)                   # +2 -> plus 2
    t = re.sub(r"(?<=\s)\+(?=[\s:,.;?]|$)|^\+(?=\s)", "plus", t)      # a lone + (the operator itself)
    t = re.sub(r"(?<=\s)\*(?=[\s:,.;?]|$)|^\*(?=\s)", "times", t)     # a lone *
    return t


# Class shapes are spoken; a button's icon is dropped (its name follows it); marks are named.
_GLYPHS = {"●": " sphere ", "■": " cube ", "▲": " cone ", "◆": " octahedron ", "✓": " tick ", "✗": " cross ",
           "▶": "", "⏩": "", "◀": "", "⏮": "", "⏭": "", "🎲": "", "🥄": "", "📊": "", "⭐": "", "👣": "",
           "✏️": "", "✏": "", "⚽": "", "️": ""}


def plain_words(text: str) -> str:
    """Markdown, symbols and code outside math, as words."""
    t = text.replace("{{", "{").replace("}}", "}")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`([^`]+)`", lambda m: _code(m.group(1)), t)
    t = _code(t)
    t = re.sub(r"(?<=\S)\s+>\s+(?=\S)", " is greater than ", re.sub(r"(?<=\S)\s+<\s+(?=\S)", " is less than ", t))
    for a, b in (("→", " to "), ("←", " from "), ("↑", " up "), ("↓", " down "), ("×", " times "),
                 ("≈", " about "), ("≤", " at most "), ("≥", " at least "), ("≠", " is not "), ("−", "minus "),
                 ("…", "..."), ("·", ", "), ("%", " percent"), ("&", " and "), ("#", " number ")):
        t = t.replace(a, b)
    for a, b in _GLYPHS.items():
        t = t.replace(a, b)
    return t


def speakable(text: str) -> str:
    """A story text as words to speak: markdown and code read out, $$…$$ math read out."""
    words = " ".join((f" {latex_words(p)} " if math else plain_words(p)) for math, p in _math_parts(text))
    words = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(words.split()))
    words = re.sub(r",([,.;:!?])", r"\1", words)
    words = re.sub(r"\s+'s\b", "'s", words)
    return words.strip().lstrip("-").strip()


# ---------------------------------------------------------------- every runtime text, from the game

# storysim.mjs (game-story) renders a beat's line, question and picked feedback; these
# patches make the same runner, at the same moment, also render every `more` paragraph
# (before any answer, as the player opens ?), every option's feedback and an input's `wrong`.
_SIM_PATCHES = [
    ("    const extra = { shown };\n",
     "    const extra = { shown };\n"
     "    shown.more = [].concat(beat.more || []).map((p) => { try { return runner.textOf(p); } catch { return null; } });\n"
     "    if (s.input) { try { shown.wrong = runner.textOf(s.input.wrong || ''); } catch {} }\n"),
    ("      shown.feedback = runner.textOf(opts[k].feedback);\n",
     "      shown.feedback = runner.textOf(opts[k].feedback);\n"
     "      shown.feedbacks = opts.map((o) => { try { return runner.textOf(o.feedback); } catch { return null; } });\n"),
]


def rest_filled_texts(package: str | Path, game_root: str | Path, *, timeout: float = 300) -> dict[str, str]:
    """{key: text as the game shows it} for a story's `more`, every option's feedback and `wrong`, on every played path."""
    sim = Path(__file__).with_name("storysim.mjs").read_text()
    for a, b in _SIM_PATCHES:
        if sim.count(a) != 1:
            raise RuntimeError(f"storysim.mjs changed: patch anchor not found once: {a.strip()[:60]!r}")
        sim = sim.replace(a, b)
    pkg = Path(package)
    st = json.loads((pkg / "story.json").read_text())
    by_beat = {str(b.get("id")): b for b in st.get("beats", [])}
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "storysim_more.mjs"
        script.write_text(sim)
        out = subprocess.run(["node", str(script), str(pkg / "story.json"), str(Path(game_root).expanduser().resolve()),
                              "--explore"], capture_output=True, text=True, check=True, timeout=timeout).stdout
    found: dict[str, str] = {}
    for run in json.loads(out)["runs"]:
        for snap in run["trace"]:
            beat = by_beat.get(str(snap.get("beat") or snap.get("id")))
            shown = snap.get("shown") or {}
            if not beat:
                continue
            raw = {e["field"]: e["text"] for e in story_texts({"beats": [beat]})}
            pairs = [(f"more.{i}", t) for i, t in enumerate(shown.get("more") or [])]
            pairs += [(f"feedback.{i}", t) for i, t in enumerate(shown.get("feedbacks") or [])]
            pairs += [("wrong", shown.get("wrong"))]
            for field, text in pairs:
                r = raw.get(field)
                if r and text and is_runtime(r) and "–" not in text and "{" not in text.replace("{{", ""):
                    found.setdefault(text_key(r), text)
    return found
