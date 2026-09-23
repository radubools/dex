"""Linking a local directory into a project, and keeping it read-only."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from dex import local_sources as ls
from dex.permissions import PermissionPolicy


@pytest.fixture
def machine(tmp_path: Path):
    """A workspace, a project's data directory, and a volume somewhere else."""
    workspace = tmp_path / "dex"
    datasets = workspace / "datasets" / "edil"
    datasets.mkdir(parents=True)
    external = tmp_path / "Volumes" / "external" / "eDIL_PACS_Projects_Mass-Load"
    (external / "batch-01").mkdir(parents=True)
    for i in range(4):
        (external / f"scan-{i}.dcm").write_text("x")
    return workspace, datasets, external


def test_linking_a_directory_makes_it_listable_through_the_link(machine):
    """The point of the link is that a task can read the material through it."""
    workspace, datasets, external = machine

    link = ls.link(datasets, str(external), workspace)

    assert link.path.is_symlink()
    assert link.path.parent == datasets
    assert link.target == external.resolve()
    # Listed through the link, not the target — that is what was just created.
    assert sorted(p.name for p in link.path.iterdir()) == [
        "batch-01", "scan-0.dcm", "scan-1.dcm", "scan-2.dcm", "scan-3.dcm",
    ]
    assert link.entries == 5
    assert link.readable and not link.partial
    assert (external / "scan-0.dcm").read_text() == (link.path / "scan-0.dcm").read_text()


def test_the_link_is_named_after_the_directory(machine):
    workspace, datasets, external = machine
    assert ls.link(datasets, str(external), workspace).name == "eDIL_PACS_Projects_Mass-Load"


def test_a_name_that_would_not_survive_a_path_is_cleaned(tmp_path: Path):
    workspace = tmp_path / "dex"
    datasets = workspace / "datasets" / "p"
    datasets.mkdir(parents=True)
    awkward = tmp_path / "vol" / "Scans 2024 (final)/"
    awkward.mkdir(parents=True)

    link = ls.link(datasets, str(awkward), workspace)

    assert link.name == "Scans-2024-final"
    assert link.path.is_symlink()


def test_linking_the_same_directory_twice_is_one_link(machine):
    """Asking again for the same path is a repeat, not a second source."""
    workspace, datasets, external = machine
    first = ls.link(datasets, str(external), workspace)
    again = ls.link(datasets, str(external), workspace)

    assert again.name == first.name
    assert len(ls.links(datasets)) == 1


def test_two_different_directories_with_one_name_do_not_collide(tmp_path: Path):
    workspace = tmp_path / "dex"
    datasets = workspace / "datasets" / "p"
    datasets.mkdir(parents=True)
    for parent in ("a", "b"):
        (tmp_path / "vol" / parent / "scans").mkdir(parents=True)

    first = ls.link(datasets, str(tmp_path / "vol" / "a" / "scans"), workspace)
    second = ls.link(datasets, str(tmp_path / "vol" / "b" / "scans"), workspace)

    assert (first.name, second.name) == ("scans", "scans-2")
    assert first.target != second.target


def test_a_link_that_cannot_be_read_is_not_left_behind(tmp_path: Path, monkeypatch):
    """Worse than no link, because it looks like access and fails inside a task."""
    workspace = tmp_path / "dex"
    datasets = workspace / "datasets" / "p"
    datasets.mkdir(parents=True)
    volume = tmp_path / "vol" / "scans"
    volume.mkdir(parents=True)

    # Passes `check`, then cannot be listed — an unmounted volume behaves this
    # way: the mount point is there and the contents are not.
    monkeypatch.setattr(ls, "verify", lambda _p: (False, 0, False, []))
    with pytest.raises(ls.Blocked, match="nothing can be read"):
        ls.link(datasets, str(volume), workspace)

    assert list(datasets.iterdir()) == []


def test_a_broken_link_is_still_listed(machine):
    """A task told nothing is there reports a missing file.

    One told the link is broken says something the operator can act on.
    """
    workspace, datasets, external = machine
    link = ls.link(datasets, str(external), workspace)
    import shutil

    shutil.rmtree(external)  # the volume was unplugged

    listed = ls.links(datasets)
    assert [l.name for l in listed] == [link.name]
    assert listed[0].readable is False


def test_counting_stops_rather_than_walking_an_enormous_volume(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "dex"
    datasets = workspace / "datasets" / "p"
    datasets.mkdir(parents=True)
    volume = tmp_path / "vol" / "scans"
    volume.mkdir(parents=True)
    monkeypatch.setattr(ls, "COUNT_LIMIT", 10)
    for i in range(25):
        (volume / f"f{i}").write_text("x")

    link = ls.link(datasets, str(volume), workspace)

    assert link.partial and link.entries == 10
    assert len(link.sample) == ls.SAMPLE
    assert "10+" in link.describe()


# --------------------------------------------------------------- refusals


def test_paths_that_are_refused(machine):
    workspace, _, external = machine
    home = Path.home()
    for raw, why in (
        ("", "No path"),
        ("relative/path", "relative"),
        (str(external / "scan-0.dcm"), "not a directory"),
        (str(external.parent / "missing"), "cannot be reached"),
        (str(home), "whole home directory"),
        ("/", "whole filesystem"),
        ("/etc", "will not link"),
        (str(home / ".ssh"), "will not link"),
    ):
        with pytest.raises(ls.Blocked, match=why):
            ls.check(raw, workspace)


def test_a_symlink_pointing_somewhere_denied_is_refused(tmp_path: Path):
    """Every check runs on the resolved path, or the name is the whole guard."""
    workspace = tmp_path / "dex"
    workspace.mkdir()
    innocent = tmp_path / "looks-fine"
    innocent.symlink_to("/etc", target_is_directory=True)

    with pytest.raises(ls.Blocked, match="will not link"):
        ls.check(str(innocent), workspace)


def test_dex_s_own_workspace_cannot_be_linked_into_itself(tmp_path: Path):
    """A walker following that link would not stop."""
    workspace = tmp_path / "dex"
    (workspace / "assets" / "edil").mkdir(parents=True)
    datasets = workspace / "datasets" / "edil"
    datasets.mkdir(parents=True)

    for inside in (workspace, workspace / "assets", datasets, tmp_path):
        with pytest.raises(ls.Blocked, match="overlaps dex's own workspace"):
            ls.check(str(inside), workspace)


def test_unlinking_leaves_the_directory_alone(machine):
    workspace, datasets, external = machine
    link = ls.link(datasets, str(external), workspace)

    assert ls.unlink(datasets, link.name) is True
    assert not link.path.exists()
    assert external.is_dir() and (external / "scan-0.dcm").exists()

    assert ls.unlink(datasets, link.name) is False
    assert ls.unlink(datasets, "../../escape") is False


def test_unlink_refuses_a_real_directory(machine):
    """Only links go. An uploaded file is not dex's to delete here."""
    _, datasets, _ = machine
    (datasets / "upload.pdf").write_text("x")
    (datasets / "a-real-dir").mkdir()

    assert ls.unlink(datasets, "upload.pdf") is False
    assert ls.unlink(datasets, "a-real-dir") is False
    assert (datasets / "upload.pdf").exists()


# ----------------------------------------------------- read-only in the policy


async def decide(policy, tool, data):
    return await policy.build()(tool, data, ToolPermissionContext())


def _policy(workspace: Path, datasets: Path, task_dir: Path, escalations: list):
    async def escalate(_id, tool, _input, title):
        escalations.append(f"{tool}: {title}")
        # What `auto_approve` does. A rule that only escalates is not a rule.
        return "allow"

    return PermissionPolicy(
        workspace=workspace, task_dir=task_dir,
        extra_writable=(datasets,),
        read_only=ls.targets(datasets),
        escalate=escalate,
    )


async def test_a_task_may_read_a_linked_source(machine):
    workspace, datasets, external = machine
    link = ls.link(datasets, str(external), workspace)
    task_dir = workspace / "assets" / "edil" / "batch"
    task_dir.mkdir(parents=True)
    escalations: list[str] = []

    result = await decide(
        _policy(workspace, datasets, task_dir, escalations),
        "Read", {"file_path": str(link.path / "scan-0.dcm")},
    )
    assert isinstance(result, PermissionResultAllow)
    assert escalations == []


async def test_a_task_may_not_write_into_a_linked_source(machine):
    """Refused outright, and not because the path looks foreign.

    The link sits inside the data directory, which *is* writable — so the
    check has to resolve the path to see where it actually lands.
    """
    workspace, datasets, external = machine
    link = ls.link(datasets, str(external), workspace)
    task_dir = workspace / "assets" / "edil" / "batch"
    task_dir.mkdir(parents=True)
    escalations: list[str] = []
    policy = _policy(workspace, datasets, task_dir, escalations)

    for path in (
        link.path / "scan-0.dcm",            # through the link
        link.path / "batch-01" / "new.dcm",  # deeper through the link
        external / "scan-0.dcm",             # the target, named directly
    ):
        result = await decide(policy, "Write", {"file_path": str(path)})
        assert isinstance(result, PermissionResultDeny), path
        assert "read, not" in result.message
    assert escalations == []

    # And the data directory itself is still writable, which is the point of
    # the check running on the resolved path rather than the prefix.
    ok = await decide(policy, "Write", {"file_path": str(datasets / "index.json")})
    assert isinstance(ok, PermissionResultAllow)
    assert escalations == []


async def test_linking_asks_the_operator(tmp_path: Path):
    """It grants standing access to a tree dex does not own.

    Every other `mcp__dex__` tool is auto-approved; this one is a decision.
    """
    task_dir = tmp_path / "assets" / "p" / "t"
    task_dir.mkdir(parents=True)
    escalations: list[str] = []
    policy = _policy(tmp_path, tmp_path / "datasets" / "p", task_dir, escalations)

    result = await decide(
        policy, "mcp__dex__link_local_source", {"path": "/Volumes/external/scans"}
    )
    assert isinstance(result, PermissionResultAllow)  # the fixture approves
    assert len(escalations) == 1
    assert "links a directory outside dex" in escalations[0]

    # The ordinary ones are not touched.
    escalations.clear()
    await decide(policy, "mcp__dex__ask_user", {"question": "?"})
    assert escalations == []
