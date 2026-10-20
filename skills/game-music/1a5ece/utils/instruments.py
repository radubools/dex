"""Instrument timbres for procedural game music, built on `audio` (from game-sfx).

Pitched instruments: `fn(freq, dur, vel) -> mono array` — `dur` is how long the
note is held; the array runs on through its release/decay tail. Drums:
`fn(vel, seed) -> mono array`. `note(name, midi, dur, vel)` is the cached entry
point the composer uses.

    from utils import instruments as I
    x = I.note("bell", 76, 0.5, 0.8)       # E5 glockenspiel, 0.5 s held
    k = I.hit("kick", 0.9)
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from . import audio as A

NYQ = A.SR / 2


def _harmonics(freq: float, dur: float, amps, decays, inharm: float = 0.0, seed: int = 0) -> np.ndarray:
    t = A.times(dur)
    out = np.zeros_like(t)
    r = A.rng(seed, int(freq * 10))
    for k, (a, d) in enumerate(zip(amps, decays), start=1):
        f = freq * k * math.sqrt(1 + inharm * k * k)
        if f >= NYQ * 0.9:
            break
        out += a * np.exp(-t / d) * np.sin(2 * np.pi * f * t + r.random() * 6.28)
    return out


def _gate(x: np.ndarray, dur: float, release: float) -> np.ndarray:
    """Damp a ringing note `release` s after it is let go."""
    t = A.times(len(x) / A.SR)
    g = np.clip(1 - (t - dur) / max(release, 1e-3), 0, 1) ** 2
    return x * g


# ------------------------------------------------------------- pitched ----

def pluck(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """Plucked string (harp / guitar-ish): bright attack, upper partials die first."""
    tail = 0.25
    life = min(dur + tail, 2.5)
    k = np.arange(1, 30)
    amps = np.abs(np.sin(np.pi * k * 0.22)) / k ** (1.4 - 0.5 * vel)
    tau = 1.4 * (220 / freq) ** 0.35
    decays = tau / (1 + 0.12 * (k - 1) ** 1.4)
    x = _harmonics(freq, life, amps, decays, inharm=1e-5)
    x[: A.n_samples(0.004)] += A.highpass(A.noise(0.004, "white", A.rng(int(freq))), 2000) * 0.2
    return _gate(x * A.perc(life, 0.002, 10), dur, tail) * vel * 0.6


def marimba(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """Wooden bar: strong fundamental, 4x and 10x partials, soft mallet click."""
    life = min(1.6, 0.4 + 0.9 * (300 / freq) ** 0.5)
    sc = (440 / freq) ** 0.4
    x = A.modal([freq, freq * 3.99, freq * 10.65], [0.45 * sc, 0.09 * sc, 0.03], [1, 0.35 * vel, 0.15 * vel], life)
    x[: A.n_samples(0.003)] += A.noise(0.003, "white", A.rng(int(freq))) * 0.1 * vel
    return x * vel


def bell(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """Glockenspiel / celesta bell: inharmonic sparkle, long shimmering decay."""
    life = 2.2
    x = A.modal([freq, freq * 2.0, freq * 2.76, freq * 5.40, freq * 8.93],
                [1.4, 0.7, 0.5, 0.22, 0.1], [1, 0.25, 0.4 * vel, 0.2 * vel, 0.08 * vel], life)
    return x * A.perc(life, 0.001, 10) * vel * 0.5


def music_box(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """Music-box tine: pure, bright and short."""
    life = 1.4
    x = A.modal([freq, freq * 3.0, freq * 5.9], [0.6, 0.15, 0.05], [1, 0.25, 0.08], life)
    return x * vel * 0.45


def pad(freq: float, dur: float, vel: float = 0.6) -> np.ndarray:
    """Warm synth pad: detuned saws through a soft lowpass, slow swell."""
    rel = 0.9
    n = dur + rel
    x = A.supersaw(freq, n, 3, 9, A.rng(int(freq))) * 0.6 + A.osc(freq / 2, n) * 0.3
    x = A.lowpass(x, 700 + 1800 * vel, order=2)
    return x * A.adsr(dur, 0.35, 0.6, 0.8, rel)[: len(x)] * vel * 0.6


def strings(freq: float, dur: float, vel: float = 0.7) -> np.ndarray:
    """String section (legato): bowed saws, delayed vibrato, gentle bite."""
    rel = 0.35
    n = dur + rel
    f = A.vibrato(freq, n, 5.3, 0.12, delay=0.25)
    x = A.supersaw(f, n, 4, 14, A.rng(int(freq), 1))
    x = A.highpass(A.lowpass(x, 1500 + 2500 * vel), 180)
    return x * A.adsr(dur, 0.12, 0.3, 0.85, rel)[: len(x)] * vel * 0.5


def spiccato(freq: float, dur: float, vel: float = 0.7) -> np.ndarray:
    """Short bowed strings for ostinatos: quick bite, no sustain."""
    n = min(dur, 0.25) + 0.12
    x = A.supersaw(freq, n, 3, 12, A.rng(int(freq), 2))
    x = A.highpass(A.lowpass(x, 2200 + 2500 * vel), 200)
    return x * A.perc(n, 0.008, 0.09) * vel * 1.5


def bass(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """Round plucked bass (upright-ish): sine body with a short harmonic thumb."""
    rel = 0.08
    n = dur + rel
    x = _harmonics(freq, n, [1, 0.45, 0.25, 0.12, 0.06], [1.2, 0.4, 0.2, 0.1, 0.06])
    return x * A.adsr(dur, 0.005, 0.4, 0.6, rel)[: len(x)] * vel


def synth_bass(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """Driving saw bass for combat: filter snaps open on each note, slight drive."""
    rel = 0.05
    n = dur + rel
    x = A.osc(freq, n, "saw") * 0.7 + A.osc(freq / 2, n, "square") * 0.3
    cut = A.envelope([(0, 300), (0.01, 400 + 2200 * vel), (0.15, 600), (n, 500)], n)
    x = A.drive(A.lowpass(x, cut, q=1.4), 1.5)
    return x * A.adsr(dur, 0.004, 0.15, 0.7, rel)[: len(x)] * vel * 0.7


def brass(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """Brass section: saws whose brightness swells with the attack."""
    rel = 0.18
    n = dur + rel
    f = A.vibrato(freq, n, 5.0, 0.06, delay=0.3)
    x = A.supersaw(f, n, 2, 7, A.rng(int(freq), 3))
    top = 600 + 3500 * vel
    cut = A.envelope([(0, 250), (0.06, top), (0.3, top * 0.6), (n, top * 0.4)], n)
    x = A.drive(A.lowpass(x, cut, q=0.9), 1.2)
    return x * A.adsr(dur, 0.03, 0.2, 0.8, rel)[: len(x)] * vel * 0.6


def flute(freq: float, dur: float, vel: float = 0.7) -> np.ndarray:
    """Breathy flute / whistle lead."""
    rel = 0.12
    n = dur + rel
    f = A.vibrato(freq, n, 5.0, 0.08, delay=0.2)
    x = A.osc(f, n) + 0.18 * A.osc(f * 2, n) + 0.06 * A.osc(f * 3, n)
    br = A.resonate(A.noise(n, "white", A.rng(int(freq), 4)), freq * 2, 3) * 0.25
    return (x + br) * A.adsr(dur, 0.06, 0.2, 0.85, rel)[: len(x)] * vel * 0.7


def choir(freq: float, dur: float, vel: float = 0.6) -> np.ndarray:
    """Soft 'ooh' choir pad."""
    rel = 0.6
    n = dur + rel
    f = A.vibrato(freq, n, 4.8, 0.1, delay=0.1)
    x = A.formant(A.supersaw(f, n, 3, 10, A.rng(int(freq), 5)), "u", 5) * 2.5
    return x * A.adsr(dur, 0.3, 0.4, 0.85, rel)[: len(x)] * vel * 0.3


PITCHED = {"pluck": pluck, "marimba": marimba, "bell": bell, "music_box": music_box, "pad": pad,
           "strings": strings, "spiccato": spiccato, "bass": bass, "synth_bass": synth_bass,
           "brass": brass, "flute": flute, "choir": choir}


# --------------------------------------------------------------- drums ----

def kick(vel: float = 0.9, seed: int = 0) -> np.ndarray:
    """Kick drum: pitch-dropping sine thump with a click."""
    d = 0.45
    x = A.osc(A.sweep(150, 46, d), d) * A.perc(d, 0.001, 0.16)
    x[: A.n_samples(0.003)] += A.noise(0.003, "white", A.rng(seed, "k")) * 0.3
    return x * vel


def snare(vel: float = 0.8, seed: int = 0) -> np.ndarray:
    """Snare: tone plus bright rattle."""
    d = 0.3
    tone = A.osc(A.sweep(230, 180, d), d) * A.perc(d, 0.001, 0.05)
    rattle = A.bandpass(A.noise(d, "white", A.rng(seed, "s")), 1200, 9000) * A.perc(d, 0.001, 0.08) * 2.5
    return (tone * 0.6 + rattle) * vel * 1.6


def brush(vel: float = 0.5, seed: int = 0) -> np.ndarray:
    """Brushed snare swish for gentle grooves."""
    d = 0.25
    return A.bandpass(A.noise(d, "pink", A.rng(seed, "b")), 1500, 8000) * A.envelope([(0, 0), (0.03, 1), (d, 0)], d) * vel * 4.5


def _metal(d: float, seed: int) -> np.ndarray:
    fs = (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)
    x = sum(A.osc(f * 4.1, d, "square", phase=k * 0.17) for k, f in enumerate(fs)) / 6
    return A.highpass(x, 7000) + A.highpass(A.noise(d, "white", A.rng(seed, "m")), 8000) * 0.6


def hat(vel: float = 0.6, seed: int = 0) -> np.ndarray:
    """Closed hi-hat."""
    d = 0.08
    return _metal(d, seed) * A.perc(d, 0.0005, 0.018) * vel * 3


def open_hat(vel: float = 0.6, seed: int = 0) -> np.ndarray:
    """Open hi-hat."""
    d = 0.4
    return _metal(d, seed) * A.perc(d, 0.001, 0.12) * vel * 2


def crash(vel: float = 0.7, seed: int = 0) -> np.ndarray:
    """Crash cymbal wash."""
    d = 1.8
    x = _metal(d, seed) * 0.7 + A.highpass(A.noise(d, "white", A.rng(seed, "c")), 3500) * 0.6
    return x * A.perc(d, 0.002, 0.5) * vel


def tom(vel: float = 0.8, seed: int = 0, freq: float = 110.0) -> np.ndarray:
    """Tom: pitched membrane thump."""
    d = 0.5
    x = A.osc(A.sweep(freq * 1.6, freq, 0.5), d) * A.perc(d, 0.001, 0.15)
    x += A.lowpass(A.noise(d, "white", A.rng(seed, "t")), 1500) * A.perc(d, 0.001, 0.02) * 0.5
    return x * vel * 0.6


def taiko(vel: float = 0.9, seed: int = 0) -> np.ndarray:
    """Big low drum for the boss: long boom, skin slap."""
    d = 1.2
    f = np.concatenate([A.sweep(95, 55, 0.6), np.full(A.n_samples(d) - A.n_samples(0.6), 55.0)])
    x = A.osc(f, d) * A.perc(d, 0.002, 0.35)
    x += A.osc(140, d) * A.perc(d, 0.001, 0.08) * 0.3
    x += A.lowpass(A.noise(d, "white", A.rng(seed, "tk")), 600) * A.perc(d, 0.001, 0.03) * 1.2
    return A.drive(x, 1.3) * vel * 0.5


def shaker(vel: float = 0.4, seed: int = 0) -> np.ndarray:
    """Shaker grain."""
    d = 0.09
    return A.bandpass(A.noise(d, "white", A.rng(seed, "sh")), 5000, 12000) * A.envelope([(0, 0), (0.015, 1), (d, 0)], d) * vel * 4.5


def clap(vel: float = 0.7, seed: int = 0) -> np.ndarray:
    """Hand clap: three quick bursts and a tail."""
    d = 0.3
    n = A.bandpass(A.noise(d, "white", A.rng(seed, "cl")), 900, 3500) * 2
    env = np.zeros(A.n_samples(d))
    burst = A.perc(0.012, 0.0005, 0.004)
    for k in range(3):
        A.place(env, burst, A.n_samples(0.01 * k))
    env += A.perc(d, 0.012, 0.06) * 0.6
    return n * env * vel * 2


def woodblock(vel: float = 0.6, seed: int = 0) -> np.ndarray:
    """Wood block tick."""
    d = 0.12
    return A.modal([900, 2050], [0.03, 0.01], [1, 0.3], d) * vel


def sleigh(vel: float = 0.6, seed: int = 0) -> np.ndarray:
    """Sleigh-bell shake: a cluster of tiny jingles."""
    r = A.rng(seed, "sleigh")
    d = 0.35
    x = np.zeros(A.n_samples(d))
    for _ in range(16):
        f = r.uniform(4200, 8800)
        b = A.modal([f, f * 1.47], [r.uniform(0.02, 0.05), 0.015], [1, 0.4], 0.15, r)
        A.place(x, b * r.uniform(0.3, 1), float(r.random() ** 2 * 0.09))
    x += A.highpass(A.noise(d, "white", r), 6000) * A.perc(d, 0.003, 0.04) * 0.3
    return x * vel * 0.4


DRUMS = {"kick": kick, "snare": snare, "brush": brush, "hat": hat, "open_hat": open_hat, "crash": crash,
         "tom_hi": lambda v=0.8, seed=0: tom(v, seed, 165.0), "tom_lo": lambda v=0.8, seed=0: tom(v, seed, 98.0),
         "taiko": taiko, "shaker": shaker, "clap": clap, "woodblock": woodblock, "sleigh": sleigh}

#: General MIDI program for each instrument (for `compose.write_midi`); drums go on channel 10.
GM_PROGRAM = {"pluck": 46, "marimba": 12, "bell": 9, "music_box": 10, "pad": 89, "strings": 48, "spiccato": 45,
              "bass": 32, "synth_bass": 38, "brass": 61, "flute": 73, "choir": 52}
GM_DRUM = {"kick": 36, "snare": 38, "brush": 40, "hat": 42, "open_hat": 46, "crash": 49, "tom_hi": 50,
           "tom_lo": 45, "taiko": 41, "shaker": 70, "clap": 39, "woodblock": 76, "sleigh": 83}


@lru_cache(maxsize=4096)
def _note(name: str, midi: float, dur: float, vel: float) -> np.ndarray:
    x = PITCHED[name](A.midi_hz(midi), dur, vel)
    x = A.fade(x, 0.0005, 0.01)
    x.setflags(write=False)
    return x


def note(name: str, midi: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """A pitched note (mono, read-only, cached on rounded duration/velocity)."""
    return _note(name, round(float(midi), 2), round(float(dur), 3), round(float(vel), 2))


@lru_cache(maxsize=512)
def _hit(name: str, vel: float, seed: int) -> np.ndarray:
    x = A.fade(DRUMS[name](vel, seed), 0.0003, 0.005)
    x.setflags(write=False)
    return x


def hit(name: str, vel: float = 0.8, seed: int = 0) -> np.ndarray:
    """A drum hit (mono, read-only, cached); `seed` 0-3 gives slightly different takes."""
    return _hit(name, round(float(vel), 2), int(seed) % 4)


def names() -> dict:
    """The instrument names: `{"pitched": [...], "drums": [...]}`."""
    return {"pitched": list(PITCHED), "drums": list(DRUMS)}
