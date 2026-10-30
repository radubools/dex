"""Render a parsed MIDI to stereo audio with a per-instrument additive synthesiser.

pretty_midi's own synthesize() ignores MIDI programs, so a four-voice canon
would come out as one tone. This builds each GM program its own timbre from
the parsed notes instead.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass

import numpy as np
from scipy.signal import fftconvolve, lfilter

SAMPLE_RATE = 44100


@dataclass
class Timbre:
    """A synthetic instrument: harmonic amplitudes, an ADSR shape, and a stereo pan."""

    harmonics: tuple[float, ...]
    attack: float
    decay: float
    sustain: float
    release: float
    vibrato_hz: float
    vibrato_depth: float  # semitones
    breath: float         # filtered noise mixed in
    gain: float
    pan: float            # -1 hard left .. +1 hard right
    # Struck and plucked strings die away on their own instead of holding a
    # plateau: each partial decays at `damping` * its number ** 0.6 per second
    # (scaled gently with pitch, as low strings ring longer). 0 holds steady.
    damping: float = 0.0
    # A short noise burst on the onset — a hammer, a pluck, a tongued attack.
    chiff: float = 0.0


# Keyed by General MIDI program number. One or two per family — woodwind,
# keyboard, bowed, plucked, brass — so a piece outside the woodwind quartet
# does not fall back to one undifferentiated tone for every voice.
TIMBRES: dict[int, Timbre] = {
    73: Timbre(  # Flute — near-pure, a little breath
        harmonics=(1.0, 0.22, 0.07, 0.025, 0.01),
        attack=0.055, decay=0.10, sustain=0.86, release=0.13,
        vibrato_hz=5.1, vibrato_depth=0.055, breath=0.030, gain=0.85, pan=-0.45,
    ),
    71: Timbre(  # Clarinet — hollow, odd harmonics dominate
        harmonics=(1.0, 0.04, 0.52, 0.04, 0.30, 0.03, 0.16, 0.02, 0.07),
        attack=0.040, decay=0.09, sustain=0.90, release=0.11,
        vibrato_hz=4.6, vibrato_depth=0.025, breath=0.014, gain=0.80, pan=0.40,
    ),
    68: Timbre(  # Oboe — reedy, strong upper partials
        harmonics=(0.55, 1.0, 0.72, 0.44, 0.26, 0.15, 0.09, 0.05),
        attack=0.035, decay=0.08, sustain=0.88, release=0.10,
        vibrato_hz=5.5, vibrato_depth=0.060, breath=0.012, gain=0.62, pan=0.18,
    ),
    70: Timbre(  # Bassoon — warm, led by the second partial
        harmonics=(0.62, 1.0, 0.60, 0.34, 0.18, 0.10, 0.05),
        attack=0.045, decay=0.10, sustain=0.87, release=0.14,
        vibrato_hz=4.4, vibrato_depth=0.035, breath=0.018, gain=0.95, pan=-0.18,
    ),
    0: Timbre(  # Acoustic Grand Piano — hammer strike, then a long dying tail
        harmonics=(1.0, 0.55, 0.32, 0.22, 0.14, 0.09, 0.06, 0.04, 0.025, 0.015),
        attack=0.004, decay=0.05, sustain=0.80, release=0.22,
        vibrato_hz=0.0, vibrato_depth=0.0, breath=0.0, gain=1.0, pan=0.0,
        damping=1.1, chiff=0.08,
    ),
    40: Timbre(  # Violin — slow bow, singing vibrato, bright saw-like partials
        harmonics=(1.0, 0.70, 0.52, 0.40, 0.32, 0.25, 0.19, 0.15, 0.11, 0.08, 0.06, 0.04),
        attack=0.11, decay=0.10, sustain=0.88, release=0.18,
        vibrato_hz=5.8, vibrato_depth=0.12, breath=0.010, gain=0.60, pan=0.30,
    ),
    48: Timbre(  # String Ensemble — slower swell, shallower vibrato, a little darker
        harmonics=(1.0, 0.62, 0.44, 0.32, 0.24, 0.17, 0.12, 0.08, 0.05, 0.03),
        attack=0.22, decay=0.15, sustain=0.92, release=0.30,
        vibrato_hz=5.2, vibrato_depth=0.06, breath=0.008, gain=0.65, pan=-0.10,
    ),
    24: Timbre(  # Nylon Guitar — plucked, decays straight away, no plateau
        harmonics=(1.0, 0.48, 0.30, 0.16, 0.10, 0.06, 0.03),
        attack=0.003, decay=0.03, sustain=0.90, release=0.12,
        vibrato_hz=0.0, vibrato_depth=0.0, breath=0.0, gain=1.0, pan=-0.30,
        damping=2.2, chiff=0.06,
    ),
    56: Timbre(  # Trumpet — brassy, second and third partials lead, tongued onset
        harmonics=(0.75, 1.0, 0.80, 0.66, 0.52, 0.38, 0.27, 0.18, 0.12, 0.08),
        attack=0.035, decay=0.08, sustain=0.85, release=0.10,
        vibrato_hz=5.2, vibrato_depth=0.035, breath=0.010, gain=0.60, pan=0.10,
        chiff=0.05,
    ),
}

DEFAULT_TIMBRE = Timbre(
    harmonics=(1.0, 0.3, 0.12, 0.05),
    attack=0.02, decay=0.08, sustain=0.85, release=0.12,
    vibrato_hz=5.0, vibrato_depth=0.03, breath=0.0, gain=0.8, pan=0.0,
)


def midi_to_hz(pitch: int) -> float:
    """Equal-tempered frequency of a MIDI note number (A4 = 440 Hz)."""
    return 440.0 * 2.0 ** ((pitch - 69) / 12.0)


def envelope(n: int, timbre: Timbre, sample_rate: int) -> np.ndarray:
    """An ADSR amplitude envelope of n samples, with the release fitted inside n."""
    attack = min(int(timbre.attack * sample_rate), max(n // 4, 1))
    release = min(int(timbre.release * sample_rate), max(n - attack - 1, 1))
    decay = min(int(timbre.decay * sample_rate), max(n - attack - release, 1))
    hold = max(n - attack - decay - release, 0)

    env = np.concatenate([
        # a raised cosine attack is softer than a straight ramp for wind tone
        0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, attack, endpoint=False)),
        np.linspace(1.0, timbre.sustain, decay, endpoint=False),
        np.full(hold, timbre.sustain),
        np.linspace(timbre.sustain, 0.0, release),
    ])
    if env.size < n:
        env = np.concatenate([env, np.zeros(n - env.size)])
    return env[:n]


def render_note(pitch: int, velocity: int, seconds: float, timbre: Timbre,
                sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Synthesise one note as a mono float array."""
    n = max(int(round(seconds * sample_rate)), 8)
    t = np.arange(n) / sample_rate

    # Vibrato swells in rather than starting at full depth.
    swell = np.clip(t / 0.35, 0.0, 1.0)
    vibrato = timbre.vibrato_depth * swell * np.sin(2 * np.pi * timbre.vibrato_hz * t)
    base_hz = midi_to_hz(pitch)
    phase = 2 * np.pi * np.cumsum(base_hz * 2.0 ** (vibrato / 12.0)) / sample_rate

    tone = np.zeros(n)
    for index, amplitude in enumerate(timbre.harmonics, start=1):
        if base_hz * index >= sample_rate / 2:
            break
        partial = amplitude * np.sin(index * phase)
        if timbre.damping > 0:
            rate = timbre.damping * index ** 0.6 * (base_hz / 261.63) ** 0.4
            partial *= np.exp(-rate * t)
        tone += partial
    tone /= max(sum(timbre.harmonics), 1e-9)

    if timbre.breath > 0:
        noise = np.random.default_rng(pitch * 7919 + n).standard_normal(n)
        alpha = 0.12  # one-pole lowpass, so the breath does not hiss
        tone += timbre.breath * lfilter([alpha], [1.0, -(1.0 - alpha)], noise)

    tone *= envelope(n, timbre, sample_rate)
    if timbre.chiff > 0:
        # Outside the envelope, so a slow attack does not swallow it.
        burst = np.random.default_rng(pitch * 104729 + n).standard_normal(n)
        alpha = 0.35
        tone += (timbre.chiff * np.exp(-t / 0.012)
                 * lfilter([alpha], [1.0, -(1.0 - alpha)], burst))

    loudness = (velocity / 127.0) ** 1.4
    return tone * loudness * timbre.gain


def room_impulse(sample_rate: int = SAMPLE_RATE, seconds: float = 1.35) -> np.ndarray:
    """A short exponentially decaying noise burst, used as a room impulse response."""
    n = int(seconds * sample_rate)
    t = np.arange(n) / sample_rate
    tail = np.random.default_rng(20260912).standard_normal(n) * np.exp(-4.2 * t)
    tail[: int(0.012 * sample_rate)] = 0.0  # pre-delay
    return tail / np.max(np.abs(tail))


def render(midi, sample_rate: int = SAMPLE_RATE, tail_seconds: float = 1.8,
           wet: float = 0.20, peak: float = 0.89) -> np.ndarray:
    """Render a PrettyMIDI to a stereo float array, timed from its own tempo map."""
    total = midi.get_end_time() + tail_seconds
    n = int(np.ceil(total * sample_rate))
    channels = np.zeros((n, 2))

    for instrument in midi.instruments:
        timbre = TIMBRES.get(instrument.program, DEFAULT_TIMBRE)
        gains = np.array([
            np.sqrt((1.0 - timbre.pan) / 2.0),
            np.sqrt((1.0 + timbre.pan) / 2.0),
        ])
        for note in instrument.notes:
            audio = render_note(
                note.pitch, note.velocity, note.end - note.start, timbre, sample_rate)
            offset = int(round(note.start * sample_rate))
            end = min(offset + audio.size, n)
            if end > offset:
                channels[offset:end] += np.outer(audio[: end - offset], gains)

    impulse = room_impulse(sample_rate)
    for channel in range(2):
        reverb = fftconvolve(channels[:, channel], impulse)[:n] * 0.09
        channels[:, channel] = (1 - wet) * channels[:, channel] + wet * reverb

    loudest = np.max(np.abs(channels))
    if loudest > 0:
        channels *= peak / loudest
    return channels


def write_wav(path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write a float array in [-1, 1] as a 16-bit PCM WAV file."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1 if pcm.ndim == 1 else pcm.shape[1])
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def wav_duration(path) -> float:
    """Duration of a WAV file in seconds, read from its own header."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()
