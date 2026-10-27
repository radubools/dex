"""Checks for game-voice: caption keys (the engine's), runtime texts, words, the plan, clips and the package."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from utils import voice as v  # noqa: E402

ENGINE = Path.home() / "games" / "engine" / "src" / "story" / "captions.js"
needs_tts = pytest.mark.skipif(not (shutil.which("ollama") and shutil.which("ffmpeg")), reason="needs ollama and ffmpeg")

STORY = {"schema": 2, "beats": [
    {"id": "a", "say": "Six numbers in a jumble.", "more": ["Swap neighbours: $$O(n^2)$$ steps.", "Now i is {$code.var.i}."],
     "wait": {"choice": {"prompt": "Which goes first?", "options": [
         {"label": "4", "correct": True, "feedback": "Yes, **4** is smaller."},
         {"label": "9", "feedback": "No: `arr[0] > arr[1]`."}]}}},
    {"id": "b", "say": "Type the total.", "wait": {"input": {"prompt": "Total?", "answer": "3", "right": "Right.", "wrong": "Not quite."}}},
]}


def test_keys_are_the_engines():
    texts = ["Six numbers in a jumble.", "naïve → $$\\frac{1}{2}$$", "{$code.depth} plates"]
    if not (ENGINE.is_file() and shutil.which("node")):
        pytest.skip("needs node and the engine")
    js = subprocess.run(["node", "--input-type=module", "-e",
                         f"import {{ textKey }} from '{ENGINE}'; console.log(JSON.stringify({json.dumps(texts)}.map(textKey)))"],
                        capture_output=True, text=True, check=True).stdout
    assert json.loads(js) == [v.text_key(t) for t in texts]
    assert v.text_key("a") == "af63dc4c8601ec8c"  # FNV-1a 64 of "a"


def test_runtime_texts():
    assert v.is_runtime("Now i is {$code.var.i}.")
    assert v.is_runtime("Fit $$b = \\val{fit.b:2}$$.")
    assert not v.is_runtime("Swap: $$\\frac{a}{b}$$ and {{braces}}.")


def test_every_spoken_field():
    fields = [(e["beat"], e["field"]) for e in v.story_texts(STORY)]
    assert fields == [("a", "say"), ("a", "prompt"), ("a", "feedback.0"), ("a", "feedback.1"), ("a", "more.0"), ("a", "more.1"),
                      ("b", "say"), ("b", "prompt"), ("b", "right"), ("b", "wrong")]


def test_words():
    assert v.speakable("Yes, **4** is smaller.") == "Yes, 4 is smaller."
    assert v.speakable("Swap neighbours: $$O(n^2)$$ steps.") == "Swap neighbours: O of n squared steps."
    assert v.speakable("$$\\frac{a + b}{2}$$") == "a plus b, all over 2"
    assert v.speakable("$$\\hat y = 0.5 x$$") == "y hat equals 0.5 x"
    assert "is greater than" in v.speakable("No: `arr[0] > arr[1]`.")


def test_plan_speaks_plain_texts_overrides_and_skips_the_rest(tmp_path):
    pkg = tmp_path / "story-demo"
    pkg.mkdir()
    (pkg / "story.json").write_text(json.dumps(STORY))
    (pkg / "manifest.json").write_text(json.dumps({"name": "demo", "kind": "story", "files": ["story.json"]}))
    assert v.story_packages(tmp_path) == [pkg]
    runtime = v.text_key("Now i is {$code.var.i}.")
    items = v.plan([pkg], spoken={v.text_key("Not quite."): "Not quite, try again."})
    how = {it["text"]: it["how"] for it in items}
    assert how["Now i is {$code.var.i}."] == "skip"
    assert how["Not quite."] == "override"
    assert v.summary(items) == {"plain": 8, "override": 1, "skip": 1, "total": 10}
    items = v.plan([pkg], spoken={runtime: "Now i is 2."})
    assert next(it for it in items if it["key"] == runtime)["words"] == "Now i is 2."


@needs_tts
def test_clips_package_and_export(tmp_path):
    items = [{"key": v.text_key("Hello there."), "text": "Hello there.", "words": "Hello there.", "how": "plain",
              "uses": [{"story": "demo", "beat": "a", "field": "say"}]},
             {"key": v.text_key("{x}"), "text": "{x}", "words": None, "how": "skip", "uses": []}]
    pkg = tmp_path / "voice-demo"
    out = v.synthesize(items, pkg, workers=2)
    assert out == {"made": 1, "had": 0, "failed": [], "left": 0}
    assert v.synthesize(items, pkg)["had"] == 1
    man = v.write_package(pkg, name="demo", items=items)
    assert man["clips"] == 1 and man["unspoken"] == 1
    rep = v.validate_voice(pkg)
    assert rep["ok"], rep
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    assert "texts" not in cap  # the review list stays out of what the game downloads
    assert [t["key"] for t in json.loads((pkg / v.TEXTS_FILE).read_text())] == [it["key"] for it in items]
    clip = cap["clips"][items[0]["key"]]
    assert (pkg / clip).read_bytes()[:4] == b"OggS"
    # Installed into a game: the clip, captions.json and the manifest, registered in the index.
    game = tmp_path / "game"
    (game / "public" / "assets").mkdir(parents=True)
    try:
        entry = v.export_voice(pkg, game)
    except ImportError:
        pytest.skip("needs game-mesh's catalog for the index")
    assert entry["base"] == "assets/voice/demo/"
    assert entry["hashes"] == {v.CAPTIONS_FILE: man["captions"][:12]}
    assert not (game / "public/assets/voice/demo" / v.TEXTS_FILE).exists()
    assert (game / "public/assets/voice/demo" / clip).is_file()
    index = json.loads((game / "public/assets/index.json").read_text())
    assert "voice/demo" in index["assets"]
