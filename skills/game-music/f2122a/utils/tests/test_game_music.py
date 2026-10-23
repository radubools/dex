"""Checks for game-music: in-key composition, exact click-free loops, additive stems, stingers, packages."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from utils import audio as A  # noqa: E402
from utils import catalog, compose  # noqa: E402
from utils import instruments as I  # noqa: E402


def test_instruments_tuned_and_finite() -> None:
    for name in I.PITCHED:
        x = I.note(name, 57, 0.4, 0.8)
        assert np.isfinite(x).all() and np.abs(x).max() > 0.01, name
        assert abs(x[0]) < 1e-3 and abs(x[-1]) < 1e-3, name
    x = I.note("pluck", 69, 0.5)[: A.n_samples(0.5)]
    f = np.fft.rfftfreq(len(x) * 4, 1 / A.SR)
    assert abs(f[np.argmax(np.abs(np.fft.rfft(x * np.hanning(len(x)), len(x) * 4)))] - 440) < 2
    for name in I.DRUMS:
        h = I.hit(name, 0.8)
        assert np.isfinite(h).all() and np.abs(h).max() > 0.01, name


@pytest.mark.parametrize("mood", list(compose.MOODS))
def test_compose_in_key_and_seeded(mood: str) -> None:
    a = compose.compose_song(mood, seed=3)
    assert compose.song_dict(a) == compose.song_dict(compose.compose_song(mood, seed=3))
    assert compose.song_dict(a)["stems"] != compose.song_dict(compose.compose_song(mood, seed=4))["stems"]
    key = compose.Key(a.root, a.scale)
    allowed = {(key.root + s) % 12 for s in key.steps}
    total = a.bars * a.meter
    for stem in compose.STEMS:
        notes = a.stems[stem]
        assert notes, stem
        for n in notes:
            assert 0 <= n.beat < total
            if not n.drum:
                assert int(n.midi) % 12 in allowed, (stem, n)


@pytest.mark.parametrize("mood,bars", [("combat", 4), ("autumn", 4)])
def test_loop_exact_seamless_and_stems_add_up(mood: str, bars: int) -> None:
    song = compose.compose_song(mood, bars=bars, seed=1)
    mix, stems = compose.render_song(song)
    expect = int(round(bars * song.meter * 60 / song.bpm * A.SR))
    assert len(mix) == song.loop_samples == expect
    assert all(len(s) == expect for s in stems.values())
    assert A.seam(mix)["ok"], A.seam(mix)
    for s in stems.values():
        assert A.seam(s)["ok"]
    assert np.allclose(mix, sum(stems[s] for s in compose.STEMS))
    acc = np.zeros_like(mix)
    for s in compose.STEMS:  # every layer combination the game plays stays under the ceiling
        acc = acc + stems[s]
        assert A.peak_db(acc) <= compose.CEILING_DB
    assert -19.0 <= A.loudness(mix) <= compose.MUSIC_LUFS + 0.3
    assert A.loudness(stems["calm"]) > A.loudness(stems["tension"])
    # Played twice back to back there is no discontinuity at the join.
    twice = np.concatenate([mix, mix])
    j = len(mix)
    steps = np.abs(np.diff(twice[j - 200:j + 200], axis=0)).max()
    assert steps <= np.percentile(np.abs(np.diff(mix, axis=0)), 99.99) * 1.5


@pytest.mark.parametrize("kind", compose.STINGERS)
def test_stingers(kind: str) -> None:
    x = compose.stinger(kind)
    m = A.measure(x)
    assert 1.0 < m["duration"] < 10.0
    assert m["peak_db"] <= compose.CEILING_DB + 1e-6 and m["clipped"] == 0
    assert abs(x[0]).max() < 1e-3 and abs(x[-1]).max() < 1e-3


def test_midi_file(tmp_path: Path) -> None:
    song = compose.compose_song("title", bars=4)
    p = compose.write_midi(tmp_path / "t.mid", song)
    data = p.read_bytes()
    assert data[:4] == b"MThd"
    ntracks = int.from_bytes(data[10:12], "big")
    assert ntracks >= 4 and data.count(b"MTrk") == ntracks


def test_build_track_package(tmp_path: Path) -> None:
    pkg = tmp_path / "boss-theme"
    rep = compose.build_track("boss-theme", pkg, mood="boss", bars=4)
    assert rep["ok"], rep["errors"]
    man = json.loads((pkg / "manifest.json").read_text())
    assert man["kind"] == "music" and man["loop"] and man["bars"] == 4 and man["mood"] == "boss"
    assert set(man["stems"]) == set(compose.STEMS)
    assert man["loop_end"] == int(round(4 * 4 * 60 / man["bpm"] * A.SR))
    for s in man["stems"].values():
        assert (pkg / s["wav"]).is_file() and (pkg / s["ogg"]).is_file()
    w = A.read_wav(pkg / "boss-theme.wav")
    assert len(w) == man["loop_end"] and A.seam(w)["ok"]
    if A.ffmpeg_path():
        assert len(A.decode(pkg / "boss-theme.ogg")) == man["loop_end"]


def test_build_stinger_package(tmp_path: Path) -> None:
    pkg = tmp_path / "stinger-victory"
    rep = compose.build_stinger("stinger-victory", pkg, "victory")
    assert rep["ok"], rep["errors"]
    assert rep["warnings"] == []  # duration in the manifest matches the file
    man = catalog.read_manifest(pkg)
    assert man["loop"] is False and man["stinger"] == "victory"
