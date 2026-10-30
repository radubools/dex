"""player.html draws the key signature and the accidentals its notes need.

Builds small players from MIDI written here, opens them in headless Chromium
(player_spelling.mjs) and reads back what was drawn.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pretty_midi
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "utils"))
import player  # noqa: E402

# Playwright is a node package of dex's own checkout.
NODE_MODULES = Path.home() / "dex" / "node_modules"


def _write(directory: Path, slug: str, pitches: list[int], key: str | None) -> Path:
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    if key:
        pm.key_signature_changes.append(
            pretty_midi.KeySignature(pretty_midi.key_name_to_key_number(key), 0))
    inst = pretty_midi.Instrument(0, name=slug)
    for i, pitch in enumerate(pitches):
        inst.notes.append(pretty_midi.Note(80, pitch, i * 0.5, i * 0.5 + 0.45))
    pm.instruments.append(inst)
    pm.write(str(directory / f"{slug}.mid"))
    sidecar = directory / f"{slug}.score.json"
    sidecar.write_text(json.dumps({"title": slug, "midi": f"{slug}.mid", "audio": f"{slug}.wav"}))
    return player.build_player(sidecar, directory / f"{slug}.html")


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    if not shutil.which("node") or not (NODE_MODULES / "playwright").is_dir():
        pytest.skip("needs node and playwright")
    directory = tmp_path_factory.mktemp("players")
    pages = {
        # D major, declared in the file: F# and C# need no sign of their own.
        "d_major": _write(directory, "d_major", [62, 64, 66, 67, 69, 71, 73, 74] * 2, "D Major"),
        # Bb major, undeclared: the key is inferred. Bar 3 has E natural, Eb
        # (the flat back) and E natural; bar 4 Eb (no sign, new bar) and Gb.
        "b_flat": _write(directory, "b_flat",
                         [70, 72, 74, 75, 77, 79, 81, 82, 76, 75, 76, 74, 75, 78, 70, 70], None),
        "c_major": _write(directory, "c_major", [60, 62, 64, 65, 67, 69, 71, 72], None),
    }
    out = subprocess.run(
        ["node", str(HERE / "player_spelling.mjs"), *map(str, pages.values())],
        capture_output=True, text=True, timeout=120,
        env={"NODE_PATH": str(NODE_MODULES), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    assert out.returncode == 0, out.stderr
    return dict(zip(pages, json.loads(out.stdout)))


def test_declared_key_is_drawn_and_covers_its_notes(drawn):
    page = drawn["d_major"]
    assert page["errors"] == []
    assert page["keySf"] == 2 and page["inferred"] == 2
    assert page["keySigns"] == ["sharp", "sharp"]
    # F# and C# sit on F and C, with no sign beside them.
    letters = {p % 12: s % 7 for p, s in page["steps"]}
    assert letters[6] == 3 and letters[1] == 0
    assert page["noteSigns"] == []


def test_undeclared_key_is_inferred_and_accidentals_last_a_bar(drawn):
    page = drawn["b_flat"]
    assert page["errors"] == []
    assert page["keySf"] == -2
    assert page["keySigns"] == ["flat", "flat"]
    assert page["noteSigns"] == ["natural", "flat", "natural", "flat"]
    assert page["misplaced"] == 0
    # The F# is written as G flat in a flat key.
    assert dict(page["steps"])[78] % 7 == 4


def test_c_major_has_no_signs(drawn):
    page = drawn["c_major"]
    assert page["keySf"] == 0
    assert page["keySigns"] == [] and page["noteSigns"] == []
