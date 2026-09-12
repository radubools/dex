from pathlib import Path

from dex.diffs import build_patch, preview_change


def test_write_to_a_new_file_is_all_additions(tmp_path: Path):
    change = preview_change("Write", {"file_path": str(tmp_path / "s.py"), "content": "a\nb\n"}, tmp_path)
    assert change is not None
    assert (change.additions, change.deletions) == (2, 0)
    assert change.path == "s.py"


def test_edit_produces_a_patch_against_the_file_on_disk(tmp_path: Path):
    target = tmp_path / "s.py"
    target.write_text("x = 1\ny = 2\n")
    change = preview_change(
        "Edit", {"file_path": str(target), "old_string": "y = 2", "new_string": "y = 3"}, tmp_path
    )
    assert change is not None
    assert (change.additions, change.deletions) == (1, 1)
    assert "-y = 2" in change.patch and "+y = 3" in change.patch


def test_edit_replaces_only_the_first_match_unless_asked(tmp_path: Path):
    target = tmp_path / "s.py"
    target.write_text("a\na\n")
    once = preview_change("Edit", {"file_path": str(target), "old_string": "a", "new_string": "b"}, tmp_path)
    assert once is not None and once.additions == 1

    every = preview_change(
        "Edit", {"file_path": str(target), "old_string": "a", "new_string": "b", "replace_all": True}, tmp_path
    )
    assert every is not None and every.additions == 2


def test_multiedit_applies_every_edit(tmp_path: Path):
    target = tmp_path / "s.py"
    target.write_text("one\ntwo\n")
    change = preview_change(
        "MultiEdit",
        {"file_path": str(target), "edits": [
            {"old_string": "one", "new_string": "1"},
            {"old_string": "two", "new_string": "2"},
        ]},
        tmp_path,
    )
    assert change is not None
    assert change.additions == 2 and change.deletions == 2


def test_no_op_edits_and_non_file_tools_produce_nothing(tmp_path: Path):
    target = tmp_path / "s.py"
    target.write_text("same\n")
    assert preview_change("Edit", {"file_path": str(target), "old_string": "same", "new_string": "same"}, tmp_path) is None
    assert preview_change("Bash", {"command": "ls"}, tmp_path) is None
    assert preview_change("Write", {}, tmp_path) is None


def test_paths_outside_the_root_keep_their_absolute_form(tmp_path: Path):
    change = build_patch(Path("/elsewhere/x.py"), "", "new\n", tmp_path)
    assert change.path == "/elsewhere/x.py"
