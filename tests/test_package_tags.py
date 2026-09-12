"""Tags reach the Library over the event stream, and survive a restart.

The Library's filters are built from whatever tags the manifests carry — the UI
holds no vocabulary of its own — so the tags have to be both streamed as they
change and persisted, or the pills would be empty until someone reloaded and
wrong until someone re-ran the tagging.
"""

from __future__ import annotations

import json

import pytest
from watchfiles import Change

from dex.bus import EventBus
from dex.queue import TaskManager
from dex.store import PackageTagStore, normalise_tags, read_manifest_tags
from dex.watcher import AssetWatcher


@pytest.fixture
async def watcher(config, db):
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    watcher = AssetWatcher(config, bus, manager)
    try:
        yield watcher
    finally:
        await bus.stop()


def write_manifest(config, slug: str, tags: list[str] | object) -> object:
    package = config.assets_dir / "algorithms" / slug
    package.mkdir(parents=True, exist_ok=True)
    manifest = package / "manifest.json"
    manifest.write_text(json.dumps({"tags": tags}), encoding="utf-8")
    return manifest


async def test_a_manifest_write_is_indexed_and_announced(watcher, config):
    manifest = write_manifest(config, "two-sum", ["difficulty:medium", "topic:arrays"])
    seen = []
    watcher.bus.publish = lambda event: seen.append(event)

    await watcher._handle(Change.added, str(manifest))

    stored = await watcher.tags.for_project("algorithms")
    assert stored["two-sum"] == ["difficulty:medium", "topic:arrays"]
    published = [e for e in seen if e.type == "tags"]
    assert published[-1].data == {
        "project": "algorithms",
        "slug": "two-sum",
        "tags": ["difficulty:medium", "topic:arrays"],
        "removed": False,
    }


async def test_a_removed_manifest_takes_its_tags_out_of_the_index(watcher, config):
    manifest = write_manifest(config, "two-sum", ["difficulty:easy"])
    await watcher._handle(Change.added, str(manifest))
    seen = []
    watcher.bus.publish = lambda event: seen.append(event)

    manifest.unlink()
    await watcher._handle(Change.deleted, str(manifest))

    assert "two-sum" not in await watcher.tags.for_project("algorithms")
    # Not "known and untagged" — absent, which is a different thing.
    assert [e for e in seen if e.type == "tags"][-1].data["removed"] is True


async def test_the_index_catches_up_on_manifests_changed_while_it_was_down(watcher, config):
    # Nobody was listening when these were written.
    write_manifest(config, "two-sum", ["difficulty:medium"])
    write_manifest(config, "coin-change", ["difficulty:hard", "topic:dynamic-programming"])

    await watcher.catch_up()

    stored = await watcher.tags.for_project("algorithms")
    assert stored["two-sum"] == ["difficulty:medium"]
    assert stored["coin-change"] == ["difficulty:hard", "topic:dynamic-programming"]


async def test_catching_up_drops_a_package_that_is_no_longer_there(watcher, config, db):
    await PackageTagStore(db).set("algorithms", "deleted-one", ["difficulty:easy"])
    (config.assets_dir / "algorithms").mkdir(parents=True, exist_ok=True)

    await watcher.catch_up()

    assert "deleted-one" not in await watcher.tags.for_project("algorithms")


async def test_a_manifest_at_the_project_root_is_not_a_package(watcher, config):
    """`<project>/manifest.json` belongs to no package and must not index one."""
    project = config.assets_dir / "algorithms"
    project.mkdir(parents=True, exist_ok=True)
    stray = project / "manifest.json"
    stray.write_text('{"tags": ["difficulty:easy"]}', encoding="utf-8")

    await watcher._handle(Change.added, str(stray))

    assert await watcher.tags.for_project("algorithms") == {}


@pytest.mark.parametrize(
    "raw, expected",
    [
        (["difficulty:easy", "topic:arrays"], ["difficulty:easy", "topic:arrays"]),
        # Case and padding are noise; two spellings of one tag would show as two pills.
        (["  Difficulty:Easy  "], ["difficulty:easy"]),
        (["difficulty:easy", "difficulty:easy"], ["difficulty:easy"]),
        (["difficulty:easy", "", "   ", None, 7], ["difficulty:easy"]),
        # The guide asks for `group:value` with no space; a slip must not become
        # a pill of its own reading " easy".
        (["difficulty: easy"], ["difficulty:easy"]),
        # Kebab-case is the convention, so the near-misses fold into it.
        (["topic: Two Pointers"], ["topic:two-pointers"]),
        (["topic:union_find"], ["topic:union-find"]),
        # Half a tag names nothing.
        (["topic:", ":arrays"], []),
        # A tag with no group at all is still usable — it just has no heading.
        (["arrays"], ["arrays"]),
        # Only the first colon separates; the rest belongs to the value.
        (["difficulty:easy:extra"], ["difficulty:easy:extra"]),
        # Shapes an agent might produce that are not a tag list at all.
        ({"difficulty": "easy"}, []),
        ("difficulty:easy", []),
        (None, []),
    ],
)
def test_only_usable_tags_survive(raw, expected):
    assert normalise_tags(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # The yoga project puts each chakra's traditional colour in the tag on
        # purpose, so the pills read at a glance in a list that is otherwise
        # all words. Canonicalising must not strip or mangle it.
        (["chakra:\U0001f534muladhara"], ["chakra:\U0001f534muladhara"]),
        (["chakra:\u26aasahasrara"], ["chakra:\u26aasahasrara"]),
        # Case-folding and stray spaces are still cleaned up around it.
        (["Chakra: \U0001f7e1Manipura"], ["chakra:\U0001f7e1manipura"]),
    ],
)
def test_an_emoji_in_a_value_is_carried_through(raw, expected):
    assert normalise_tags(raw) == expected


def test_a_chakra_with_and_without_its_colour_are_different_tags():
    """Two pills, deliberately: the colour is part of the value, so a tag
    written without it is a different tag rather than the same one misspelt."""
    assert normalise_tags(["chakra:\U0001f534muladhara", "chakra:muladhara"]) == [
        "chakra:\U0001f534muladhara",
        "chakra:muladhara",
    ]


def test_one_tag_spelled_three_ways_is_one_pill():
    """The Library counts by exact string, so near-misses must collapse first."""
    assert normalise_tags(
        ["topic:two-pointers", "Topic: Two Pointers", "topic:two_pointers"]
    ) == ["topic:two-pointers"]


def test_tag_order_is_kept_because_a_manifest_lists_them_deliberately():
    # Difficulty first, then topics: the guide asks for that order and the
    # Library shows tags in the order the manifest gives them.
    assert normalise_tags(["difficulty:hard", "topic:graphs", "topic:strings"]) == [
        "difficulty:hard",
        "topic:graphs",
        "topic:strings",
    ]


def test_an_unreadable_manifest_yields_no_tags(tmp_path):
    broken = tmp_path / "manifest.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert read_manifest_tags(broken) == []
    assert read_manifest_tags(tmp_path / "absent.json") == []


def test_both_project_guides_carry_the_same_spelling_rules(config):
    """The convention is one convention; a project that states it differently
    would produce tags the shared Library cannot group together."""
    from pathlib import Path

    guides = {
        name: (Path("assets") / name / "AGENTS.md").read_text(encoding="utf-8")
        for name in ("algorithms", "yoga")
    }
    for name, text in guides.items():
        assert "Every tag is written `group:value`" in text, name
        assert "Lowercase, kebab-case, no spaces" in text, name
        assert "Group names are singular" in text, name
        assert "difficulty:easy" in text, name
        # Each project names its own groups, but every one of them must be
        # written in the `group:` form the Library splits on.
        assert "A new *group* is not" in text, name
