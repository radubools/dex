"""Measure a written looping music package (mix, stems, cumulative layers) and list where it misses the gates.

    from utils import music_checks
    rep = music_checks.measure_track("out/night-shift")      # numbers only, read back from disk
    problems = music_checks.track_problems(rep)              # [] when every gate passes

`measure_track` reads the files the game will load, not the arrays that were
rendered. `track_problems` compares a report with the gates in SKILL.md; every
threshold is a keyword default, so a package with other needs passes its own.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import signal

from . import audio as A
from . import catalog

#: Welch PSD bands the mix's balance is read over (Hz).
BANDS = ((60, 120), (120, 250), (250, 500), (500, 1000))


def band_levels(x: np.ndarray, bands=BANDS) -> list[float]:
    """Mean Welch PSD in dB for each (lo, hi) band of a signal's mono sum."""
    f, p = signal.welch(A.mono(x), A.SR, nperseg=8192)
    return [round(float(10 * np.log10(p[(f >= lo) & (f < hi)].mean())), 1) for lo, hi in bands]


def measure_track(package: str | Path) -> dict:
    """Read back a looping music package and measure mix, stems and cumulative stem layers."""
    pkg = Path(package)
    man = catalog.read_manifest(pkg)
    name = man["name"]
    loop_end = int(round(man["bars"] * man["beats_per_bar"] * 60 / man["bpm"] * A.SR))
    order = man.get("stem_order", [])
    x = {"mix": A.read_wav(pkg / f"{name}.wav")}
    for s in order:
        x[s] = A.read_wav(pkg / man["stems"][s]["wav"])
    layers, acc = {}, None
    for s in order:
        acc = x[s] if acc is None else acc + x[s]
        layers["+".join(order[: len(layers) + 1])] = acc
    out = {k: A.measure(v, loop=True) for k, v in {**x, **layers}.items()}
    f, p = signal.welch(A.mono(x["mix"]), A.SR, nperseg=8192)
    return {
        "name": name, "manifest_loop_end": man.get("loop_end"), "loop_end": loop_end, "stem_order": order,
        "layers": out,
        "mix_minus_stems": float(np.abs(acc - x["mix"]).max()) if acc is not None else None,
        "bands_db": band_levels(x["mix"]),
        "sub60_db": round(float(10 * np.log10(p[f < 60].mean() / p[(f >= 60) & (f < 1000)].mean())), 1),
        "ogg_samples": len(A.decode(pkg / f"{name}.ogg")),
    }


def track_problems(rep: dict, ceiling_db: float = -1.0, lufs: float = -16.0, lufs_tol: float = 0.5,
                   band_spread_db: float = 10.0, max_centroid_hz: float = 6000.0, max_sub60_db: float = 3.0,
                   loudness_order: tuple[str, ...] = ("calm", "combat", "tension"), mix_tol: float = 4 / 32768,
                   ogg_tol: int = 2048) -> list[str]:
    """The gates a `measure_track` report misses, one string each; empty when it passes them all."""
    out: list[str] = []
    L = rep["loop_end"]
    if rep["manifest_loop_end"] != L:
        out.append(f"manifest loop_end {rep['manifest_loop_end']} != bar-aligned {L}")
    for k, m in rep["layers"].items():
        if m["samples"] != L:
            out.append(f"{k}: {m['samples']} samples, loop is {L}")
        if not m["seam"]["ok"]:
            out.append(f"{k}: seam ratio {m['seam']['ratio']}, curve {m['seam']['curve_ratio']}")
        if m["peak_db"] > ceiling_db or m["clipped"]:
            out.append(f"{k}: peak {m['peak_db']} dBFS, {m['clipped']} clipped samples")
    if rep["mix_minus_stems"] is not None and rep["mix_minus_stems"] > mix_tol:
        out.append(f"mix differs from the summed stems by {rep['mix_minus_stems']:.5f}")
    mix = rep["layers"]["mix"]
    if abs(mix["lufs"] - lufs) > lufs_tol:
        out.append(f"mix {mix['lufs']} LUFS, target {lufs}")
    louds = [rep["layers"][s]["lufs"] for s in loudness_order if s in rep["layers"]]
    if any(a <= b for a, b in zip(louds, louds[1:])):
        out.append(f"stem loudness not ordered {' > '.join(loudness_order)}: {louds}")
    spread = max(rep["bands_db"]) - min(rep["bands_db"])
    if spread > band_spread_db:
        out.append(f"60 Hz-1 kHz bands spread {spread:.1f} dB: {rep['bands_db']}")
    if mix["centroid_hz"] > max_centroid_hz:
        out.append(f"mix centroid {mix['centroid_hz']} Hz")
    if rep["sub60_db"] > max_sub60_db:
        out.append(f"sub-60 Hz energy {rep['sub60_db']:+.1f} dB over 60 Hz-1 kHz")
    if abs(rep["ogg_samples"] - L) > ogg_tol:
        out.append(f"ogg decodes to {rep['ogg_samples']} samples, loop is {L}")
    return out
