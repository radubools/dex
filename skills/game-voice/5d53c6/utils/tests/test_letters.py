"""Checks for game-voice's rule that a maths symbol's letter is spoken in uppercase, and only in the words."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from utils import speech  # noqa: E402
from utils import voice as v  # noqa: E402

# Both readers: voice.speakable (the plan's) and speech.speakable (the better one, into spoken.json).
READERS = [v.speakable, speech.speakable, speech.speakable_code]


def letters(words: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z0-9'])[A-Za-z](?![A-Za-z0-9'])", words)


def check(read, text: str, want: str) -> None:
    """The whole reading from speech's readers; from voice's plainer one, its single letters."""
    got = read(text)
    if read is v.speakable:
        assert letters(got) == letters(want), got
    else:
        assert got == want

LATEX = [
    ("$$\\hat y = w x + b$$", "Y hat equals W X plus B"),
    ("$$x_i$$", "X I"),
    ("$$y = kx$$", "Y equals kx"),                   # a run of letters is a name, not spelt
    ("$$\\text{loss} = (y - \\hat y)^2$$", None),    # \\text stays a word
    ("$$\\sigma(z)$$", None),                        # greek is a word already
]

PROSE = [
    ("Slide k to 4.", "Slide K to 4."),
    ("Now x is 3 and w is 0.5.", "Now X is 3 and W is 0.5."),
    ("Each x_i is a point.", "Each X I is a point."),
    ("Try a = 2, then b.", "Try A equals 2, then B."),
    ("the window is b b a.", "the window is B B A."),
    ("Leave or take a, then b.", "Leave or take A, then B."),
    ("`a` holds the answer.", "A holds the answer."),
    ("a[i] is the item.", "A at I is the item."),
]

UNCHANGED = [
    "I think a model is a function.",
    "Take a step, and I will take a look.",
    "Total is a length and k a count of planks.",   # only k is a variable
    "He's a cat, isn't he? It can't be.",
    "Use e.g. node.p or a.next here.",
    "the bag(s) of samples",
]


@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("text,want", LATEX)
def test_latex_letters_are_uppercase(read, text, want):
    got = read(text)
    if want is not None:
        check(read, text, want)
    if "\\text" in text:
        assert "loss" in got and "Y hat" in got
    if "\\sigma" in text:
        assert letters(got) == ["Z"] and "sigma" in got


@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("text,want", PROSE)
def test_prose_variables_are_uppercase(read, text, want):
    check(read, text, want)


@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("text", UNCHANGED)
def test_the_words_a_and_i_stay_words(read, text):
    got = read(text)
    words = got.split()
    assert "a" in words or "a" not in text.split()
    assert "I" in words or "I" not in text.split()
    if "k a count" in text:
        assert "K a count" in got


def test_runtime_placeholders_are_left_alone():
    assert v.prose_letters("Now p = {bt2.p:3} and k = {$code.var.k}.") == "Now P = {bt2.p:3} and K = {$code.var.k}."


def test_a_val_card_speaks_its_letters_in_uppercase_with_the_games_numbers():
    # The game fills \val{…} in as the card shows; the words are made from that text.
    shown = "Depth 2: $$x \\le −1.40$$, then $$z \\le −1.25$$."
    assert speech.speakable(shown) == "Depth 2: X is at most minus 1.40, then Z is at most minus 1.25."
    assert v.speakable("Fit $$b = 0.42$$ and $$\\hat y = 0.9$$.") == "Fit B equals 0.42 and Y hat equals 0.9."


def test_key_and_text_stay_as_the_story_writes_them(tmp_path, monkeypatch):
    plain = "Fit $$\\hat y = w x + b$$ with k at 3."
    runtime = "Fit $$b = \\val{fit.b:2}$$."
    story = {"schema": 2, "beats": [{"id": "a", "say": plain, "more": [runtime]}]}
    pkg = tmp_path / "story-demo"
    pkg.mkdir()
    (pkg / "story.json").write_text(json.dumps(story))
    (pkg / "manifest.json").write_text(json.dumps({"name": "demo", "kind": "story", "files": ["story.json"]}))
    monkeypatch.setattr(v, "filled_texts", lambda package, game_root: {v.text_key(runtime): "Fit $$b = 0.42$$."})
    items = {it["text"]: it for it in v.plan([pkg], game_root=tmp_path)}
    assert set(items) == {plain, runtime}                        # the display texts, untouched
    assert items[plain]["key"] == v.text_key(plain)              # keyed by the text as written
    assert items[runtime]["key"] == v.text_key(runtime)
    assert items[plain]["words"] == "Fit Y hat equals W X plus B with K at 3."
    assert items[runtime]["words"] == "Fit B equals 0.42."
    assert json.loads((pkg / "story.json").read_text()) == story  # the story is not written
