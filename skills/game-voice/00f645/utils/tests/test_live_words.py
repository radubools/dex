"""Schema 2 captions: the words for every text, live words for runtime texts, and export with or without clips."""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

import utils  # noqa: E402
from utils import voice as v  # noqa: E402


@pytest.fixture
def catalog(monkeypatch):
    """game-mesh's catalog (not part of this skill): an index of the installed manifests."""
    def rebuild_index(game_root):
        root = Path(game_root) / v.EXPORT_SUBDIR
        assets = {}
        for m in sorted(root.glob("*/*/manifest.json")):
            e = json.loads(m.read_text())
            assets[f"{e['kind']}/{e['name']}"] = e
        (root / "index.json").write_text(json.dumps({"version": 1, "assets": assets}))
    stub = types.SimpleNamespace(rebuild_index=rebuild_index)
    monkeypatch.setitem(sys.modules, "utils.catalog", stub)
    monkeypatch.setattr(utils, "catalog", stub, raising=False)
    return stub


# ---------------------------------------------------------------- live readings

def test_a_value_outside_maths_stays_as_written():
    text = "Loss {$model.loss.mse:2}, **down** from {prev:%}."
    assert v.live_words(text) == "Loss {$model.loss.mse:2}, down from {prev:%}."


def test_a_val_inside_maths_becomes_a_slot_and_the_maths_is_read_out():
    assert v.live_words("Fit $$b = \\val{fit.b:2}$$ now.") == "Fit B equals {fit.b:2} now."
    assert v.live_words("$$\\frac{\\val{$model.param.w:2}}{2} \\le x_i$$") == "{$model.param.w:2} over 2 is at most X I"
    assert v.live_words("So $$\\hat y = \\val{$model.param.w:3} x + \\val{$model.param.b:3}$$.") \
        == "So Y hat equals {$model.param.w:3} X plus {$model.param.b:3}."


def test_the_words_around_a_value_are_read_as_around_a_number():
    # Words checked against a play-through's: the reader that matches them is used.
    assert v.live_words("Now i is {$code.var.i}, j is {$code.var.j}.", "Now I is 3, J is 4.") \
        == "Now I is {$code.var.i}, J is {$code.var.j}."
    assert v.live_words("It tips {p.tip}: the pulls add to {p.moment}.", "It tips left: the pulls add to minus 5.") \
        == "It tips {p.tip}: the pulls add to {p.moment}."
    # A package's words from an older reading (lowercase letters) still show what the values are.
    assert v.live_words("a is on {$code.var.a.val}.", "a is on 1.") == "A is on {$code.var.a.val}."


def test_braces_the_reading_says_stay_braces_for_the_engine():
    said = v.live_words("At best {{A, B}} against {{C}}, score {r.score:2}.")
    assert said == "At best {{A, B}} against {{C}}, score {r.score:2}."
    assert v._is_template(said)


def test_a_text_without_runtime_values_has_no_live_words():
    assert v.live_words("Six numbers in a jumble.") is None
    assert v.live_words("Swap: $$\\frac{a}{b}$$ and {{braces}}.") is None


def test_a_value_that_is_not_a_number_or_short_word_gets_no_live_words():
    # A list, a sentence, an error: the text is spoken with its fixed words instead.
    assert v.live_words("The stack holds {$code.var.result}.", "The stack holds 1, 2, 3.") is None
    assert v.live_words("Ward down to 2: {ok ? 'the trio stands alone' : 'not yet'}.",
                        "Ward down to 2: the trio stands alone.") is None
    assert v.live_words("It stops with {$code.error}.", "It stops with ZeroDivisionError: division by zero.") is None
    # No words to show what it holds, and nothing says it is a number.
    assert v.live_words("Note: {msg}.") is None
    # A number with a format is a number, whatever the words say.
    assert v.live_words("It keeps {lim.last:4}.", "It keeps 1 point 0 0 0 0.") == "It keeps {lim.last:4}."


def test_what_one_text_shows_decides_for_the_packages_other_texts():
    texts = [
        {"key": "1" * 16, "text": "result is {$code.var.result}.", "words": "result is 1, 2, 3."},
        {"key": "2" * 16, "text": "Still {$code.var.result}: line 4.", "words": "It is unchanged at line 4."},
        {"key": "3" * 16, "text": "i is {$code.var.i}.", "words": "I is 2."},
        {"key": "4" * 16, "text": "Then {$code.var.i} again.", "words": "Then two again."},
        {"key": "5" * 16, "text": "Plain.", "words": "Plain."},
        {"key": "6" * 16, "text": "Skipped {$code.var.i}.", "words": None},
    ]
    # Text 1 shows the value is a list; text 2's words were rephrased, so they show nothing.
    words, live = v.spoken_lookups(texts)
    assert list(words) == ["1" * 16, "2" * 16, "3" * 16, "4" * 16, "5" * 16]
    assert live == {"3" * 16: "I is {$code.var.i}.", "4" * 16: "Then {$code.var.i} again.",
                    "6" * 16: "Skipped {$code.var.i}."}


# ---------------------------------------------------------------- the package

def _items():
    use = [{"story": "demo", "beat": "a", "field": "say"}]
    return [
        {"key": v.text_key("Hello there."), "text": "Hello there.", "words": "Hello there.", "how": "plain", "uses": use},
        {"key": v.text_key("Loss is {$model.loss.mse:2}."), "text": "Loss is {$model.loss.mse:2}.",
         "words": "Loss is 0.42.", "how": "filled", "uses": use},
        {"key": v.text_key("{x}"), "text": "{x}", "words": None, "how": "skip", "uses": use},
    ]


def test_write_package_writes_schema_2(tmp_path):
    pkg = tmp_path / "voice-demo"
    items = _items()
    rel = v.clip_name("Hello there.")
    (pkg / rel).parent.mkdir(parents=True)
    (pkg / rel).write_bytes(b"OggS fake clip")
    man = v.write_package(pkg, name="demo", items=items)
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    assert cap["schema"] == 2 and cap["voice"] == v.DEFAULT_VOICE and cap["model"] == v.DEFAULT_MODEL
    assert cap["words"] == {items[0]["key"]: "Hello there.", items[1]["key"]: "Loss is 0.42."}
    assert cap["live"] == {items[1]["key"]: "Loss is {$model.loss.mse:2}."}
    assert cap["clips"] == {items[0]["key"]: rel}
    assert man["words"] == 2 and man["live"] == 1 and man["clips"] == 1 and man["unspoken"] == 2
    assert man["captions"] == hashlib.sha256((pkg / v.CAPTIONS_FILE).read_bytes()).hexdigest()
    rep = v.validate_voice(pkg)
    assert rep["ok"], rep


def test_validate_refuses_a_live_reading_the_engine_cannot_fill(tmp_path):
    pkg = tmp_path / "voice-demo"
    pkg.mkdir()
    v.write_package(pkg, name="demo", items=_items())
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    cap["live"][_items()[1]["key"]] = "Loss is {x"
    (pkg / v.CAPTIONS_FILE).write_text(json.dumps(cap))
    man = json.loads((pkg / "manifest.json").read_text())
    man["captions"] = hashlib.sha256((pkg / v.CAPTIONS_FILE).read_bytes()).hexdigest()
    (pkg / "manifest.json").write_text(json.dumps(man))
    rep = v.validate_voice(pkg)
    assert not rep["ok"] and any(e.startswith("live:") for e in rep["errors"])


def _schema_1_package(tmp_path) -> Path:
    """A package as written before schema 2: captions.json with clips only."""
    pkg = tmp_path / "voice-demo"
    items = _items()
    rel = v.clip_name("Hello there.")
    (pkg / rel).parent.mkdir(parents=True)
    (pkg / rel).write_bytes(b"OggS fake clip")
    texts = [{"key": it["key"], "text": it["text"], "words": it["words"], "how": it["how"],
              "clip": rel if it is items[0] else None, "uses": it["uses"]} for it in items]
    (pkg / v.TEXTS_FILE).write_text(json.dumps(texts))
    (pkg / v.CAPTIONS_FILE).write_text(json.dumps({"schema": 1, "voice": "Preston", "model": "phonon",
                                                   "clips": {items[0]["key"]: rel}}, separators=(",", ":")) + "\n")
    (pkg / "manifest.json").write_text(json.dumps({
        "name": "demo", "kind": "voice", "files": [v.CAPTIONS_FILE], "voice": "Preston", "model": "phonon",
        "clips": 1, "unspoken": 2, "captions": hashlib.sha256((pkg / v.CAPTIONS_FILE).read_bytes()).hexdigest(),
        "tags": ["type:voice"], "created": "2026-11-01T00:00:00"}))
    return pkg


def test_a_schema_1_package_exports_without_clips(tmp_path, catalog):
    pkg = _schema_1_package(tmp_path)
    before = {p.name: p.read_bytes() for p in pkg.iterdir() if p.is_file()}
    assert v.validate_voice(pkg)["ok"]
    game = tmp_path / "game"
    dest = game / v.EXPORT_SUBDIR / "voice" / "demo"
    # An earlier export left clips in the game.
    (dest / v.CLIPS_DIR).mkdir(parents=True)
    (dest / v.CLIPS_DIR / "0123456789abcdef.ogg").write_bytes(b"OggS old")
    entry = v.export_voice(pkg, game, clips=False)

    body = (dest / v.CAPTIONS_FILE).read_bytes()
    cap = json.loads(body)
    items = _items()
    assert cap == {"schema": 2, "voice": "Preston", "model": "phonon",
                   "words": {items[0]["key"]: "Hello there.", items[1]["key"]: "Loss is 0.42."},
                   "live": {items[1]["key"]: "Loss is {$model.loss.mse:2}."}}
    assert not (dest / v.CLIPS_DIR).exists()  # old .ogg removed, and the emptied voice/ with them
    assert not list(dest.rglob("*.ogg"))
    # The installed manifest describes what is installed, so captions.json's URL versions with it.
    assert entry["files"] == [v.CAPTIONS_FILE] and entry["clips"] == 0
    assert entry["captions"] == hashlib.sha256(body).hexdigest()
    assert entry["hashes"] == {v.CAPTIONS_FILE: hashlib.sha256(body).hexdigest()[:12]}
    assert json.loads((dest / "manifest.json").read_text()) == entry
    assert json.loads((game / v.EXPORT_SUBDIR / "index.json").read_text())["assets"]["voice/demo"] == entry
    # The package itself is not remade.
    assert {p.name: p.read_bytes() for p in pkg.iterdir() if p.is_file()} == before


def test_export_with_clips_is_schema_2_too(tmp_path, catalog):
    pkg = _schema_1_package(tmp_path)
    game = tmp_path / "game"
    entry = v.export_voice(pkg, game)  # clips by default, as the puller has always called it
    dest = game / v.EXPORT_SUBDIR / "voice" / "demo"
    body = (dest / v.CAPTIONS_FILE).read_bytes()
    cap = json.loads(body)
    rel = v.clip_name("Hello there.")
    assert cap["schema"] == 2 and cap["clips"] == {_items()[0]["key"]: rel} and len(cap["words"]) == 2
    assert (dest / rel).read_bytes() == b"OggS fake clip"
    assert entry["clips"] == 1 and entry["hashes"] == {v.CAPTIONS_FILE: hashlib.sha256(body).hexdigest()[:12]}
    # Switching the game to live speech takes the clips back out; its captions.json gets a new version.
    again = v.export_voice(pkg, game, clips=False)
    assert not (dest / v.CLIPS_DIR).exists() and again["hashes"] != entry["hashes"]


def test_a_schema_2_package_exports_its_own_lookups(tmp_path, catalog):
    pkg = tmp_path / "voice-demo"
    pkg.mkdir()
    v.write_package(pkg, name="demo", items=_items())
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    v.export_voice(pkg, tmp_path / "game", clips=False)
    got = json.loads((tmp_path / "game" / v.EXPORT_SUBDIR / "voice" / "demo" / v.CAPTIONS_FILE).read_text())
    assert got == {k: cap[k] for k in ("schema", "voice", "model", "words", "live")}
    # With clips, what is installed is the package's own captions.json, byte for byte.
    entry = v.export_voice(pkg, tmp_path / "game2")
    assert (tmp_path / "game2" / v.EXPORT_SUBDIR / "voice" / "demo" / v.CAPTIONS_FILE).read_bytes() \
        == (pkg / v.CAPTIONS_FILE).read_bytes()
    assert entry["hashes"] == {v.CAPTIONS_FILE: json.loads((pkg / "manifest.json").read_text())["captions"][:12]}


EXPR = Path.home() / "games" / "engine" / "src" / "story" / "expr.js"


def test_the_engine_fills_live_words():
    import shutil
    import subprocess
    if not (EXPR.is_file() and shutil.which("node")):
        pytest.skip("needs node and the engine")
    said = [v.live_words("So $$\\hat y = \\val{$model.param.w:2} x$$, from {prev:%}; {{A}} and {n}.", "So Y hat equals 0.5 X, from 40 percent; {A} and 3."),
            v.live_words("Loss {loss:2} after {n} steps.", "Loss 0.42 after 7 steps.")]
    assert all(said), said
    js = (f"import {{ interpolate }} from '{EXPR}';"
          "const scope = { vars: { prev: 0.4, n: 3, loss: 0.123 }, read: (p) => (p === 'model.param.w' ? -0.5 : null) };"
          f"console.log(JSON.stringify({json.dumps(said)}.map((t) => interpolate(t, scope))))")
    out = json.loads(subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True,
                                    check=True).stdout)
    assert out == ["So Y hat equals −0.50 X, from 40%; {A} and 3.", "Loss 0.12 after 3 steps."]
