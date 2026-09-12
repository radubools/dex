"""The generated index of a project's shared utilities.

Tasks used to read every module in `utils/` to find out what was there. Yoga's
went from nothing to 21.9 KB in a quarter of an hour, and every task that
glanced at it paid for the whole directory. This is the digest they read
instead, and the point of generating it is that it cannot drift.
"""

from __future__ import annotations

from pathlib import Path

from dex.tools.utils_api import render, write

MODULE = '''"""Checks every pose package needs before it is done."""

LANDMARKS = ["A", "B"]
_PRIVATE = 1


def check_limb_symmetry(pose, tolerance=0.001):
    """Left and right limb lengths agree to within `tolerance` meters.

    A second paragraph that belongs in the source, not in the index.
    """
    return [pose, tolerance]


def _helper(x):
    """Not offered to anyone."""
    return x
'''


def utils(tmp_path: Path, **modules: str) -> Path:
    directory = tmp_path / "utils"
    directory.mkdir()
    (directory / "__init__.py").write_text("")
    for name, source in modules.items():
        (directory / f"{name}.py").write_text(source)
    return tmp_path


def test_the_index_carries_the_interface_and_not_the_bodies(tmp_path: Path):
    project = utils(tmp_path, skeleton=MODULE)
    text = render(project / "utils")

    assert "## `skeleton`" in text
    assert "Checks every pose package needs before it is done." in text
    assert "`check_limb_symmetry(pose, tolerance=0.001)`" in text
    assert "Left and right limb lengths agree" in text
    # One line of the docstring, never the rest, and never the body.
    assert "second paragraph" not in text
    assert "return [pose, tolerance]" not in text
    # Public constants are part of the interface; underscored names are not.
    assert "`LANDMARKS`" in text
    assert "_PRIVATE" not in text
    assert "_helper" not in text


def test_the_index_is_a_fraction_of_the_source(tmp_path: Path):
    """The whole reason it exists: reading it must be cheaper than the modules.

    A real module is mostly body — yoga's three came to 21.9 KB against a
    2.9 KB index. The bodies here stand in for that.
    """
    body = "\n".join(f"    step_{n} = {n} * 2  # a line of real implementation" for n in range(40))
    fat = "\n\n".join(
        f'"""Module {name}."""' if name == "head" else
        f'def work_{name}(value, option=None):\n    """Do the {name} part."""\n{body}\n    return value'
        for name in ("head", "one", "two", "three")
    )
    project = utils(tmp_path, a=fat, b=fat, c=fat)
    source = sum(p.stat().st_size for p in (project / "utils").glob("*.py"))
    index = len(render(project / "utils"))
    assert index * 4 < source, f"index {index} vs source {source}"


def test_writing_is_idempotent_so_a_task_start_is_not_an_asset_event(tmp_path: Path):
    project = utils(tmp_path, skeleton=MODULE)
    assert write(project) is True
    assert write(project) is False
    assert (project / "utils" / "API.md").exists()


def test_a_changed_module_shows_up_without_anyone_maintaining_a_table(tmp_path: Path):
    """A hand-kept index drifts the moment a module changes. This one cannot."""
    project = utils(tmp_path, skeleton=MODULE)
    write(project)
    (project / "utils" / "skeleton.py").write_text(
        MODULE + '\n\ndef check_origin(pose):\n    """The origin sits below the hips."""\n    return []\n'
    )
    assert write(project) is True
    assert "check_origin" in (project / "utils" / "API.md").read_text()


def test_a_project_without_utils_is_left_alone(tmp_path: Path):
    assert write(tmp_path) is False
    assert not (tmp_path / "utils").exists()


def test_a_module_that_does_not_parse_does_not_take_the_index_down(tmp_path: Path):
    project = utils(tmp_path, good=MODULE, broken="def oops(:\n")
    text = render(project / "utils")
    assert "check_limb_symmetry" in text
    assert "Could not be parsed" in text


def test_check_mode_reports_without_writing(tmp_path: Path):
    project = utils(tmp_path, skeleton=MODULE)
    assert write(project, check=True) is True
    assert not (project / "utils" / "API.md").exists()


ROSTER_GUIDE = """# Project: demo

### Shared utilities

<!-- utils:begin -->
<!-- Rows come from each module's first docstring line. Change the docstring, not the table. -->

| Module | What it does |
|---|---|
| _(none yet)_ | |

<!-- utils:end -->

## Conventions

Unchanged text below the roster.
"""


def test_the_roster_in_the_guide_is_kept_in_step_with_the_modules(tmp_path: Path):
    """The roster is in every brief, so a stale one misleads every task."""
    project = utils(tmp_path, skeleton=MODULE)
    (project / "AGENTS.md").write_text(ROSTER_GUIDE)

    assert write(project) is True
    guide = (project / "AGENTS.md").read_text()
    assert "| `skeleton` | Checks every pose package needs before it is done. |" in guide
    assert "_(none yet)_" not in guide
    # Only the marked block moves; the rest of the guide is untouched.
    assert "# Project: demo" in guide
    assert "Unchanged text below the roster." in guide
    # And it settles.
    assert write(project) is False


def test_a_module_removed_leaves_the_roster_honest(tmp_path: Path):
    project = utils(tmp_path, skeleton=MODULE)
    (project / "AGENTS.md").write_text(ROSTER_GUIDE)
    write(project)

    (project / "utils" / "skeleton.py").unlink()
    assert write(project) is True
    guide = (project / "AGENTS.md").read_text()
    assert "skeleton" not in guide
    assert "_(none yet)_" in guide
    # The fuller index goes with it rather than outliving the modules.
    assert not (project / "utils" / "API.md").exists()


def test_a_guide_without_the_markers_is_never_guessed_at(tmp_path: Path):
    """A hand-edited guide that did not opt in must come out byte for byte."""
    project = utils(tmp_path, skeleton=MODULE)
    plain = "# Project: demo\n\nNo roster here.\n"
    (project / "AGENTS.md").write_text(plain)

    write(project)
    assert (project / "AGENTS.md").read_text() == plain
    # The generated index is still written; only the guide is left alone.
    assert (project / "utils" / "API.md").exists()


def test_a_module_without_a_docstring_says_so_rather_than_going_blank(tmp_path: Path):
    project = utils(tmp_path, bare="def f():\n    return 1\n")
    (project / "AGENTS.md").write_text(ROSTER_GUIDE)
    write(project)
    assert "give the module a docstring" in (project / "AGENTS.md").read_text()


def test_check_mode_reports_a_stale_roster_without_touching_it(tmp_path: Path):
    project = utils(tmp_path, skeleton=MODULE)
    (project / "AGENTS.md").write_text(ROSTER_GUIDE)
    assert write(project, check=True) is True
    assert "_(none yet)_" in (project / "AGENTS.md").read_text()
