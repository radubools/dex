"""Reading a skill, and the version that says which one it is."""

from __future__ import annotations

import json
import time

import pytest

from dex import skills


@pytest.fixture
def made(tmp_path):
    """A skill on disk, with one module."""

    def build(name: str = "demo", **files: str):
        # Staged under a placeholder version; `publish` renames it to the
        # content hash. The layout is `skills/<name>/<version>/`.
        path = tmp_path / "skills" / name / "new"
        (path / "utils").mkdir(parents=True)
        for rel, body in (files or {"utils/thing.py": "X = 1\n"}).items():
            target = path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        (path / skills.MANIFEST_NAME).write_text(
            json.dumps({"name": name, "description": "a demo"}) + "\n"
        )
        # Published, so it has the versioned directory everything else expects.
        return skills.publish(skills.read(path))

    return build


# ------------------------------------------------------------------ versions


def test_a_version_is_short_and_stable(made):
    skill = made()
    first = skills.fingerprint(skill.path)
    assert len(first) == skills.VERSION_CHARS
    assert skills.fingerprint(skill.path) == first


def test_changing_a_module_changes_the_version(made):
    skill = made()
    before = skills.fingerprint(skill.path)
    (skill.utils_dir / "thing.py").write_text("X = 2\n")
    assert skills.fingerprint(skill.path) != before


def test_renaming_a_module_changes_the_version(made):
    """The hash covers names as well as bytes; a rename is a change."""
    skill = made()
    before = skills.fingerprint(skill.path)
    (skill.utils_dir / "thing.py").rename(skill.utils_dir / "other.py")
    assert skills.fingerprint(skill.path) != before


def test_the_manifest_is_not_part_of_its_own_hash(made):
    """It holds the version, so hashing it would never settle."""
    skill = made()
    before = skills.fingerprint(skill.path)
    (skill.path / skills.MANIFEST_NAME).write_text(json.dumps({"name": "x"}) + "\n")
    assert skills.fingerprint(skill.path) == before


def test_build_artefacts_are_not_part_of_the_hash(made):
    """`__pycache__` belongs to whoever last imported the module."""
    skill = made()
    before = skills.fingerprint(skill.path)
    cache = skill.utils_dir / "__pycache__"
    cache.mkdir()
    (cache / "thing.cpython-312.pyc").write_bytes(b"\x00\x01")
    assert skills.fingerprint(skill.path) == before


def test_stamping_records_the_version_and_the_time(made):
    skill = made()
    before = time.time()
    skills.publish(skill)

    stored = json.loads((skill.path / skills.MANIFEST_NAME).read_text())
    assert stored["version"] == skills.fingerprint(skill.path)
    assert stored["updated"] >= before
    assert skills.read(skill.path).version == stored["version"]


# -------------------------------------------------------------- requirements


DOCSTRING_IMPORTS = '''\
"""A module that explains itself.

    from utils import rig
    import numpy as np

That block is an example, not a dependency.
"""

import math
import itertools
'''


def test_requirements_come_from_real_imports_not_docstrings(made):
    """These modules teach by example, and an example is not a dependency."""
    skill = made(**{"utils/thing.py": DOCSTRING_IMPORTS})
    skills.publish(skill)
    assert skill.requires == []


def test_a_real_third_party_import_is_recorded(made):
    skill = made(**{"utils/thing.py": "import numpy as np\nimport math\n"})
    skills.publish(skill)
    assert skill.requires == ["numpy"]


def test_a_syntax_error_does_not_take_the_scan_down(made):
    skill = made(**{"utils/broken.py": "def (:\n"})
    skills.publish(skill)
    assert skill.requires == []


# -------------------------------------------------------------------- reading


def test_a_directory_without_a_manifest_is_not_a_skill(tmp_path):
    (tmp_path / "notaskill").mkdir()
    assert skills.read(tmp_path / "notaskill") is None


def test_an_unreadable_manifest_is_refused_not_raised(tmp_path):
    path = tmp_path / "broken"
    path.mkdir()
    (path / skills.MANIFEST_NAME).write_text("{not json")
    assert skills.read(path) is None


def test_the_package_marker_belongs_to_nobody(made):
    """`__init__.py` is the materialised package's, not any one skill's.

    Several skills land in the same `utils/`; whichever was copied last would
    otherwise own the marker and quietly replace the others' docstring.
    """
    skill = made(**{"utils/thing.py": "X = 1\n", "utils/__init__.py": '"""mine."""\n'})
    assert [m.name for m in skill.modules()] == ["thing.py"]


def test_rules_come_from_bind_json(made):
    skill = made()
    (skill.path / "bind.json").write_text(
        json.dumps({"rules": [{"widget": "pose-3d", "extensions": [".pose.json"]}]})
    )
    assert skill.rules() == [{"widget": "pose-3d", "extensions": [".pose.json"]}]


def test_a_skill_with_no_bind_file_contributes_no_rules(made):
    assert made().rules() == []


def test_widgets_are_named_by_their_directories(made):
    skill = made()
    widget = skill.widgets_dir / "pose-3d"
    widget.mkdir(parents=True)
    (widget / "widget.json").write_text(json.dumps({"name": "pose-3d"}))
    # A directory with no manifest is not a widget, however suggestive.
    (skill.widgets_dir / "scratch").mkdir()
    assert skill.widgets() == ["pose-3d"]


def test_listing_finds_every_skill_in_the_workspace(made, tmp_path):
    made("alpha")
    made("beta")
    (tmp_path / "skills" / "loose-file.txt").write_text("x")
    assert [s.name for s in skills.all_skills(tmp_path)] == ["alpha", "beta"]


def test_no_skills_directory_is_not_an_error(tmp_path):
    assert skills.all_skills(tmp_path) == []


# ----------------------------------------------------- the yoga extraction


def test_the_extracted_skills_match_what_is_live():
    """Step 1 of the migration, asserted rather than remembered.

    The whole claim of the extraction is that nothing changed: the modules in
    `skills/` are the ones 147 packages already import. If this ever fails, the
    skill and the project have drifted and the materialise in step 2 would be
    silently rewriting live code.
    """
    from pathlib import Path

    live = Path("assets/yoga/utils")
    if not live.is_dir():  # a checkout without the yoga assets
        pytest.skip("no yoga assets here")

    # The versions yoga actually has enabled, not every version on disk. Two
    # versions of a skill can sit side by side, and the older one's modules are
    # legitimately different from what is materialised.
    on = skills.read_enabled(Path("assets/yoga")).skills
    extracted = {
        m.name: m
        for skill in skills.all_skills(Path("."))
        if on.get(skill.name) == skill.version
        for m in skill.modules()
        if m.suffix == ".py"
    }
    for module in sorted(live.glob("*.py")):
        if module.name == "__init__.py":
            continue
        assert module.name in extracted, f"{module.name} is in no skill"
        assert extracted[module.name].read_bytes() == module.read_bytes(), (
            f"{module.name} has drifted from the skill"
        )


# -------------------------------------------------------------- materialising


def clash_dir(root):
    """A second skill claiming a name `figure` already has."""
    return root / "skills" / "other" / "new"


def clash_utils(root):
    path = clash_dir(root) / "utils"
    path.mkdir(parents=True, exist_ok=True)
    return path


def at(root, name: str):
    """Where a skill lives now. Its directory name carries its version."""
    found = skills.find(root, name)
    assert found is not None, f"no skill {name}"
    return found.path


@pytest.fixture
def workspace(tmp_path):
    """A workspace with two skills and an empty project."""

    def build():
        for name, modules in (
            ("figure", {"rig.py": "R = 1\n", "skeleton.py": "S = 1\n"}),
            ("packages", {"manifest.py": "M = 1\n"}),
        ):
            staged = tmp_path / "skills" / name / "new"
            (staged / "utils").mkdir(parents=True)
            for file, body in modules.items():
                (staged / "utils" / file).write_text(body)
            (staged / skills.MANIFEST_NAME).write_text(
                json.dumps({"name": name}) + "\n"
            )
            skills.publish(skills.read(staged))
        project = tmp_path / "assets" / "demo"
        project.mkdir(parents=True)
        return tmp_path, project

    return build


def test_enabling_writes_every_module(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure", "packages"])
    assert sorted(p.name for p in (project / "utils").glob("*.py")) == [
        "__init__.py", "manifest.py", "rig.py", "skeleton.py",
    ]


def test_materialising_twice_changes_nothing(workspace):
    """Idempotence is the whole safety argument for running this on live work."""
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (project / "utils").iterdir()}
    skills.materialise(root, project, ["figure"])
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in (project / "utils").iterdir()}
    assert after == before


def test_an_unchanged_module_keeps_its_mtime(workspace):
    """`API.md` regenerates when a module is newer than it.

    Rewriting every file every run would rebuild the index on every task, for
    nothing.
    """
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    was = (project / "utils" / "rig.py").stat().st_mtime_ns
    skills.materialise(root, project, ["figure"])
    assert (project / "utils" / "rig.py").stat().st_mtime_ns == was


def test_disabling_takes_back_only_its_own_modules(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure", "packages"])
    skills.materialise(root, project, ["figure"])

    left = sorted(p.name for p in (project / "utils").glob("*.py"))
    assert "manifest.py" not in left
    assert "rig.py" in left and "skeleton.py" in left


def test_a_module_nobody_materialised_is_left_alone(workspace):
    """A file dropped in by hand is not ours to delete."""
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    stray = project / "utils" / "scratch.py"
    stray.write_text("local = True\n")

    skills.materialise(root, project, [])
    assert stray.is_file()


def test_the_package_marker_is_never_overwritten(workspace):
    """It carries the project's own words, and several skills share it."""
    root, project = workspace()
    (project / "utils").mkdir()
    (project / "utils" / "__init__.py").write_text('"""Mine, and it stays."""\n')

    skills.materialise(root, project, ["figure"])
    assert (project / "utils" / "__init__.py").read_text() == '"""Mine, and it stays."""\n'


def test_the_index_describes_the_skills_and_nothing_else(workspace):
    """It used to be the project's and was never deleted.

    It is generated from the enabled skills now, so with none enabled there is
    nothing for it to describe and it goes with them. A module somebody dropped
    in by hand still survives — that one is not ours.
    """
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    assert (project / "utils" / "API.md").is_file()
    stray = project / "utils" / "scratch.py"
    stray.write_text("local = True\n")

    skills.materialise(root, project, [])
    assert not (project / "utils" / "API.md").exists()
    assert stray.is_file()


def test_what_is_enabled_is_recorded_with_its_version(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    recorded = skills.read_enabled(project)
    assert list(recorded.skills) == ["figure"]
    assert recorded.skills["figure"] == skills.read(at(root, "figure")).version


def test_re_materialising_with_no_argument_keeps_what_was_on(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["packages"])
    skills.materialise(root, project)
    assert list(skills.read_enabled(project).skills) == ["packages"]


# ------------------------------------------------------------------ conflicts


def test_two_skills_offering_one_module_are_refused(workspace):
    root, project = workspace()
    clash = clash_utils(root)
    (clash / "rig.py").write_text("R = 2\n")
    (clash_dir(root) / skills.MANIFEST_NAME).write_text(
        json.dumps({"name": "other"}) + "\n"
    )

    with pytest.raises(skills.Collision, match="rig.py"):
        skills.materialise(root, project, ["figure", "other"])


def test_two_skills_offering_one_widget_are_refused(workspace):
    root, project = workspace()
    for name in ("figure", "packages"):
        widget = at(root, name) / "widgets" / "viewer"
        widget.mkdir(parents=True)
        (widget / "widget.json").write_text(json.dumps({"name": "viewer"}))

    with pytest.raises(skills.Collision, match="viewer"):
        skills.materialise(root, project, ["figure", "packages"])


def test_a_refused_enable_writes_nothing(workspace):
    """Collisions are found before any file is touched."""
    root, project = workspace()
    clash = clash_utils(root)
    (clash / "rig.py").write_text("R = 2\n")
    (clash_dir(root) / skills.MANIFEST_NAME).write_text(
        json.dumps({"name": "other"}) + "\n"
    )

    with pytest.raises(skills.Collision):
        skills.materialise(root, project, ["figure", "other"])
    assert not (project / "utils").exists()


def test_enabling_a_skill_that_is_not_there_is_refused(workspace):
    root, project = workspace()
    with pytest.raises(skills.Collision, match="no such skill"):
        skills.materialise(root, project, ["imaginary"])


# ---------------------------------------------------------------- widget rules


def _bind(root, name, rules):
    (at(root, name) / "bind.json").write_text(json.dumps({"rules": rules}))


def test_a_skills_rules_reach_widgets_json(workspace):
    root, project = workspace()
    _bind(root, "figure", [{"widget": "pose-3d", "extensions": [".pose.json"]}])
    skills.materialise(root, project, ["figure"])

    rules = json.loads((project / "widgets.json").read_text())["rules"]
    assert rules == [
        {"widget": "pose-3d", "extensions": [".pose.json"], "skill": "figure"}
    ]


def test_a_projects_own_rule_survives_materialising(workspace):
    """A project may bind a widget itself; that is not ours to take away."""
    root, project = workspace()
    (project / "widgets.json").write_text(
        json.dumps({"rules": [{"widget": "local", "extensions": [".x"]}]})
    )
    _bind(root, "figure", [{"widget": "pose-3d", "extensions": [".pose.json"]}])
    skills.materialise(root, project, ["figure"])

    rules = json.loads((project / "widgets.json").read_text())["rules"]
    assert rules[0] == {"widget": "local", "extensions": [".x"]}
    assert rules[1]["skill"] == "figure"


def test_disabling_removes_that_skills_rules(workspace):
    root, project = workspace()
    _bind(root, "figure", [{"widget": "pose-3d", "extensions": [".pose.json"]}])
    skills.materialise(root, project, ["figure"])
    skills.materialise(root, project, [])

    assert json.loads((project / "widgets.json").read_text())["rules"] == []


def test_widgets_json_is_left_untouched_when_nothing_changes(workspace):
    """Including its `$comment`, which a rewrite would not preserve."""
    root, project = workspace()
    (project / "widgets.json").write_text(
        json.dumps({"$comment": "hand written", "rules": []}, indent=2) + "\n"
    )
    before = (project / "widgets.json").read_bytes()
    skills.materialise(root, project, ["figure"])
    assert (project / "widgets.json").read_bytes() == before


# ------------------------------------------------------- promoting a helper


def test_a_promoted_helper_reaches_the_project(workspace):
    """The whole point of step 4: write to the skill, import from the project.

    A task writes into `skills/<name>/utils/`; `resync` re-versions the skill
    and materialises it, so the next task can `import utils.measure`.
    """
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    was = skills.read(at(root, "figure")).version

    # What a task does at the end of a run, having been told to write here.
    (at(root, "figure") / "utils" / "measure.py").write_text(
        '"""Measure a thing."""\n\n\ndef measure(x):\n    return x\n'
    )

    changed = skills.resync(root, project)
    assert changed == ["figure"]
    assert (project / "utils" / "measure.py").is_file()
    assert skills.read(at(root, "figure")).version != was


def test_a_helper_written_to_the_project_does_not_survive(workspace):
    """Why the guides send writes to the skill.

    A module left in the project's `utils/` is not in any skill, so the next
    materialise takes it back — and the skill anybody would copy never had it.
    """
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    (project / "utils" / "measure.py").write_text("def measure(x): return x\n")

    # Not a module this materialise wrote, so not one it removes...
    skills.materialise(root, project, ["figure"])
    assert (project / "utils" / "measure.py").is_file()
    # ...but it is in no skill, so it reaches no other project and no copy of
    # this one. That is the trap the wording exists to keep a task out of.
    assert "measure.py" not in [m.name for m in
                                skills.read(at(root, "figure")).modules()]


def test_resync_does_nothing_when_nothing_changed(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    assert skills.resync(root, project) == []


def test_a_promotion_moves_only_the_project_that_made_it(workspace):
    """Versions coexist, so a change is not applied to projects behind your back.

    Before the version was in the directory name, publishing moved every
    project with the skill enabled at once. Now the project whose task wrote
    the helper goes to the new version and the other stays where it was, with
    both versions on disk — which is what makes moving it a decision somebody
    takes rather than one that happens to them.
    """
    root, project = workspace()
    second = root / "assets" / "other"
    second.mkdir(parents=True)
    skills.materialise(root, project, ["figure"])
    skills.materialise(root, second, ["figure"])
    was = skills.read_enabled(second).skills["figure"]

    kept = skills.snapshot(root, project, root / "snap")
    (at(root, "figure") / "utils" / "measure.py").write_text("X = 1\n")
    skills.resync(root, project, kept)

    # The project that made it has the helper and a new version.
    assert (project / "utils" / "measure.py").is_file()
    assert skills.read_enabled(project).skills["figure"] != was
    # The other is untouched, and the version it is on is still there.
    assert not (second / "utils" / "measure.py").exists()
    assert skills.read_enabled(second).skills["figure"] == was
    assert skills.find(root, "figure", was) is not None


def test_both_versions_are_on_disk_after_a_promotion(workspace):
    """The whole point of the version being in the directory name."""
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    before = skills.read_enabled(project).skills["figure"]

    kept = skills.snapshot(root, project, root / "snap")
    (at(root, "figure") / "utils" / "measure.py").write_text("X = 1\n")
    skills.resync(root, project, kept)

    versions = sorted(s.version for s in skills.all_skills(root) if s.name == "figure")
    assert len(versions) == 2, versions
    assert before in versions


def test_a_project_can_be_moved_back_to_the_older_version(workspace):
    """Migration, in the direction people actually need when something breaks."""
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    old = skills.read_enabled(project).skills["figure"]

    kept = skills.snapshot(root, project, root / "snap")
    (at(root, "figure") / "utils" / "measure.py").write_text("X = 1\n")
    skills.resync(root, project, kept)
    assert (project / "utils" / "measure.py").is_file()

    skills.materialise(root, project, ["figure"], versions={"figure": old})
    assert not (project / "utils" / "measure.py").exists()
    assert skills.read_enabled(project).skills["figure"] == old


def test_the_writable_dirs_are_the_enabled_skills(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    dirs = skills.enabled_utils_dirs(root, project)
    assert dirs == (at(root, "figure") / "utils",)


def test_a_project_with_no_skills_may_write_nowhere(workspace):
    root, project = workspace()
    assert skills.enabled_utils_dirs(root, project) == ()


# -------------------------------------------------- the design chat authoring


def test_a_fork_is_a_draft_carrying_everything(workspace):
    root, _ = workspace()
    draft = skills.fork(root, "figure")
    assert skills.is_draft(draft)
    assert draft.path.name == "draft"
    assert draft.path.parent.name == "figure"
    assert [m.name for m in draft.modules()] == ["rig.py", "skeleton.py"]
    # A copy that dropped its own manifest would not be a skill.
    assert (draft.path / skills.MANIFEST_NAME).is_file()


def test_forking_leaves_the_published_version_alone(workspace):
    """The version some project is running on is not the one being edited."""
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    published = at(root, "figure")
    before = (published / "utils" / "rig.py").read_bytes()

    draft = skills.fork(root, "figure")
    (draft.utils_dir / "rig.py").write_text("R = 999\n")

    assert (published / "utils" / "rig.py").read_bytes() == before


def test_forking_a_name_nobody_has_yet_starts_an_empty_skill(workspace):
    root, _ = workspace()
    draft = skills.fork(root, "brand-new")
    assert draft.modules() == []
    assert draft.utils_dir.is_dir()


def test_adopting_publishes_the_draft(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    draft = skills.fork(root, "figure")
    (draft.utils_dir / "measure.py").write_text("X = 1\n")

    done = skills.adopt(root, root / "assets")
    assert len(done) == 1
    name, version, moved = done[0]
    assert name == "figure"
    assert not skills.is_draft(skills.find(root, "figure", version))
    assert moved == ["demo"]
    assert (project / "utils" / "measure.py").is_file()


def test_adopting_moves_every_project_on_that_skill(workspace):
    """The design chat's half: authoring is deliberate, so everybody moves.

    A task promoting a helper moves only its own project — it was doing that
    in passing. A design turn changed the skill on purpose and tested it, so
    leaving projects behind would mean the thing just designed runs nowhere.
    """
    root, project = workspace()
    second = root / "assets" / "other"
    second.mkdir(parents=True)
    skills.materialise(root, project, ["figure"])
    skills.materialise(root, second, ["figure"])

    draft = skills.fork(root, "figure")
    (draft.utils_dir / "measure.py").write_text("X = 1\n")
    _, version, moved = skills.adopt(root, root / "assets")[0]

    assert sorted(moved) == ["demo", "other"]
    for p in (project, second):
        assert (p / "utils" / "measure.py").is_file()
        assert skills.read_enabled(p).skills["figure"] == version


def test_a_project_without_the_skill_is_not_given_it(workspace):
    root, project = workspace()
    bystander = root / "assets" / "bystander"
    bystander.mkdir(parents=True)
    skills.materialise(root, project, ["figure"])

    draft = skills.fork(root, "figure")
    (draft.utils_dir / "measure.py").write_text("X = 1\n")
    _, _, moved = skills.adopt(root, root / "assets")[0]

    assert moved == ["demo"]
    assert not (bystander / "utils").exists()


def test_adopting_a_brand_new_skill_moves_nobody(workspace):
    """Nothing is on it yet; an admin ticks it on."""
    root, _ = workspace()
    draft = skills.fork(root, "brand-new")
    (draft.utils_dir / "thing.py").write_text("X = 1\n")

    name, version, moved = skills.adopt(root, root / "assets")[0]
    assert (name, moved) == ("brand-new", [])
    assert skills.find(root, "brand-new", version) is not None


def test_nothing_to_adopt_is_not_an_error(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    assert skills.adopt(root, root / "assets") == []


def test_a_published_version_is_never_a_draft(workspace):
    root, _ = workspace()
    assert not skills.is_draft(skills.find(root, "figure"))
    assert skills.drafts(root) == []


def test_build_artefacts_never_reach_a_published_version(workspace):
    """A rename takes the whole directory, `__pycache__` included."""
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    draft = skills.fork(root, "figure")
    cache = draft.utils_dir / "__pycache__"
    cache.mkdir()
    (cache / "rig.cpython-312.pyc").write_bytes(b"\x00")

    _, version, _ = skills.adopt(root, root / "assets")[0]
    published = skills.find(root, "figure", version)
    assert not (published.utils_dir / "__pycache__").exists()


# ---------------------------------------------------- the index per skill


def test_a_skill_carries_its_own_index(workspace):
    """So it arrives at another install already describing itself."""
    root, _ = workspace()
    skill = skills.find(root, "figure")
    assert skill.index.is_file()
    body = skill.index.read_text()
    assert "## `rig`" in body
    # No attribution inside a skill: everything in it came from it.
    assert "from `" not in body


def test_the_index_is_not_a_module(workspace):
    """Two skills would otherwise collide on `API.md`, and it is not importable."""
    root, project = workspace()
    assert "API.md" not in [m.name for m in skills.find(root, "figure").modules()]
    skills.materialise(root, project, ["figure", "packages"])
    assert (project / "utils" / "API.md").is_file()


def test_the_projects_index_combines_its_skills(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure", "packages"])
    body = (project / "utils" / "API.md").read_text()
    assert "## `rig` — from `figure`" in body
    assert "## `manifest` — from `packages`" in body


def test_the_index_follows_a_promotion(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    kept = skills.snapshot(root, project, root / "snap")
    (at(root, "figure") / "utils" / "measure.py").write_text(
        '"""Measure a thing."""\n\n\ndef measure(x):\n    return x\n'
    )
    skills.resync(root, project, kept)
    assert "## `measure` — from `figure`" in (project / "utils" / "API.md").read_text()


def test_indexing_does_not_chase_its_own_tail(workspace):
    """The index is inside the skill and inside the hash.

    Generated before the hash is taken, so a published skill is internally
    consistent and publishing twice changes nothing — the mistake `dist/` made
    the hard way.
    """
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    skill = skills.find(root, "figure")
    assert skills.fingerprint(skill.path) == skill.version
    skills.publish(skill)
    assert skills.fingerprint(skill.path) == skill.version


def test_disabling_takes_the_index_back_with_the_modules(workspace):
    root, project = workspace()
    skills.materialise(root, project, ["figure"])
    skills.materialise(root, project, [])
    assert not (project / "utils" / "API.md").exists()
