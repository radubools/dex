"""Checks for game-catalog: building, writing, checking (with the game's engine), previewing and installing catalogs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from utils import story_catalog as sc  # noqa: E402

GAMES = Path.home() / "games"
ENGINE_GAME = GAMES / "model-workshop"
needs_engine = pytest.mark.skipif(not sc.has_engine(ENGINE_GAME), reason="needs node and ~/games with the engine")


def _catalog() -> dict:
    cat = sc.new_catalog("demo", "Demo")
    sc.add_section(cat, sc.section("basics", "Basics", layout="grid", columns=2, icon="🧱", tiles=[
        sc.tile("intro", "Intro", at=[0, 0]),
        sc.tile("moving", "Moving", at=[1, 0], after=["intro"]),
    ]))
    sc.add_section(cat, sc.section("road", "The road", layout="linear", placeholder=True, tiles=[
        sc.placeholder("one", "One"), sc.placeholder("two", "Two"), sc.placeholder("three", "Three"),
    ]))
    sc.add_section(cat, sc.section("maps", "Mind maps", layout="tree", placeholder=True, tiles=[
        sc.placeholder("array", "Arrays"),
        sc.placeholder("binary-search", "Binary search", parent="array"),
        sc.placeholder("graph", "Graphs"),
        sc.placeholder("bfs", "Breadth-first search", parent="graph"),
    ]))
    return cat


def test_building_keeps_what_is_there_unless_replaced():
    cat = _catalog()
    again = sc.add_section(cat, sc.section("basics", "Other"))
    assert again["title"] == "Basics" and len(cat["sections"]) == 3
    sc.add_tiles(cat, "basics", [sc.tile("intro", "Changed"), sc.tile("jumping", "Jumping")])
    titles = [t["title"] for t in sc.find_section(cat, "basics")["tiles"]]
    assert titles == ["Intro", "Moving", "Jumping"]
    sc.add_tiles(cat, "basics", [sc.tile("intro", "Changed")], replace=True)
    assert sc.find_section(cat, "basics")["tiles"][0]["title"] == "Changed"
    with pytest.raises(KeyError):
        sc.add_tiles(cat, "nowhere", [])


def test_story_keys_and_mark_ready_follow_variants():
    cat = _catalog()
    sc.find_section(cat, "road")["variants"] = [{"id": "easy", "title": "Easy", "suffix": ""},
                                                {"id": "hard", "title": "Hard", "suffix": "-hard"}]
    keys = sc.story_keys(cat)
    assert "story/one" in keys and "story/one-hard" in keys and "story/intro" in keys
    # One live without its hard variant stays a placeholder.
    assert sc.mark_ready(cat, {"story/one", "story/one-hard", "story/two"}) == ["road/one"]
    sc.mark_ready(cat, {"story/two", "story/two-hard", "story/three", "story/three-hard"})
    assert "placeholder" not in sc.find_section(cat, "road")


def test_write_catalog_writes_the_manifest(tmp_path):
    pkg = tmp_path / "catalog-demo"
    sc.write_catalog(pkg, _catalog())
    man = json.loads((pkg / "manifest.json").read_text())
    assert man["kind"] == "catalog" and man["name"] == "demo" and man["files"] == ["catalog.json"]
    assert man["sections"] == ["basics", "road", "maps"]
    assert man["tiles"] == 9 and man["placeholders"] == 7
    assert "type:catalog" in man["tags"]
    rep = sc.validate_catalog(pkg)
    assert rep["ok"] and not rep["engine"]
    bad = _catalog()
    bad["sections"][0]["tiles"][0]["story"] = "intro"
    with pytest.raises(ValueError, match="story must be story/<name>"):
        sc.write_catalog(tmp_path / "bad", bad)
    with pytest.raises(ValueError, match="files not in the package"):
        sc.write_catalog(tmp_path / "x", _catalog(), files=["thumbs/none.png"])


@needs_engine
def test_the_engine_checks_it(tmp_path):
    pkg = tmp_path / "catalog-demo"
    sc.write_catalog(pkg, _catalog())
    rep = sc.validate_catalog(pkg, ENGINE_GAME, known={"story/intro"})
    assert rep["ok"] and rep["engine"], rep
    assert any("story/moving" in w for w in rep["warnings"])  # not a placeholder, not in the game
    assert not any("story/one" in w for w in rep["warnings"])
    cat = _catalog()
    cat["sections"][0]["tiles"][1]["at"] = [0, 0]
    rep = sc.validate_catalog(cat, ENGINE_GAME, known=set())
    assert not rep["ok"] and any("share the cell" in e for e in rep["errors"])


@needs_engine
def test_layout_and_preview(tmp_path):
    pkg = tmp_path / "catalog-demo"
    sc.write_catalog(pkg, _catalog())
    lay = sc.layout(pkg, ENGINE_GAME)
    assert set(lay["sections"]) == {"basics", "road", "maps"}
    assert lay["sections"]["basics"]["pos"]["moving"] == [1, 0]
    assert [e["kind"] for e in lay["sections"]["road"]["edges"]] == ["path", "path"]
    made = sc.preview_catalog(pkg, ENGINE_GAME)
    assert [p.name for p in made] == ["catalog.png", "basics.png", "road.png", "maps.png"]
    assert all(p.stat().st_size > 1000 for p in made)
    text = sc.outline(pkg)
    assert "Mind maps [maps] tree" in text and "Binary search" in text


@needs_engine
def test_export_installs_into_the_game(tmp_path):
    try:
        from utils import catalog  # noqa: F401  game-mesh, in a project that has both
    except ImportError:
        pytest.skip("export needs game-mesh's catalog beside this skill (run in a project)")
    games = tmp_path / "games"
    game = games / "demo-game"
    game.mkdir(parents=True)
    (game / "package.json").write_text("{}")
    os.symlink(GAMES / "node_modules", games / "node_modules")
    pkg = tmp_path / "catalog-demo"
    sc.write_catalog(pkg, _catalog())
    entry = sc.export_catalog(pkg, game)
    assert entry["base"] == "assets/catalog/demo/"
    index = json.loads((game / "public/assets/index.json").read_text())
    assert index["assets"]["catalog/demo"]["kind"] == "catalog"
    assert sc.installed_catalog(game)["id"] == "demo"


def _subjects() -> dict:
    cat = _catalog()
    cat["navigation"] = "subjects"
    for s, blurb in zip(cat["sections"], ("Blocks first.", "One after another.", "Ideas around a centre.")):
        s["blurb"] = blurb
        s.setdefault("icon", "📘")
    return cat


def test_subjects_navigation_is_built_and_checked_without_a_game():
    assert sc.new_catalog("g", "G")
    assert "navigation" not in sc.new_catalog("g", "G")  # map is the default, left out
    assert sc.new_catalog("g", "G", navigation="subjects")["navigation"] == "subjects"
    assert sc.validate_catalog(_subjects())["ok"]
    bad = _subjects()
    bad["sections"][1]["after"] = ["basics"]
    bad["sections"][2]["parent"] = "road"
    rep = sc.validate_catalog(bad)
    assert not rep["ok"]
    assert any("road" in e and "after" in e for e in rep["errors"])
    assert any("maps" in e and "nest" in e for e in rep["errors"])
    # The same `after` on a map is a progression, and fine.
    on_map = dict(bad, navigation="map")
    on_map["sections"] = [dict(s) for s in bad["sections"]]
    on_map["sections"][2].pop("parent")
    assert sc.validate_catalog(on_map)["ok"]
    nav = _subjects()
    nav["navigation"] = "menu"
    assert any("navigation" in e for e in sc.validate_catalog(nav)["errors"])


def test_outline_shows_each_subject_as_its_classroom():
    text = sc.outline(_subjects())
    assert "navigation subjects" in text and "main menu" in text
    assert "subject 🧱 Basics [basics]: Blocks first." in text
    assert "classroom: 2 stories, grid" in text and "classroom: 3 stories, linear" in text
    assert "navigation map" in sc.outline(_catalog())


@needs_engine
def test_the_engine_checks_subjects(tmp_path):
    rep = sc.validate_catalog(_subjects(), ENGINE_GAME, known={"story/intro"})
    assert rep["ok"] and rep["engine"], rep
    assert not any("subject" in w for w in rep["warnings"])
    faceless = _subjects()
    faceless["sections"][0].pop("blurb")
    rep = sc.validate_catalog(faceless, ENGINE_GAME, known={"story/intro"})
    assert any("basics" in w and "blurb" in w for w in rep["warnings"])
    chained = _subjects()
    chained["sections"][1]["after"] = ["basics"]
    assert not sc.validate_catalog(chained, ENGINE_GAME, known=set())["ok"]
    pkg = tmp_path / "catalog-demo"
    sc.write_catalog(pkg, _subjects())
    made = sc.preview_catalog(pkg, ENGINE_GAME)
    assert made[0].name == "catalog.png" and len(made) == 4
