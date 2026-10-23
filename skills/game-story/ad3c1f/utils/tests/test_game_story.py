"""Checks for game-story: the schema-1 validator, writing packages, exporting into a game."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from utils import story as S  # noqa: E402


def _story() -> dict:
    return {
        "schema": 1,
        "id": "knn-basics",
        "title": "k-nearest neighbours",
        "summary": "Classify a point by asking its neighbours.",
        "algorithm": "knn",
        "assets": ["environment/marker-sphere", "environment/query-star"],
        "beats": [
            {"id": "intro", "say": "Every point here has a colour: its class.",
             "camera": {"target": [0, 0], "distance": 8},
             "act": {"action": "data.scatter", "args": {"dataset": "blobs", "n": 30}},
             "wait": "next"},
            {"id": "query", "say": "A new point arrives. Which class is it?",
             "spawn": [{"id": "q", "asset": "environment/query-star", "at": [1, 1], "color": "highlight"},
                       {"id": "p1", "asset": "environment/marker-sphere", "at": [2, 1], "color": "class-0"}],
             "highlight": {"entity": "q"}, "sfx": "reveal",
             "wait": {"choice": {"prompt": "What should we look at first?", "options": [
                 {"label": "Its nearest points", "correct": True, "feedback": "Yes: neighbours share classes."},
                 {"label": "The farthest point", "correct": False, "feedback": "Far points say little about here."},
             ]}}},
            {"id": "vote", "say": "The neighbours vote.", "act": {"action": "knn.vote"},
             "remove": ["p1"], "music": {"intensity": 0.5}, "wait": {"time": 2}},
            {"id": "play", "say": "Your turn: place points and watch.",
             "act": {"action": "sandbox.open", "args": {"algorithm": "knn", "tools": ["place-query"]}},
             "wait": {"action": "knn.query"}},
        ],
    }


KNOWN = {"environment/marker-sphere", "environment/query-star", "sfx/reveal"}


def test_valid_story_passes() -> None:
    rep = S.validate_story(_story(), known=KNOWN)
    assert rep["ok"], rep["errors"]
    assert rep["stats"]["choices"] == 1 and rep["stats"]["beats"] == 4
    assert rep["warnings"] == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: s.pop("title"), "missing 'title'"),
    (lambda s: s["beats"][1].update(id="intro"), "duplicate beat id"),
    (lambda s: s["beats"][0].update(say="x" * 141), "141 chars"),
    (lambda s: s["beats"][1]["wait"]["choice"]["options"][0].update(correct=False), "no correct option"),
    (lambda s: s["beats"][1]["wait"]["choice"]["options"][1].pop("feedback"), "needs feedback"),
    (lambda s: s["beats"][1]["wait"]["choice"]["options"].pop(), "at least 2 options"),
    (lambda s: s["assets"].remove("environment/query-star"), "not in assets"),
    (lambda s: s["beats"][0].update(act={"action": "magic.go"}), "unknown action"),
    (lambda s: s["beats"][2].update(remove=["ghost"]), "never spawned"),
    (lambda s: s["beats"][1].update(highlight={"entity": "nope"}), "not in the scene"),
    (lambda s: s["beats"][0].update(camera={"target": [0]}), "camera needs target"),
    (lambda s: s["beats"][0].update(wait="later"), "wait is"),
    (lambda s: s["beats"][1]["spawn"][0].update(color="red"), "palette name"),
    (lambda s: s["beats"][1].update(wait="next"), "no choice beats"),
])
def test_invalid_stories_fail(mutate, needle) -> None:
    s = _story()
    mutate(s)
    rep = S.validate_story(s, known=KNOWN)
    assert not rep["ok"]
    assert any(needle in e for e in rep["errors"]), rep["errors"]


def test_unknown_assets_fail_only_when_checked() -> None:
    s = _story()
    assert S.validate_story(s)["ok"]  # no game_root, no known list: structure only
    rep = S.validate_story(s, known={"sfx/reveal"})
    assert not rep["ok"] and any("environment/query-star" in e for e in rep["errors"])
    rep = S.validate_story(s, known=KNOWN - {"sfx/reveal"})
    assert any("sfx/reveal" in e for e in rep["errors"])


def test_missing_sandbox_handoff_warns() -> None:
    s = _story()
    s["beats"][-1]["act"] = {"action": "knn.vote"}
    rep = S.validate_story(s, known=KNOWN)
    assert rep["ok"] and any("sandbox.open" in w for w in rep["warnings"])


def test_actions_catalogue_is_complete() -> None:
    acts = S.actions()
    for name in ("data.scatter", "data.label", "regression.fitStep", "regression.showResiduals",
                 "knn.query", "knn.vote", "kmeans.assign", "kmeans.update", "cluster.merge",
                 "tree.split", "forest.bag", "boost.addStump", "svm.margin", "svm.kernelLift",
                 "bayes.gaussian", "pca.project", "nn.forward", "nn.backward", "train.step",
                 "eval.split", "sandbox.open"):
        assert name in acts, name
    for name, a in acts.items():
        assert a["summary"] and a["algorithm"] and isinstance(a["args"], dict), name


def _game(tmp_path: Path) -> Path:
    """A temp game whose index already holds the assets the story uses."""
    game = tmp_path / "game"
    for key in KNOWN:
        kind, name = key.split("/")
        d = game / "public" / "assets" / kind / name
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"name": name, "kind": kind, "files": []}))
    from utils import catalog
    catalog.rebuild_index(game)
    return game


def test_write_validate_export(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    S.write_story(pkg, _story(), name="knn-basics", tags=["algo:knn"])
    man = json.loads((pkg / "manifest.json").read_text())
    assert man["kind"] == "story" and man["files"] == ["story.json"]
    assert man["tags"][:2] == ["type:story", "story"] and "algo:knn" in man["tags"]
    assert man["choices"] == 1 and "knn.vote" in man["actions"]

    game = _game(tmp_path)
    empty = tmp_path / "no-packages"
    rep = S.validate_story(pkg, game, packages_dir=empty)
    assert rep["ok"], rep["errors"]

    entry = S.export_story(pkg, game)
    dest = game / "public" / "assets" / "story" / "knn-basics"
    assert entry["base"] == "assets/story/knn-basics/" and "exported" in entry
    assert json.loads((dest / "story.json").read_text())["id"] == "knn-basics"
    assert json.loads((dest / "manifest.json").read_text())["base"] == entry["base"]
    index = json.loads((game / "public" / "assets" / "index.json").read_text())
    assert "story/knn-basics" in index["assets"] and "environment/query-star" in index["assets"]
    assert not [p for p in dest.parent.iterdir() if p.name.startswith(".")]

    # Re-export replaces in place.
    S.export_story(pkg, game)
    assert (dest / "story.json").is_file()


def test_export_refuses_missing_assets(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    s = _story()
    s["assets"].append("environment/not-built")
    s["beats"][0]["spawn"] = [{"id": "x", "asset": "environment/not-built", "at": [0, 0]}]
    S.write_story(pkg, s, name="knn-basics")
    game = _game(tmp_path)
    with pytest.raises(ValueError, match="not-built"):
        S.export_story(pkg, game)
    assert not (game / "public" / "assets" / "story").exists() or \
        not (game / "public" / "assets" / "story" / "knn-basics").exists()


def test_project_packages_count_as_known(tmp_path: Path) -> None:
    pkgs = tmp_path / "project"
    d = pkgs / "dataviz-markers" / "marker-sphere"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"name": "marker-sphere", "kind": "environment"}))
    (pkgs / "dataviz-markers" / "manifest.json").write_text(json.dumps({"name": "markers", "kind": "set"}))
    found = S.known_assets(None, pkgs)
    assert found == {"environment/marker-sphere"}


def test_write_refuses_broken_story(tmp_path: Path) -> None:
    s = _story()
    s["beats"] = []
    with pytest.raises(ValueError):
        S.write_story(tmp_path / "pkg", s, name="knn-basics")
    with pytest.raises(ValueError):
        S.write_story(tmp_path / "pkg", _story(), name="Bad Name")


def test_export_all_stories(tmp_path: Path) -> None:
    root = tmp_path / "set"
    for n in ("knn-a", "knn-b"):
        S.write_story(root / n, copy.deepcopy(_story()), name=n)
    game = _game(tmp_path)
    out = S.export_all_stories(root, game)
    assert sorted(e["name"] for e in out) == ["knn-a", "knn-b"]


def test_outline_reads_every_beat() -> None:
    text = S.outline(_story())
    assert "**vote**" in text and "✔ Its nearest points" in text and "sandbox.open" in text


def test_story_packages_need_story_tags(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    S.write_story(pkg, _story(), name="knn-basics")
    man = json.loads((pkg / "manifest.json").read_text())
    assert {"type:story", "story"} <= set(man["tags"])
    man["tags"] = ["type:story"]
    (pkg / "manifest.json").write_text(json.dumps(man))
    rep = S.validate_story(pkg, known=KNOWN)
    assert not rep["ok"] and any("'story'" in e for e in rep["errors"])


def _long(n: int) -> dict:
    s = _story()
    filler = [{"id": f"f{i}", "say": "More.", "wait": "next"} for i in range(n - len(s["beats"]))]
    s["beats"] = s["beats"][:-1] + filler + s["beats"][-1:]
    return s


def test_beat_limits_soft_and_hard() -> None:
    rep = S.validate_story(_long(28), known=KNOWN)
    assert rep["ok"] and not any("beats:" in w for w in rep["warnings"])
    rep = S.validate_story(_long(29), known=KNOWN)
    assert rep["ok"] and any("aim for at most 28" in w for w in rep["warnings"])
    rep = S.validate_story(_long(100), known=KNOWN)
    assert rep["ok"]
    rep = S.validate_story(_long(101), known=KNOWN)
    assert not rep["ok"] and any("hard limit is 100" in e for e in rep["errors"])


def test_levels(tmp_path: Path) -> None:
    s = _story()
    assert S.validate_story(s, known=KNOWN)["stats"]["level"] == "easy"
    s["level"] = "expert"
    assert not S.validate_story(s, known=KNOWN)["ok"]
    s["level"] = "hard"
    rep = S.validate_story(s, known=KNOWN)
    assert rep["ok"] and any("knn-basics-hard" not in w and "-hard" in w for w in rep["warnings"])
    S.write_story(tmp_path / "pkg", s, name="knn-hard", tags=["algo:knn"])
    man = json.loads((tmp_path / "pkg" / "manifest.json").read_text())
    assert man["level"] == "hard" and "level:hard" in man["tags"]
    assert not any("-hard" in w for w in S.validate_story(tmp_path / "pkg", known=KNOWN)["warnings"])


def test_level_actions_in_catalogue() -> None:
    acts = S.actions()
    for name in ("ui.table", "ui.steps", "ui.note", "ui.chart", "data.values", "data.table",
                 "data.outlier", "data.scale", "data.shift", "data.duplicate", "data.imbalance",
                 "data.flipLabels", "model.params", "model.explain", "measure.distance",
                 "knn.concentration", "nn.gradients", "eval.roc", "eval.threshold"):
        assert name in acts, name
    assert "ravine" in acts["loss.surface"]["args"]["shape"]
    assert "heteroscedastic" in acts["data.scatter"]["args"]["dataset"]


def test_ui_note_length() -> None:
    s = _story()
    s["beats"][0]["act"] = {"action": "ui.note", "args": {"text": "x" * 141, "tone": "warn"}}
    rep = S.validate_story(s, known=KNOWN)
    assert not rep["ok"] and any("ui.note" in e for e in rep["errors"])


def test_custom_points_are_rows() -> None:
    s = _story()
    s["beats"][0]["act"] = {"action": "data.scatter", "args": {"dataset": "custom",
                            "points": [[0, 0, 1, 2.5], [1, 1], [1.5, -0.5, 0]]}}
    assert S.validate_story(s, known=KNOWN)["ok"]
    s["beats"][0]["act"]["args"]["points"].append({"x": 1, "z": 2, "cls": 0})
    rep = S.validate_story(s, known=KNOWN)
    assert not rep["ok"] and any("rows [x, z, class, y]" in e for e in rep["errors"])


def test_highlight_data_points() -> None:
    s = _story()
    s["beats"][0]["highlight"] = {"entity": "p3"}
    assert S.validate_story(s, known=KNOWN)["ok"]
    s["beats"][0]["highlight"] = {"entity": "pz"}
    assert not S.validate_story(s, known=KNOWN)["ok"]


def test_easy_stories_record_their_level(tmp_path: Path) -> None:
    S.write_story(tmp_path / "pkg", _story(), name="knn-basics")
    assert json.loads((tmp_path / "pkg" / "story.json").read_text())["level"] == "easy"
    man = json.loads((tmp_path / "pkg" / "manifest.json").read_text())
    assert man["level"] == "easy" and "level:easy" in man["tags"]


def test_highlight_points_added_with_an_id() -> None:
    s = _story()
    s["beats"][0]["act"] = {"action": "data.outlier", "args": {"at": [4, 4], "id": "outlier"}}
    s["beats"][1]["highlight"] = {"entity": "outlier"}
    assert S.validate_story(s, known=KNOWN)["ok"]
