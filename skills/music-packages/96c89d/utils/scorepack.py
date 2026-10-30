"""Inspect a music package: validate its score sidecar and measure its audio."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

SIDECAR_FIELDS = ("title", "midi", "audio")


def problems_with_sidecar(path, fields: tuple[str, ...] = SIDECAR_FIELDS) -> list[str]:
    """List what is wrong with a .score.json: missing fields, or files it cannot reach."""
    path = Path(path)
    found = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"{path.name} could not be read as JSON: {error}"]

    if not isinstance(data, dict):
        return [f"{path.name} is not a JSON object"]

    for field in fields:
        if field not in data:
            found.append(f"{path.name} is missing the {field!r} field")
        elif not data[field]:
            found.append(f"{path.name} has an empty {field!r} field")
    for extra in sorted(set(data) - set(fields)):
        found.append(f"{path.name} has an unexpected field {extra!r}")

    # The viewer is only ever handed files sitting beside the sidecar, so a
    # path component here is a file it can never open.
    for field in ("midi", "audio"):
        name = data.get(field)
        if not name or field not in fields:
            continue
        if "/" in name or "\\" in name:
            found.append(f"{path.name}: {field} is not a bare sibling filename: {name}")
        elif not (path.parent / name).exists():
            found.append(f"{path.name}: {field} names a missing file: {name}")
    return found


def read_wav_mono(path) -> tuple[np.ndarray, int]:
    """Read a 16-bit WAV as a mono float array in [-1, 1], with its sample rate."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    return frames.reshape(-1, channels).mean(axis=1) / 32768.0, rate


def dominant_hz(samples: np.ndarray, sample_rate: int) -> float:
    """The strongest spectral peak of a window of samples, in Hz."""
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    return float(np.fft.rfftfreq(samples.size, 1 / sample_rate)[np.argmax(spectrum)])
