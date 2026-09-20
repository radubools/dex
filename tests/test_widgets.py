"""Which widget opens which file.

The rules live per project in `assets/<project>/widgets.json`; the code lives
inside the skill that provides it. Everything here is read from disk per call,
which is what lets a widget built a moment ago work without a restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from dex import widgets


def make_widget(root: Path, name: str, *, built: bool = True, skill: str = "") -> Path:
    """A widget inside a skill, which is the only place one lives now.

    Returns the widget's directory, because a test that changes the bundle
    needs it and `skills/<skill>/<version>/widgets/<name>` is not a path worth
    spelling out at every call site.
    """
    from dex import skills

    named = skill or f"{name}-skill"
    version = root / "skills" / named / "v1"
    directory = version / "widgets" / name
    directory.mkdir(parents=True, exist_ok=True)
    (version / skills.MANIFEST_NAME).write_text(json.dumps({"name": named}))
    (directory / "widget.json").write_text(json.dumps({"name": name, "title": name.title()}))
    if built:
        (directory / "dist").mkdir(exist_ok=True)
        (directory / "dist" / "index.js").write_text("export function mount(){}")
    return directory


def make_registry(assets: Path, project: str, rules: list[dict]) -> None:
    (assets / project).mkdir(parents=True, exist_ok=True)
    (assets / project / "widgets.json").write_text(json.dumps({"rules": rules}))


def test_a_multipart_extension_is_what_distinguishes_a_pose(tmp_path: Path):
    """`.json` is far too coarse: a manifest is JSON and is not a figure."""
    make_widget(tmp_path, "pose-3d")
    assets = tmp_path / "assets"
    make_registry(assets, "yoga", [{"widget": "pose-3d", "extensions": [".pose.json"]}])

    hit = widgets.resolve(assets, tmp_path, "yoga", "yoga/p/poses/tree.pose.json")
    assert hit is not None and hit.name == "pose-3d"
    assert widgets.resolve(assets, tmp_path, "yoga", "yoga/p/manifest.json") is None


def test_filename_globs_cover_what_extensions_cannot(tmp_path: Path):
    make_widget(tmp_path, "pose-3d")
    assets = tmp_path / "assets"
    make_registry(assets, "yoga", [{"widget": "pose-3d", "filenames": ["*_pose.json"]}])

    assert widgets.resolve(assets, tmp_path, "yoga", "yoga/p/poses/tree_pose.json")
    assert widgets.resolve(assets, tmp_path, "yoga", "yoga/p/notes.json") is None


def test_the_first_matching_rule_wins(tmp_path: Path):
    """Order is the tie-breaker, so a specific rule goes above a general one."""
    make_widget(tmp_path, "special")
    make_widget(tmp_path, "general")
    assets = tmp_path / "assets"
    make_registry(assets, "p", [
        {"widget": "special", "filenames": ["narrated_*.mp4"]},
        {"widget": "general", "extensions": [".mp4"]},
    ])
    assert widgets.resolve(assets, tmp_path, "p", "p/x/narrated_demo.mp4").name == "special"
    assert widgets.resolve(assets, tmp_path, "p", "p/x/plain.mp4").name == "general"


def test_an_unbuilt_widget_falls_back_rather_than_showing_an_empty_frame(tmp_path: Path):
    make_widget(tmp_path, "half-done", built=False)
    assets = tmp_path / "assets"
    make_registry(assets, "p", [{"widget": "half-done", "extensions": [".json"]}])
    assert widgets.resolve(assets, tmp_path, "p", "p/x/a.json") is None
    # Still listed, so "I wrote it and it does not appear" is not the failure.
    listed = widgets.available(tmp_path)
    assert [w.name for w in listed] == ["half-done"]
    assert listed[0].built is False


def test_a_project_with_no_registry_uses_the_built_in_viewers(tmp_path: Path):
    make_widget(tmp_path, "pose-3d")
    assert widgets.resolve(tmp_path / "assets", tmp_path, "nothing-here", "x/y.json") is None


def test_a_broken_registry_is_survived_not_raised(tmp_path: Path):
    """A half-written rules file must not make every asset in the project fail."""
    assets = tmp_path / "assets"
    (assets / "p").mkdir(parents=True)
    (assets / "p" / "widgets.json").write_text("{ this is not json")
    assert widgets.rules_for(assets, "p") == []
    assert widgets.resolve(assets, tmp_path, "p", "p/x/a.json") is None


def test_a_rule_naming_a_widget_that_does_not_exist_is_ignored(tmp_path: Path):
    assets = tmp_path / "assets"
    make_registry(assets, "p", [{"widget": "imaginary", "extensions": [".json"]}])
    assert widgets.resolve(assets, tmp_path, "p", "p/x/a.json") is None


def test_the_version_changes_when_the_bundle_does(tmp_path: Path):
    """The cache key. import() and fetch cache by URL for the life of the page."""
    import os
    import time

    directory = make_widget(tmp_path, "w")
    first = widgets.available(tmp_path)[0].version
    later = time.time() + 5
    os.utime(directory / "dist" / "index.js", (later, later))
    assert widgets.available(tmp_path)[0].version != first


def test_a_bundle_is_served_only_from_the_skill_that_provides_it(tmp_path: Path):
    """The path comes off a URL, so every part of it is checked."""
    make_widget(tmp_path, "w", skill="demo")
    found = widgets.bundle_path(tmp_path, "demo", "v1", "w")
    assert found is not None and found.is_file()

    # A skill that is not there, a version that is not, and a widget that skill
    # does not provide — none of them resolve.
    assert widgets.bundle_path(tmp_path, "nope", "v1", "w") is None
    assert widgets.bundle_path(tmp_path, "demo", "v2", "w") is None
    assert widgets.bundle_path(tmp_path, "demo", "v1", "other") is None
    # And nothing walks out of the workspace.
    assert widgets.bundle_path(tmp_path, "..", "v1", "w") is None
    assert widgets.bundle_path(tmp_path, "demo", "..", "w") is None


def test_an_unbuilt_widget_has_no_bundle_to_serve(tmp_path: Path):
    make_widget(tmp_path, "w", built=False, skill="demo")
    assert widgets.bundle_path(tmp_path, "demo", "v1", "w") is None


def test_a_widget_says_which_skill_it_came_from(tmp_path: Path):
    """Two versions of a skill can be on disk; the name alone is not enough."""
    make_widget(tmp_path, "w", skill="demo")
    found = widgets.available(tmp_path)[0]
    assert (found.skill, found.skill_version) == ("demo", "v1")
    assert "/api/widgets/demo/v1/w/index.js" in found.to_json()["url"]


def test_matching_ignores_case(tmp_path: Path):
    make_widget(tmp_path, "video")
    assets = tmp_path / "assets"
    make_registry(assets, "p", [{"widget": "video", "extensions": [".mp4"]}])
    assert widgets.resolve(assets, tmp_path, "p", "p/x/CLIP.MP4")


def test_the_real_registries_point_at_widgets_that_exist():
    """The two shipped projects, checked against the widgets actually built."""
    repo = Path(__file__).resolve().parent.parent
    names = {w.name for w in widgets.available(repo)}
    for project in ("yoga", "algorithms"):
        registry = repo / "assets" / project / "widgets.json"
        if not registry.is_file():
            continue
        for rule in widgets.rules_for(repo / "assets", project):
            assert rule.widget in names, f"{project} maps to unknown widget {rule.widget}"
