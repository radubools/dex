"""Which widget opens which file.

The rules live per project in `assets/<project>/widgets.json`; the code lives at
the top level in `widgets/`. Everything here is read from disk per call, which
is what lets a widget built a moment ago work without a restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from dex import widgets


def make_widget(root: Path, name: str, *, built: bool = True) -> None:
    directory = root / "widgets" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "widget.json").write_text(json.dumps({"name": name, "title": name.title()}))
    if built:
        (directory / "dist").mkdir(exist_ok=True)
        (directory / "dist" / "index.js").write_text("export function mount(){}")


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

    make_widget(tmp_path, "w")
    first = widgets.available(tmp_path)[0].version
    entry = tmp_path / "widgets" / "w" / "dist" / "index.js"
    later = time.time() + 5
    os.utime(entry, (later, later))
    assert widgets.available(tmp_path)[0].version != first


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
