"""Checks for game-sfx: DSP sanity, every generator renders cleanly, loops are seamless, packages validate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from utils import audio as A  # noqa: E402
from utils import catalog, sfx  # noqa: E402


def _peak_hz(x: np.ndarray) -> float:
    m = A.mono(x)
    f = np.fft.rfftfreq(len(m) * 4, 1 / A.SR)
    return float(f[np.argmax(np.abs(np.fft.rfft(m * np.hanning(len(m)), len(m) * 4)))])


def test_oscillators_and_noise() -> None:
    for shape in ("sine", "triangle", "saw", "square"):
        x = A.osc(440, 0.5, shape)
        assert len(x) == A.n_samples(0.5)
        assert abs(_peak_hz(x) - 440) < 2
        assert np.abs(x).max() < 1.3
    for color in ("white", "pink", "brown"):
        n = A.noise(1.0, color, A.rng(1))
        assert abs(np.sqrt(np.mean(n ** 2)) - 0.25) < 1e-6
    assert A.centroid(A.noise(1, "brown")) < A.centroid(A.noise(1, "pink")) < A.centroid(A.noise(1, "white"))
    assert np.allclose(A.noise(0.2, "white", A.rng(3, "x")), A.noise(0.2, "white", A.rng(3, "x")))


def test_loudness_reference() -> None:
    """BS.1770: a 0 dBFS 1 kHz sine on both channels reads about 0 LUFS; -20 dBFS reads about -20."""
    s = A.stereo(A.osc(1000, 3.0))
    assert abs(A.loudness(s)) < 0.3
    assert abs(A.loudness(s * A.db_to_amp(-20)) + 20) < 0.3
    assert A.loudness(np.zeros((1000, 2))) == -120.0


def test_limiter_and_master() -> None:
    x = A.noise(1.0, "white", A.rng(2)) * 8
    y = A.limit(x, -1.0)
    assert A.peak_db(y) <= -1.0 + 1e-6
    m = A.master(A.osc(220, 1.0) * A.perc(1.0, 0.01, 0.3), lufs=-18, ceiling_db=-3)
    assert A.peak_db(m) <= -3.0 + 1e-6
    assert abs(m[0]).max() < 1e-3 and abs(m[-1]).max() < 1e-3  # faded ends: no clicks


def test_filters_shape_spectrum() -> None:
    n = A.noise(1.0, "white", A.rng(4))
    assert A.centroid(A.lowpass(n, 500)) < 1500 < A.centroid(A.highpass(n, 5000))
    swept = A.lowpass(n, A.sweep(200, 8000, 1.0))
    assert A.centroid(swept[: A.SR // 4]) < A.centroid(swept[-A.SR // 4:])
    assert abs(_peak_hz(A.resonate(n, 1200, 30)) - 1200) < 60


def test_loops_are_seamless() -> None:
    raw = A.lowpass(A.noise(3.0, "pink", A.rng(5)), 2000)
    loop = A.make_loop(raw, A.n_samples(2.0), 0.5)
    assert len(loop) == A.n_samples(2.0)
    assert A.seam(loop)["ok"]
    assert not A.seam(A.osc(100, 1.0) + np.linspace(0, 1, A.SR))["ok"]  # a ramp leaves a jump
    buf = np.zeros((A.SR, 2))
    A.place(buf, A.osc(300, 0.5) * A.perc(0.5, 0.001, 0.2), A.SR - 1000, wrap=True)
    assert np.abs(buf[:100]).max() > 0  # the tail wrapped round to the start


def test_wav_ogg_roundtrip(tmp_path: Path) -> None:
    x = A.pan(A.osc(440, 0.5) * 0.5, 0.3)
    wav = A.write_wav(tmp_path / "t.wav", x)
    y = A.read_wav(wav)
    assert y.shape == x.shape and np.abs(y - x).max() < 2e-4
    assert abs(catalog.wav_duration(wav) - 0.5) < 1e-3
    if A.ffmpeg_path() is None:
        pytest.skip("no ffmpeg")
    ogg = A.encode_ogg(wav)
    assert ogg.stat().st_size > 200
    # ffmpeg's decoder trims/pads very short Opus files by up to a frame (the stream's granule is exact).
    assert abs(len(A.decode(ogg)) - len(x)) <= 1024


@pytest.mark.parametrize("name", list(sfx.SOUNDS))
def test_every_sound_renders_cleanly(name: str) -> None:
    s = sfx.SOUNDS[name]
    cat = sfx.CATEGORIES[s.category]
    x = sfx.render(name, seed=0, variation=0)
    assert x.ndim == 2 and x.shape[1] == 2 and np.isfinite(x).all()
    m = A.measure(x, loop=s.loop)
    assert m["peak_db"] <= cat["ceiling_db"] + 0.01, m
    assert m["clipped"] == 0
    assert m["dc"] < 0.01
    assert cat["lufs"] - sfx.MAX_LIMIT_DB - 1 <= m["lufs"] <= cat["lufs"] + 0.5, m
    if s.loop:
        assert m["seam"]["ok"], m["seam"]
        assert m["duration"] == pytest.approx(20.0)
    else:
        assert 0.03 < m["duration"] < 8.0
        assert np.abs(x[0]).max() < 1e-3 and np.abs(x[-1]).max() < 1e-3


def test_variations_differ_and_seeds_repeat() -> None:
    a = sfx.render("shotgun-fire", 0, 0)
    assert np.array_equal(a, sfx.render("shotgun-fire", 0, 0))
    b = sfx.render("shotgun-fire", 0, 1)
    n = min(len(a), len(b))
    assert len(a) != len(b) or not np.allclose(a[:n], b[:n])
    assert not np.allclose(sfx.render("pause", 0, 0)[:1000], sfx.render("pause", 0, 1)[:1000])


def test_build_package_and_export(tmp_path: Path) -> None:
    pkg = tmp_path / "ui-click"
    rep = sfx.build_sfx("ui-click", pkg, variations=2)
    assert rep["ok"], rep["errors"]
    man = json.loads((pkg / "manifest.json").read_text())
    assert man["kind"] == "sfx" and man["category"] == "ui" and man["variations"] == 2
    assert sorted(man["files"]) == ["ui-click_1.ogg", "ui-click_1.wav", "ui-click_2.ogg", "ui-click_2.wav"]
    assert len(man["loudness"]["lufs"]) == 2
    game = tmp_path / "game"
    entry = catalog.export(pkg, game_root=game)
    assert (game / "public" / "assets" / "sfx" / "ui-click" / "ui-click_2.ogg").is_file()
    assert entry["base"] == "assets/sfx/ui-click/"


def test_loop_package(tmp_path: Path) -> None:
    rep = sfx.build_sfx("ambience-wind", tmp_path / "w", variations=1, previews=False, seconds=6.0)
    assert rep["ok"], rep["errors"]
    man = json.loads((tmp_path / "w" / "manifest.json").read_text())
    assert man["loop"] and man["takes"][0]["loop_end"] == A.n_samples(6.0)
    assert A.seam(A.read_wav(tmp_path / "w" / "ambience-wind_1.wav"))["ok"]
