"""Checks for game-voice: caption keys (the engine's), runtime texts, words, the plan and the words-only package."""

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


def test_touch_line_is_spoken_too():
    story = {"schema": 2, "beats": [
        {"id": "a", "say": "Drag a box around the builders.", "sayTouch": "Tap a builder, then tap the others."},
        {"id": "b", "say": "Click the bunker.", "sayTouch": ""}]}
    got = [(e["beat"], e["field"], e["text"]) for e in v.story_texts(story)]
    assert got == [("a", "say", "Drag a box around the builders."),
                   ("a", "sayTouch", "Tap a builder, then tap the others."),
                   ("b", "say", "Click the bunker.")]


def test_words():
    assert v.speakable("Yes, **4** is smaller.") == "Yes, 4 is smaller."
    assert v.speakable("Swap neighbours: $$O(n^2)$$ steps.") == "Swap neighbours: O of N squared steps."
    assert v.speakable("$$\\frac{a + b}{2}$$") == "A plus B, all over 2"
    assert v.speakable("$$\\hat y = 0.5 x$$") == "Y hat equals 0.5 X"
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


def test_words_only_package(tmp_path):
    items = [{"key": v.text_key("Hello there."), "text": "Hello there.", "words": "Hello there.", "how": "plain",
              "uses": [{"story": "demo", "beat": "a", "field": "say"}]},
             {"key": v.text_key("{x}"), "text": "{x}", "words": None, "how": "skip", "uses": []}]
    pkg = tmp_path / "voice-demo"
    man = v.write_package(pkg, name="demo", items=items)
    assert man["words"] == 1 and man["unspoken"] == 1
    assert not {"clips", "voice", "model"} & set(man)
    rep = v.validate_voice(pkg)
    assert rep["ok"], rep
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    assert cap == {"schema": 2, "words": {items[0]["key"]: "Hello there."}, "live": {}}
    texts = json.loads((pkg / v.TEXTS_FILE).read_text())
    assert [t["key"] for t in texts] == [it["key"] for it in items] and not any("clip" in t for t in texts)
    assert sorted(p.name for p in pkg.iterdir()) == ["captions.json", "manifest.json", "texts.json"]


def test_no_synthesis_left():
    for gone in ("clip_name", "synthesize", "synthesize_one", "reuse_clips", "DEFAULT_VOICE", "DEFAULT_MODEL"):
        assert not hasattr(v, gone), gone
    src = Path(v.__file__).read_text()
    assert "ollama" not in src and "ffmpeg" not in src and "subprocess" not in src


def _story(tmp_path):
    story = tmp_path / "story-demo"
    story.mkdir()
    (story / "story.json").write_text(json.dumps({"schema": 2, "beats": [
        {"id": "a", "say": "Hello there."}, {"id": "b", "say": "Now i is {$code.var.i}."}]}))
    (story / "manifest.json").write_text(json.dumps({"name": "demo", "kind": "story", "files": ["story.json"]}))
    return story


def test_a_story_gets_its_own_words_package(tmp_path):
    story = _story(tmp_path)
    pkg = tmp_path / "voice-demo"
    out = v.make_story_captions(story, pkg)
    assert out["name"] == "demo" and out["summary"] == {"plain": 1, "skip": 1, "total": 2}
    runtime = v.text_key("Now i is {$code.var.i}.")
    assert [m["key"] for m in out["missing"]] == [runtime] == [s["key"] for s in out["skipped"]]
    man = json.loads((pkg / "manifest.json").read_text())
    assert man["name"] == "demo" and man["kind"] == "voice" and "story:demo" in man["tags"]
    assert man["words"] == 1 and man["unspoken"] == 1
    assert v.validate_voice(pkg)["ok"]
    # Done once the skipped text's words are inferred into spoken.json.
    (pkg / v.SPOKEN_FILE).write_text(json.dumps({runtime: "Now I is 2."}))
    with pytest.warns(UserWarning, match="max_seconds"):  # the clip-making call still works
        out = v.make_story_captions(story, pkg, max_seconds=480)
    assert out["missing"] == [] and v.missing_words(story, pkg) == []
    cap = json.loads((pkg / v.CAPTIONS_FILE).read_text())
    assert cap["words"][runtime] == "Now I is 2." and cap["live"] == {runtime: "Now I is {$code.var.i}."}
    assert v.validate_voice(pkg)["ok"]


def test_remaking_an_old_package_drops_its_clips(tmp_path):
    story = _story(tmp_path)
    pkg = tmp_path / "voice-demo"
    (pkg / v.CLIPS_DIR).mkdir(parents=True)
    (pkg / v.CLIPS_DIR / "0123456789abcdef.ogg").write_bytes(b"OggS old")
    v.make_story_captions(story, pkg)
    assert not (pkg / v.CLIPS_DIR).exists() and v.validate_voice(pkg)["ok"]


def test_missing_words_without_a_package(tmp_path):
    story = _story(tmp_path)
    assert len(v.missing_words(story, tmp_path / "nothing")) == 2
    assert len(v.missing_words(json.loads((story / "story.json").read_text()), tmp_path / "nothing")) == 2


def test_link_words():
    t = "See [[story/llm-linear-regions|linear regions]] and [[story/llm-approximation-history]]."
    assert v.link_words(t) == "See linear regions and the lesson on approximation history."
    assert v.link_words("[[story/knn]]", prefix="") == "the lesson on knn"
    assert v.link_words("no links here") == "no links here"
