"""Schema 2 captions: the words for every text, live words for runtime texts, words-only packages, drop_clips and export."""

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


WORDS = {_items()[0]["key"]: "Hello there.", _items()[1]["key"]: "Loss is 0.42."}
LIVE = {_items()[1]["key"]: "Loss is {$model.loss.mse:2}."}


def test_write_package_writes_words_only(tmp_path):
    pkg = tmp_path / "voice-demo"
    man = v.write_package(pkg, name="demo", items=_items())
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    assert cap == {"schema": 2, "words": WORDS, "live": LIVE}
    assert man["words"] == 2 and man["live"] == 1 and man["unspoken"] == 1 and "clips" not in man
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


CLIP = "voice/0123456789abcdef.ogg"


def _clips_package(tmp_path, schema: int) -> Path:
    """A package as written when there were clips: schema 1 (clips only) or schema 2 (words, live and clips)."""
    pkg = tmp_path / f"voice-demo-{schema}"
    items = _items()
    (pkg / v.CLIPS_DIR).mkdir(parents=True)
    (pkg / CLIP).write_bytes(b"OggS fake clip")
    (pkg / v.CLIPS_DIR / "fedcba9876543210.ogg").write_bytes(b"OggS unused")
    texts = [{"key": it["key"], "text": it["text"], "words": it["words"], "how": it["how"],
              "clip": CLIP if it is items[0] else None, "uses": it["uses"]} for it in items]
    (pkg / v.TEXTS_FILE).write_text(json.dumps(texts))
    cap = {"schema": schema, "voice": "Preston", "model": "phonon", "clips": {items[0]["key"]: CLIP}}
    if schema == 2:
        # Hand-fixed words in a schema 2 package are kept as they are, not read again from texts.json.
        cap = {**cap, "words": {**WORDS, items[0]["key"]: "Hello, there."}, "live": LIVE}
    (pkg / v.CAPTIONS_FILE).write_text(json.dumps(cap, separators=(",", ":")) + "\n")
    (pkg / v.SPOKEN_FILE).write_text("{}\n")
    (pkg / "manifest.json").write_text(json.dumps({
        "name": "demo", "kind": "voice", "title": "Spoken captions — demo", "files": [v.CAPTIONS_FILE],
        "voice": "Preston", "model": "phonon", "clips": 1, "unspoken": 2,
        "captions": hashlib.sha256((pkg / v.CAPTIONS_FILE).read_bytes()).hexdigest(),
        "tags": ["story:demo", "type:voice"], "created": "2026-11-01T00:00:00"}))
    return pkg


@pytest.mark.parametrize("schema", [1, 2])
def test_validate_treats_clips_as_an_error(tmp_path, schema):
    rep = v.validate_voice(_clips_package(tmp_path, schema))
    assert not rep["ok"]
    assert any("drop_clips" in e and "recorded clips" in e for e in rep["errors"]), rep


@pytest.mark.parametrize("schema", [1, 2])
def test_drop_clips_makes_a_package_words_only(tmp_path, schema):
    pkg = _clips_package(tmp_path, schema)
    expected = v.game_captions(pkg)
    out = v.drop_clips(pkg)
    assert out == {"name": "demo", "schema": schema, "files": 2, "bytes": len(b"OggS fake clip") + len(b"OggS unused"),
                   "words": 2, "live": 1, "ok": True}
    assert sorted(p.name for p in pkg.iterdir()) == ["captions.json", "manifest.json", "spoken.json", "texts.json"]
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    assert cap == expected == {"schema": 2, "words": cap["words"], "live": LIVE}
    assert cap["words"] == (WORDS if schema == 1 else {**WORDS, _items()[0]["key"]: "Hello, there."})
    texts = json.loads((pkg / v.TEXTS_FILE).read_text())
    assert [t["key"] for t in texts] == [it["key"] for it in _items()] and not any("clip" in t for t in texts)
    man = json.loads((pkg / "manifest.json").read_text())
    assert not {"voice", "model", "clips"} & set(man)
    assert man["tags"] == ["story:demo", "type:voice"] and man["created"] == "2026-11-01T00:00:00"
    assert man["title"] == "Spoken captions — demo" and man["words"] == 2 and man["live"] == 1 and man["unspoken"] == 1
    assert man["captions"] == hashlib.sha256((pkg / v.CAPTIONS_FILE).read_bytes()).hexdigest()
    assert v.validate_voice(pkg) == {"ok": True, "errors": [], "warnings": [
        "1 texts have no words (runtime values not inferred yet: spoken.json)"]}
    # Again: nothing left to remove, and the package is as it was.
    before = {p.name: p.read_bytes() for p in pkg.iterdir()}
    assert v.drop_clips(pkg)["files"] == 0
    assert {p.name: p.read_bytes() for p in pkg.iterdir()} == before


def test_export_refuses_a_package_with_clips(tmp_path, catalog):
    with pytest.raises(ValueError, match="drop_clips"):
        v.export_voice(_clips_package(tmp_path, 1), tmp_path / "game", clips=False)


def test_export_installs_the_words(tmp_path, catalog):
    pkg = tmp_path / "voice-demo"
    v.write_package(pkg, name="demo", items=_items())
    game = tmp_path / "game"
    dest = game / v.EXPORT_SUBDIR / "voice" / "demo"
    # An earlier export left clips in the game.
    (dest / v.CLIPS_DIR).mkdir(parents=True)
    (dest / v.CLIPS_DIR / "0123456789abcdef.ogg").write_bytes(b"OggS old")
    before = {p.name: p.read_bytes() for p in pkg.iterdir()}
    entry = v.export_voice(pkg, game, clips=False)  # as the puller calls it

    body = (dest / v.CAPTIONS_FILE).read_bytes()
    assert body == (pkg / v.CAPTIONS_FILE).read_bytes()  # the package's own lookups, byte for byte
    assert json.loads(body) == {"schema": 2, "words": WORDS, "live": LIVE}
    assert not (dest / v.CLIPS_DIR).exists() and not list(dest.rglob("*.ogg"))
    assert sorted(p.name for p in dest.iterdir()) == ["captions.json", "manifest.json"]
    # The installed manifest describes what is installed, so captions.json's URL versions with it.
    assert entry["files"] == [v.CAPTIONS_FILE] and "clips" not in entry
    assert entry["captions"] == hashlib.sha256(body).hexdigest()
    assert entry["hashes"] == {v.CAPTIONS_FILE: hashlib.sha256(body).hexdigest()[:12]}
    assert json.loads((dest / "manifest.json").read_text()) == entry
    assert json.loads((game / v.EXPORT_SUBDIR / "index.json").read_text())["assets"]["voice/demo"] == entry
    # The package itself is not changed.
    assert {p.name: p.read_bytes() for p in pkg.iterdir()} == before
    # Asking for clips installs the same words, with a warning.
    with pytest.warns(UserWarning, match="no clips"):
        again = v.export_voice(pkg, tmp_path / "game2", clips=True)
    assert again["hashes"] == entry["hashes"]
    assert v.export_voice(pkg, tmp_path / "game3")["hashes"] == entry["hashes"]


def test_the_puller_can_still_ask_for_no_clips():
    import inspect
    assert "clips" in inspect.signature(v.export_voice).parameters  # pull_dex_assets.py checks this


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
