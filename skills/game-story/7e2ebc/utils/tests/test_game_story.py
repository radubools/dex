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


def _fake_game(root: Path) -> Path:
    """A minimal Model Workshop-shaped game for storysim.mjs: one action per simulator feature."""
    import textwrap
    (root / "src" / "actions").mkdir(parents=True)
    eng = root / "node_modules" / "@games" / "engine"
    eng.mkdir(parents=True)
    (root / "package.json").write_text('{"name": "fake", "type": "module"}')
    (eng / "package.json").write_text('{"name": "@games/engine", "type": "module", "exports": {"./core": "./core.js"}}')
    (eng / "core.js").write_text("export const makeRng = (s) => () => 0.5;\n")
    (root / "src" / "dataset.js").write_text("export class Dataset { constructor() { this.points = []; } }\n")
    (root / "src" / "config.js").write_text("export const ALGORITHM = { knn: { index: 0 } };\n")
    (root / "src" / "actions" / "general.js").write_text(textwrap.dedent("""
        export const LOSS_LABELS = {};
        export function runLive(ctx) { for (const f of (ctx.state.live || new Map()).values()) f(ctx); }
    """))
    (root / "src" / "actions" / "index.js").write_text(textwrap.dedent("""
        export const ACTIONS = {
          'fake.chain'(ctx) { ctx.stage.spawn('a:1', 'environment/x', {}).setPos(1, 0, 2).setScale(3); },
          'fake.meter'(ctx) { ctx.ui.meter('acc', { label: 'acc', value: 1 }); return [...ctx.ui.items.keys()]; },
          'fake.chart'(ctx) { ctx.ui.chart('c', { title: 't' }); },
          'fake.hide'(ctx, { id }) { ctx.ui.hide(id); },
          'fake.table'(ctx) {
            const box = ctx.ui.doc.createElement('div'); const tb = ctx.ui.doc.createElement('tbody');
            const tr = ctx.ui.doc.createElement('tr'); const td = ctx.ui.doc.createElement('td');
            tr.append(td); tb.append(tr); box.append(tb); ctx.ui.items.set('table:m', box);
            box.querySelectorAll('tbody tr')[0].querySelector('td').classList.add('mark');
            return box.querySelectorAll('td.mark').length;
          },
          'fake.live'(ctx) { ctx.state.n = 0; ctx.state.live = new Map([['n', (c) => { c.state.n += 1; }]]); },
          'fake.read'(ctx) { return ctx.state.n; },
        };
    """))
    return root


def test_storysim_matches_the_engine(tmp_path: Path) -> None:
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    game = _fake_game(tmp_path / "game")
    beats = [{"id": n, "say": "x", "act": {"action": a, "args": args}, "wait": "next"} for n, a, args in [
        ("chain", "fake.chain", {}), ("meter", "fake.meter", {}), ("chart", "fake.chart", {}),
        ("hide-chart", "fake.hide", {"id": "c"}), ("hide-meter", "fake.hide", {"id": "acc"}),
        ("table", "fake.table", {}), ("live", "fake.live", {}), ("read", "fake.read", {})]]
    story_dict = {"schema": 1, "id": "fake", "title": "t", "summary": "s", "algorithm": "knn",
                  "assets": [], "beats": beats}
    tr = {s["id"]: s for s in S.simulate(story_dict, game)}
    assert "meter:acc" in tr["meter"]["result"] and tr["meter"]["meters"]["acc"]["value"] == 1
    assert "c" in tr["chart"]["charts"] and "c" not in tr["hide-chart"]["charts"]
    assert tr["hide-meter"]["meters"] == {}
    assert tr["table"]["result"] == 1
    # The live refresh runs after every action, as lesson.runAction does: once after
    # fake.live itself, once after fake.read (which reads the count before its refresh).
    assert tr["read"]["result"] == 1


MODEL_WORKSHOP = Path.home() / "games" / "model-workshop"


def _gd_story(beats: list[dict]) -> dict:
    return {"schema": 1, "id": "gd-lab", "title": "t", "summary": "s", "algorithm": "gradient-descent",
            "assets": [], "beats": [{"id": f"b{i}", "say": "x", "wait": "next", **b} for i, b in enumerate(beats)]}


def test_check_lab_plays_the_story_through_the_game_s_machine() -> None:
    import shutil
    if not shutil.which("node") or not S.has_lab_machines(MODEL_WORKSHOP):
        pytest.skip("needs node and ~/games/model-workshop with lab machines")
    surface = {"act": {"action": "loss.surface", "args": {"shape": "bowl"}}}
    # Stepping a ball that was never dropped: the lab refuses it, so the story is not valid.
    bad = _gd_story([surface, {"act": {"action": "gd.step", "args": {"steps": 3}}}])
    rep = S.check_lab(bad, MODEL_WORKSHOP)
    assert not rep["ok"] and "b1" in rep["errors"][0] and "Drop a ball first" in rep["errors"][0]
    assert not S.validate_story(bad, lab=MODEL_WORKSHOP)["ok"]
    # A ball that flew out has to be dropped again before it can step.
    flown = _gd_story([surface, {"act": {"action": "gd.ball", "args": {"at": [3, 3]}}},
                       {"act": {"action": "gd.step", "args": {"lr": 1.8, "steps": 30}}},
                       {"act": {"action": "gd.step", "args": {"steps": 1}}}])
    rep = S.check_lab(flown, MODEL_WORKSHOP)
    assert rep["states"][2] == "out" and "left the surface" in rep["errors"][0]
    # Dropped again, it rolls; the player can do what a wait asks (the drop tool runs gd.ball).
    good = _gd_story([surface, {"act": {"action": "gd.ball", "args": {"at": [3, 3]}}},
                      {"act": {"action": "gd.step", "args": {"lr": 1.8, "steps": 30}}},
                      {"act": {"action": "gd.ball", "args": {"at": [0, -3]}}, "wait": {"action": "gd.ball"}},
                      {"act": {"action": "gd.step", "args": {"lr": 0.3, "steps": 5}}}])
    rep = S.check_lab(good, MODEL_WORKSHOP)
    assert rep["ok"], rep["errors"]
    assert rep["states"] == ["empty", "ready", "out", "ready", "ready"]
    md = S.lab_machine("gradient-descent", MODEL_WORKSHOP)
    assert "**out**" in md and "Drop a ball first." in md


def _v2_story(beats: list[dict]) -> dict:
    return {"schema": 2, "id": "gd-v2", "title": "t", "summary": "s", "algorithm": "gradient-descent",
            "assets": ["environment/marker-halo"] if any(b.get("spawn") for b in beats) else [], "beats": beats}


def test_schema_2_is_checked_and_played_by_the_game_s_engine() -> None:
    import shutil
    if not shutil.which("node") or not S.has_lab_machines(MODEL_WORKSHOP):
        pytest.skip("needs node and ~/games/model-workshop")
    beats = [
        {"id": "setup", "section": "Bowl", "say": "A bowl and a ball.", "wait": "next", "act": [
            {"action": "loss.surface", "args": {"shape": "bowl"}},
            {"action": "gd.ball", "args": {"at": [-3, 0]}},
            {"parallel": [{"action": "gd.setRate", "args": {"lr": 0.3}}, {"action": "ui.formula", "args": {"text": "w ← w − lr·g"}}]}]},
        {"id": "loss", "say": "The loss is {$model.loss.mse:2}, w is {$model.param.w}.", "wait": "next",
         "spawn": [{"id": "goal", "asset": "environment/marker-halo", "at": [0, 0]}],
         "highlight": [{"entity": "goal", "callout": "the bottom"}]},
        {"id": "step", "say": "One step.", "wait": "next", "act": {"action": "gd.step", "args": {"steps": 1}, "as": "s1"}},
        {"id": "ask", "say": "Which way is downhill?", "wait": {"choice": {"prompt": "Step which way?", "as": "dir", "options": [
            {"label": "Against the gradient", "correct": True, "feedback": "Yes.", "value": "down"},
            {"label": "Along it", "correct": False, "feedback": "That climbs.", "goto": "help"}]}}},
        {"id": "w-now", "say": "Type w.", "wait": {"input": {"prompt": "w = ?", "answer": "$model.param.w", "tolerance": 0.01, "as": "typed", "right": "Right: {typed:2}."}}},
        {"id": "help", "say": "The gradient points uphill.", "wait": "next", "when": "dir != 'down'", "goto": "ask"},
        {"id": "end", "say": "Done, w = {typed:2}.", "wait": "next", "end": True},
    ]
    story = _v2_story(beats)
    # Before: missing game root for schema 2.
    assert not S.validate_story(story)["ok"]
    rep = S.validate_story(story, lab=MODEL_WORKSHOP)
    assert rep["ok"], rep["errors"]
    runs = S.explore(story, MODEL_WORKSHOP)
    main, remedial = runs[0], runs[1]
    assert main["finished"] and main["path"] == ["setup", "loss", "step", "ask", "w-now", "end"]
    tr = {s["id"]: s for s in main["trace"]}
    assert "–" not in tr["loss"]["shown"]["say"] and "w is −3" in tr["loss"]["shown"]["say"]
    assert tr["w-now"]["shown"]["feedback"].startswith("Right:")
    assert remedial["pick"] == {"beat": "ask", "option": 1} and remedial["finished"]
    assert remedial["path"][:6] == ["setup", "loss", "step", "ask", "help", "ask"]
    # A read that is not there shows "–": an error.
    bad = _v2_story(beats[:1] + [{"id": "x", "say": "Prob {$model.prob.p9}", "wait": "next", "end": True}])
    rep = S.check_lab(bad, MODEL_WORKSHOP)
    assert not rep["ok"] and "–" in rep["errors"][0]
    # The engine's own checks: an unknown goto.
    worse = _v2_story([{"id": "a", "say": "x", "wait": "next", "goto": "nowhere"}])
    assert any("nowhere" in e for e in S.validate_story(worse, lab=MODEL_WORKSHOP)["errors"])
    assert "together(" in S.outline(story) and "### Bowl" in S.outline(story)


def test_e2e_gate_skeleton_and_done(tmp_path: Path) -> None:
    import hashlib
    pkg = tmp_path / "story-x"
    pkg.mkdir()
    story = {"schema": 2, "id": "story-x", "title": "t", "summary": "s", "algorithm": "knn", "assets": [], "beats": [
        {"id": "a", "say": "Class {vote.cls}", "wait": "next", "act": {"action": "knn.vote", "as": "vote"}},
        {"id": "b", "say": "Add three points.", "wait": {"until": "$data.count >= 8"}},
        {"id": "c", "say": "Bye.", "wait": "next"}]}
    (pkg / "story.json").write_text(json.dumps(story))
    sk = S.e2e_skeleton(pkg)
    assert set(sk["beats"]) == {"a", "b"} and "expect" in sk["beats"]["a"] and "do" in sk["beats"]["b"]
    assert "no e2e.json" in S.e2e_tests_problems(pkg)[0]
    (pkg / "e2e.json").write_text(json.dumps(sk))
    assert any("TODO" in p for p in S.e2e_tests_problems(pkg))
    tests = {"schema": 1, "beats": {"a": {"expect": ["vote.cls == 1"]}}}
    (pkg / "e2e.json").write_text(json.dumps(tests))
    assert any("until" in p and "'b'" in p for p in S.e2e_tests_problems(pkg))
    tests["beats"]["b"] = {"do": [{"action": "data.add", "args": {"at": [1, 1], "cls": 0}}]}
    (pkg / "e2e.json").write_text(json.dumps(tests))
    assert S.e2e_tests_problems(pkg) == []
    # The gate: a report for this exact story.json and e2e.json.
    assert "no end-to-end test report" in S.e2e_problem(pkg)
    (pkg / "e2e").mkdir()
    sha = lambda f: hashlib.sha256(f.read_bytes()).hexdigest()  # noqa: E731
    rep = {"passed": True, "story": {"sha256": sha(pkg / "story.json")}, "tests": {"sha256": sha(pkg / "e2e.json")}}
    (pkg / "e2e" / "report.json").write_text(json.dumps(rep))
    assert S.e2e_problem(pkg) is None
    (pkg / "story.json").write_text(json.dumps({**story, "title": "t2"}))
    assert "changed" in S.e2e_problem(pkg)


def test_math_counts_half_and_plays_through_the_game() -> None:
    assert S.text_length("a $$\\frac{1}{2}$$ b") == 2 + 6 + 2
    assert S.unclosed_math("x $$ y") == "$$ y" and S.unclosed_math("$$x$$") is None
    import shutil
    if not shutil.which("node") or not S.has_lab_machines(MODEL_WORKSHOP):
        pytest.skip("needs node and ~/games/model-workshop")
    story = _v2_story([
        {"id": "a", "say": "The step is $$w \\leftarrow w - \\eta g$$, now $$w = \\val{$model.param.w:2}$$.", "wait": "next", "act": [
            {"action": "loss.surface", "args": {"shape": "bowl"}}, {"action": "gd.ball", "args": {"at": [-3, 0]}},
            {"action": "ui.steps", "args": {"id": "s", "title": "Step", "lines": ["$$w = \\val{$model.param.w:2}$$"]}}]},
        {"id": "b", "say": "Done.", "wait": "next", "end": True}])
    assert S.validate_story(story, lab=MODEL_WORKSHOP)["ok"]
    tr = S.simulate(story, MODEL_WORKSHOP)
    assert "$$w = −3.00$$" in tr[0]["shown"]["say"] and tr[0]["panels"]["steps:s"][1] == "$$w = −3.00$$"
    bad = _v2_story([{"id": "a", "say": "Broken $$w = 1", "wait": "next", "end": True}])
    assert not S.validate_story(bad, lab=MODEL_WORKSHOP)["ok"]


def test_more_is_the_only_explanation_field():
    story = {"beats": [
        {"id": "a", "say": "x", "more": ["One.", "Two."]},
        {"id": "b", "say": "x", "hint": "no"},
        {"id": "c", "say": "x", "wait": {"choice": {"prompt": "?", "more": "no", "options": [{"label": "y", "explanation": "no"}]}}},
    ]}
    probs = S.hint_key_problems(story)
    assert len(probs) == 3
    assert any("'hint'" in p for p in probs) and any("belongs on the beat" in p for p in probs)
    assert any("'explanation'" in p for p in probs)
    assert S.missing_more(story) == ["c"]


def test_more_limits_match_the_engine():
    assert S.more_problems(None) == []
    assert S.more_problems("fine") == []
    assert S.more_problems([]) and S.more_problems(["ok", " "])
    assert "max 8" in S.more_problems(["p"] * 9)[0]
    assert "max 400" in S.more_problems(["x" * 401])[0]
    assert S.more_problems(["$$" + "a" * 700 + "$$"]) == []  # a formula counts half
    assert "does not close" in S.more_problems(["$$x"])[0]


def test_many_objects_are_reported_but_never_fail() -> None:
    import shutil
    if not shutil.which("node") or not S.has_lab_machines(MODEL_WORKSHOP):
        pytest.skip("needs node and ~/games/model-workshop")
    halos = [{"id": f"h{i}", "asset": "environment/marker-halo", "at": [i % 20 - 10, i // 20 - 7]} for i in range(300)]
    crowded = _v2_story([{"id": "many", "say": "Many.", "wait": "next", "spawn": halos, "end": True}])
    rep = S.check_lab(crowded, MODEL_WORKSHOP)
    assert rep["ok"], rep["errors"]
    assert rep["max_objects"] == 300
    assert not any("objects" in w for w in rep["warnings"])


def test_board_lines_camera_frames_line_ids() -> None:
    from utils.code_board import board_lines_camera
    src = "def f(x):\n    y = x + 1\n    return y\n\nr = f(2)"
    cam = board_lines_camera(src, 2, 3, duration=1.2)
    assert cam == {"frame": ["code:line-2", "code:line-3"], "duration": 1.2}
    assert "target" not in cam
    with pytest.raises(ValueError):
        board_lines_camera(src, 4, 9)


def test_clear_screenshots_keeps_the_reports(tmp_path):
    """done() deletes a passed story's screenshots; the pull gate's report stays."""
    pkg = tmp_path / "story-demo"
    shots = pkg / "e2e" / "desktop" / "shots"
    shots.mkdir(parents=True)
    (shots / "r0-001-intro.png").write_bytes(b"\x89PNG" + b"0" * 100)
    (pkg / "e2e" / "small").mkdir()
    (pkg / "e2e" / "small" / "r0-001-intro.jpg").write_bytes(b"jpg")
    (pkg / ".review").mkdir()
    (pkg / ".review" / "r0-001-intro.jpg").write_bytes(b"jpg")
    for keep in ("report.json", "review.md", "e2e.log", "e2e.done"):
        (pkg / "e2e" / keep).write_text("{}")
    (pkg / "e2e" / "desktop" / "report.json").write_text("{}")
    out = S.clear_screenshots(pkg)
    assert out["files"] == 3 and out["bytes"] > 100
    left = sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file())
    assert left == ["e2e/desktop/report.json", "e2e/e2e.done", "e2e/e2e.log", "e2e/report.json", "e2e/review.md"]
    assert not (pkg / ".review").exists() and not (pkg / "e2e" / "small").exists()
    assert S.clear_screenshots(pkg) == {"files": 0, "bytes": 0}  # twice is harmless
