"""Build a package's self-contained player.html from its score sidecar."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pretty_midi

# The player's markup lives beside this module. It reads track count, clefs,
# bar length and tempo out of the MIDI, so it suits any piece without editing.
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "player_template.html"


def build_player(sidecar_path, out_path=None, subtitle: str = "",
                 template=DEFAULT_TEMPLATE) -> Path:
    """Write a standalone player.html beside a .score.json, with its MIDI inlined."""
    sidecar_path = Path(sidecar_path)
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    midi_path = sidecar_path.parent / sidecar["midi"]

    # Parsing it first means a MIDI the player could not read never ships.
    pretty_midi.PrettyMIDI(str(midi_path))

    html = Path(template).read_text(encoding="utf-8")
    for placeholder, value in (
        ("__TITLE__", sidecar["title"]),
        ("__SUBTITLE__", subtitle),
        ("__AUDIO__", sidecar["audio"]),
        ("__MIDI_BASE64__", base64.b64encode(midi_path.read_bytes()).decode("ascii")),
    ):
        html = html.replace(placeholder, value)

    out_path = Path(out_path) if out_path else sidecar_path.parent / "player.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
