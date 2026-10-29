"""Shared DSP for game audio: oscillators, noise, envelopes, filters, effects,
loudness, loops, WAV/OGG files and spectrogram previews.

Signals are float64 numpy arrays at `SR` (44100 Hz): mono is shape `(n,)`,
stereo is `(n, 2)`. Every function takes either unless it says otherwise.
Full scale is +-1.0.

    from utils import audio as A
    x = A.osc(A.sweep(900, 200, 0.3), 0.3, "saw") * A.perc(0.3, decay=0.08)
    x = A.lowpass(x, 3000)
    y = A.master(A.pan(x, -0.2), lufs=-15, ceiling_db=-1)
    A.write_wav("pew.wav", y); A.encode_ogg("pew.wav")
    A.measure(y)       # peak_db, rms_db, lufs, centroid_hz, duration, ...
"""

from __future__ import annotations

import math
import shutil
import subprocess
import wave
import zlib
from pathlib import Path
from typing import Sequence

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

#: Sample rate of everything this module makes (same as `catalog.SAMPLE_RATE`).
SR = 44100
FFMPEG_FALLBACK = "/opt/homebrew/bin/ffmpeg"
_NYQ = SR / 2


# ---------------------------------------------------------------- basics ----

def n_samples(dur: float) -> int:
    """Number of samples in `dur` seconds."""
    return max(0, int(round(dur * SR)))


def times(dur: float) -> np.ndarray:
    """Time in seconds of each sample of a `dur`-second signal."""
    return np.arange(n_samples(dur)) / SR


def rng(*seed: int | str) -> np.random.Generator:
    """A seeded generator; strings are hashed so `rng(0, "shotgun", 2)` is stable."""
    parts = [zlib.crc32(s.encode()) if isinstance(s, str) else int(s) & 0xFFFFFFFF for s in seed]
    return np.random.default_rng(parts or [0])


def db_to_amp(db: float) -> float:
    """Decibels to linear gain."""
    return 10.0 ** (db / 20.0)


def amp_to_db(a: float) -> float:
    """Linear amplitude to decibels (-120 for silence)."""
    return 20.0 * math.log10(a) if a > 1e-6 else -120.0


def midi_hz(m: float) -> float:
    """MIDI note number to frequency in Hz (69 = A4 = 440)."""
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def mono(x: np.ndarray) -> np.ndarray:
    """Mix stereo down to mono (mono passes through)."""
    return x.mean(axis=1) if x.ndim == 2 else x


def stereo(x: np.ndarray) -> np.ndarray:
    """Mono to identical-channel stereo (stereo passes through)."""
    return np.stack([x, x], axis=1) if x.ndim == 1 else x


def pad_to(x: np.ndarray, n: int) -> np.ndarray:
    """Zero-pad (or cut) to `n` samples."""
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros((n - len(x),) + x.shape[1:])])


def mix(*xs: np.ndarray) -> np.ndarray:
    """Sum signals of any lengths (mono ones are made stereo if any is stereo)."""
    xs = [x for x in xs if x is not None and len(x)]
    if not xs:
        return np.zeros(0)
    if any(x.ndim == 2 for x in xs):
        xs = [stereo(x) for x in xs]
    n = max(len(x) for x in xs)
    out = np.zeros((n,) + xs[0].shape[1:])
    for x in xs:
        out[: len(x)] += x
    return out


def place(dst: np.ndarray, src: np.ndarray, at: float | int, gain: float = 1.0, wrap: bool = False) -> np.ndarray:
    """Add `src` into `dst` starting at `at` (seconds if float, samples if int), in place.

    With `wrap=True` whatever runs past the end is added at the start — the way
    to drop events into a loop buffer so the loop stays seamless.
    """
    i = int(round(at * SR)) if isinstance(at, float) else int(at)
    if src.ndim == 1 and dst.ndim == 2:
        src = stereo(src)
    n = len(dst)
    if wrap:
        i %= n
        pos = 0
        while pos < len(src):
            start = (i + pos) % n
            take = min(n - start, len(src) - pos)
            dst[start:start + take] += gain * src[pos:pos + take]
            pos += take
    else:
        if i >= n or i + len(src) <= 0:
            return dst
        s0 = max(0, -i)
        take = min(len(src) - s0, n - max(i, 0))
        dst[max(i, 0):max(i, 0) + take] += gain * src[s0:s0 + take]
    return dst


# ----------------------------------------------------------- oscillators ----

def sweep(f0: float, f1: float, dur: float, curve: str = "exp") -> np.ndarray:
    """A per-sample frequency glide from `f0` to `f1` ("exp" or "lin")."""
    n = n_samples(dur)
    return np.geomspace(f0, f1, n) if curve == "exp" else np.linspace(f0, f1, n)


def vibrato(freq: float | np.ndarray, dur: float, rate: float = 5.5, depth: float = 0.03,
            delay: float = 0.0) -> np.ndarray:
    """Frequency array with sinusoidal vibrato of `depth` semitones, fading in after `delay`."""
    t = times(dur)
    ramp = np.clip((t - delay) / 0.25, 0, 1) if delay > 0 else 1.0
    return np.broadcast_to(freq, t.shape) * 2.0 ** (depth * ramp * np.sin(2 * np.pi * rate * t) / 12)


def _phase(freq: float | np.ndarray, n: int, phase: float) -> tuple[np.ndarray, np.ndarray]:
    f = np.broadcast_to(np.asarray(freq, dtype=float), (n,))
    dt = f / SR
    ph = phase + np.concatenate([[0.0], np.cumsum(dt)[:-1]]) if n else np.zeros(0)
    return ph, dt


def _blep(t: np.ndarray, dt: np.ndarray) -> np.ndarray:
    out = np.zeros_like(t)
    a = t < dt
    x = t[a] / dt[a]
    out[a] = 2 * x - x * x - 1
    b = t > 1 - dt
    x = (t[b] - 1) / dt[b]
    out[b] = x * x + 2 * x + 1
    return out


def osc(freq: float | np.ndarray, dur: float, shape: str = "sine", phase: float = 0.0,
        pw: float = 0.5) -> np.ndarray:
    """Oscillator: "sine", "triangle", "saw" or "square" (band-limited PolyBLEP); `freq` may be an array."""
    n = n_samples(dur)
    ph, dt = _phase(freq, n, phase)
    t = ph % 1.0
    if shape == "sine":
        return np.sin(2 * np.pi * ph)
    if shape == "triangle":
        return 2 * np.abs(2 * t - 1) - 1
    if shape == "saw":
        return 2 * t - 1 - _blep(t, dt)
    if shape == "square":
        return np.where(t < pw, 1.0, -1.0) + _blep(t, dt) - _blep((t - pw) % 1.0, dt)
    raise ValueError(f"unknown shape {shape!r}")


def supersaw(freq: float | np.ndarray, dur: float, voices: int = 3, detune_cents: float = 12,
             r: np.random.Generator | None = None) -> np.ndarray:
    """Several detuned saws with random phases, normalised to about +-1."""
    r = r or rng(0)
    cents = np.linspace(-detune_cents, detune_cents, voices) if voices > 1 else [0.0]
    out = sum(osc(np.asarray(freq) * 2 ** (c / 1200), dur, "saw", phase=r.random()) for c in cents)
    return out / math.sqrt(voices)


def noise(dur: float, color: str = "white", r: np.random.Generator | None = None) -> np.ndarray:
    """Noise at RMS 0.25: "white", "pink" (-3 dB/oct) or "brown" (-6 dB/oct), nothing below 20 Hz."""
    r = r or rng(0)
    n = n_samples(dur)
    w = r.standard_normal(n)
    if color != "white" and n > 1:
        spec = np.fft.rfft(w)
        f = np.fft.rfftfreq(n, 1 / SR)
        f[0] = f[1] if len(f) > 1 else 1.0
        spec /= np.sqrt(f) if color == "pink" else f
        spec[f < 20] = 0  # no sub-audio rumble: it would eat the headroom
        w = np.fft.irfft(spec, n)
    s = np.sqrt(np.mean(w ** 2)) if n else 1.0
    return w * (0.25 / s if s > 0 else 0)


def smooth_random(dur: float, rate: float, r: np.random.Generator | None = None,
                  loop: bool = False) -> np.ndarray:
    """A slow random control curve in 0..1 changing about `rate` times a second (periodic if `loop`)."""
    r = r or rng(0)
    n = n_samples(dur)
    if loop:
        spec = np.zeros(n // 2 + 1, complex)
        k = max(1, int(rate * dur))
        spec[1:k + 1] = (r.standard_normal(k) + 1j * r.standard_normal(k)) / np.arange(1, k + 1) ** 0.5
        c = np.fft.irfft(spec, n)
    else:
        m = int(dur * rate) + 2
        c = np.interp(np.arange(n), np.linspace(0, n, m), r.random(m))
        c = uniform_filter1d(c, size=max(1, int(SR / max(rate, 1e-3))), mode="nearest")
    lo, hi = (c.min(), c.max()) if n else (0.0, 1.0)
    return (c - lo) / (hi - lo) if hi > lo else np.full(n, 0.5)


def modal(freqs: Sequence[float], decays: Sequence[float], amps: Sequence[float], dur: float,
          r: np.random.Generator | None = None) -> np.ndarray:
    """Sum of exponentially decaying sines (bells, wood, metal); `decays` are 1/e times in seconds."""
    t = times(dur)
    r = r or rng(0)
    out = np.zeros_like(t)
    for f, d, a in zip(freqs, decays, amps):
        if 20 < f < _NYQ * 0.95:
            out += a * np.exp(-t / d) * np.sin(2 * np.pi * f * t + r.random() * 0.2)
    return out


def partial(freq: float | np.ndarray, decay: float, amp: float, dur: float, r: np.random.Generator,
            attack: float = 0.001) -> np.ndarray:
    """One decaying sine (1/e time `decay`) that lands on zero, with zero slope, at its buffer end; `freq` may glide."""
    # `modal` stops a partial dead at `dur`, which clicks mid-file once reverb is added; here the
    # exponential is shifted down by its end value and rescaled, then `smooth_end` removes the kink.
    t = times(dur)
    env = smooth_end((np.exp(-t / decay) - np.exp(-dur / decay)) / (1 - np.exp(-dur / decay)))
    env *= np.minimum(t / max(attack, 1e-5), 1.0)
    if np.ndim(freq) == 0 and not 20 < freq < SR * 0.45:
        return np.zeros_like(t)
    return amp * env * osc(freq, dur, "sine", phase=r.random() * 0.2)


def bar(freqs: Sequence[float], decays: Sequence[float], amps: Sequence[float], dur: float,
        r: np.random.Generator, attack: float = 0.001) -> np.ndarray:
    """A struck bar, bell or block as a sum of `partial`s: `modal` without the click at the buffer end."""
    return sum(partial(f, d, a, dur, r, attack) for f, d, a in zip(freqs, decays, amps))


# ------------------------------------------------------------- envelopes ----

def envelope(points: Sequence[tuple[float, float]], dur: float) -> np.ndarray:
    """Piecewise-linear envelope through `(time, level)` breakpoints."""
    t = times(dur)
    pt = np.array(points, dtype=float)
    return np.interp(t, pt[:, 0], pt[:, 1])


def adsr(dur: float, a: float = 0.01, d: float = 0.1, s: float = 0.7, r: float = 0.2) -> np.ndarray:
    """Attack-decay-sustain-release; `dur` is the gate (held) time, the result is `dur + r` long."""
    t = times(dur + r)
    a = max(a, 1e-4)
    env = np.where(t < a, t / a, s + (1 - s) * np.exp(-(t - a) / max(d, 1e-4) * 3))
    held = env[min(len(env) - 1, n_samples(dur))] if len(env) else 0
    rel = t >= dur
    env[rel] = held * np.clip(1 - (t[rel] - dur) / max(r, 1e-4), 0, 1) ** 2
    return env


def perc(dur: float, attack: float = 0.002, decay: float = 0.2) -> np.ndarray:
    """Percussive envelope: linear attack, exponential decay (1/e time `decay`), forced to 0 at the end."""
    t = times(dur)
    env = np.where(t < attack, t / max(attack, 1e-5), np.exp(-(t - attack) / max(decay, 1e-5)))
    tail = min(len(t), n_samples(0.005))
    if tail:
        env[-tail:] *= np.linspace(1, 0, tail)
    return env


def fade(x: np.ndarray, fade_in: float = 0.002, fade_out: float = 0.01) -> np.ndarray:
    """Raised-cosine fades at both ends so a one-shot never starts or stops with a click."""
    x = x.copy()
    for length, at_end in ((n_samples(fade_in), False), (n_samples(fade_out), True)):
        length = min(length, len(x))
        if not length:
            continue
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, length))
        if at_end:
            ramp = ramp[::-1]
        if x.ndim == 2:
            ramp = ramp[:, None]
        if at_end:
            x[-length:] *= ramp
        else:
            x[:length] *= ramp
    return x


def smooth_end(env: np.ndarray, frac: float = 0.3, longest: float = 0.12) -> np.ndarray:
    """Multiply the last `frac` of an envelope (at most `longest` s) by a half-cosine to 0."""
    n = min(int(len(env) * frac), n_samples(longest))
    if n > 1:
        env = env.copy()
        env[-n:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, n)))
    return env


# --------------------------------------------------------------- filters ----

def _clampf(f: float) -> float:
    return float(np.clip(f, 10.0, _NYQ * 0.9))


def biquad(kind: str, freq: float, q: float = 0.7071, gain_db: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """RBJ biquad coefficients `(b, a)`: lowpass, highpass, bandpass, peak, lowshelf, highshelf."""
    w = 2 * np.pi * _clampf(freq) / SR
    cw, sw = np.cos(w), np.sin(w)
    al = sw / (2 * q)
    A = 10 ** (gain_db / 40)
    if kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]; a = [1 + al, -2 * cw, 1 - al]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]; a = [1 + al, -2 * cw, 1 - al]
    elif kind == "bandpass":
        b = [al, 0, -al]; a = [1 + al, -2 * cw, 1 - al]
    elif kind == "peak":
        b = [1 + al * A, -2 * cw, 1 - al * A]; a = [1 + al / A, -2 * cw, 1 - al / A]
    elif kind in ("lowshelf", "highshelf"):
        sq = 2 * np.sqrt(A) * al
        sgn = 1 if kind == "lowshelf" else -1
        b = [A * ((A + 1) - sgn * (A - 1) * cw + sq), sgn * 2 * A * ((A - 1) - sgn * (A + 1) * cw),
             A * ((A + 1) - sgn * (A - 1) * cw - sq)]
        a = [(A + 1) + sgn * (A - 1) * cw + sq, -sgn * 2 * ((A - 1) + sgn * (A + 1) * cw),
             (A + 1) + sgn * (A - 1) * cw - sq]
    else:
        raise ValueError(f"unknown biquad {kind!r}")
    a = np.array(a, float)
    return np.array(b, float) / a[0], a / a[0]


def _tv(x: np.ndarray, kind: str, freq: np.ndarray, q: float, block: int = 128) -> np.ndarray:
    """Time-varying biquad: coefficients updated every `block` samples, state carried over."""
    freq = np.broadcast_to(freq, (len(x),))
    out = np.empty_like(x)
    zi = np.zeros((2,) + x.shape[1:])
    for i in range(0, len(x), block):
        b, a = biquad(kind, float(freq[i]), q)
        out[i:i + block], zi = signal.lfilter(b, a, x[i:i + block], axis=0, zi=zi)
    return out


def _filt(x: np.ndarray, kind: str, freq, q: float | None, order: int) -> np.ndarray:
    if not len(x):
        return x
    if np.ndim(freq) > 0:
        return _tv(x, kind, np.asarray(freq, float), q or 0.7071)
    if q is not None:
        b, a = biquad(kind, freq, q)
        return signal.lfilter(b, a, x, axis=0)
    sos = signal.butter(order, _clampf(freq), btype={"lowpass": "low", "highpass": "high"}[kind], fs=SR, output="sos")
    return signal.sosfilt(sos, x, axis=0)


def lowpass(x: np.ndarray, cutoff: float | np.ndarray, order: int = 2, q: float | None = None) -> np.ndarray:
    """Lowpass (Butterworth; `q` for a resonant biquad; an array `cutoff` sweeps over time)."""
    return _filt(x, "lowpass", cutoff, q, order)


def highpass(x: np.ndarray, cutoff: float | np.ndarray, order: int = 2, q: float | None = None) -> np.ndarray:
    """Highpass (Butterworth; `q` for a resonant biquad; an array `cutoff` sweeps over time)."""
    return _filt(x, "highpass", cutoff, q, order)


def bandpass(x: np.ndarray, lo: float, hi: float, order: int = 2) -> np.ndarray:
    """Butterworth band-pass between `lo` and `hi` Hz."""
    if not len(x):
        return x
    sos = signal.butter(order, [_clampf(lo), _clampf(max(hi, lo * 1.05))], btype="band", fs=SR, output="sos")
    return signal.sosfilt(sos, x, axis=0)


def resonate(x: np.ndarray, freq: float | np.ndarray, q: float = 8.0) -> np.ndarray:
    """Resonant band-pass (constant 0 dB peak) at `freq`, which may sweep (array)."""
    if np.ndim(freq) > 0:
        return _tv(x, "bandpass", np.asarray(freq, float), q)
    b, a = biquad("bandpass", freq, q)
    return signal.lfilter(b, a, x, axis=0)


def eq(x: np.ndarray, kind: str, freq: float, gain_db: float, q: float = 0.7071) -> np.ndarray:
    """One EQ band: "peak", "lowshelf" or "highshelf"."""
    b, a = biquad(kind, freq, q, gain_db)
    return signal.lfilter(b, a, x, axis=0)


def formant(x: np.ndarray, vowel: str = "a", q: float = 6.0) -> np.ndarray:
    """Vowel-ish colouring via three parallel resonances ("a", "e", "i", "o", "u")."""
    table = {"a": (730, 1090, 2440), "e": (530, 1840, 2480), "i": (270, 2290, 3010),
             "o": (570, 840, 2410), "u": (300, 870, 2240)}
    f1, f2, f3 = table[vowel]
    return resonate(x, f1, q) + 0.6 * resonate(x, f2, q) + 0.25 * resonate(x, f3, q)


# --------------------------------------------------------------- effects ----

def drive(x: np.ndarray, amount: float = 2.0) -> np.ndarray:
    """Soft saturation (tanh), level-compensated so +-1 stays +-1."""
    return np.tanh(amount * x) / np.tanh(amount) if amount > 0 else x


def pan(x: np.ndarray, position: float = 0.0) -> np.ndarray:
    """Constant-power pan, -1 left .. +1 right; on stereo input it balances."""
    th = (np.clip(position, -1, 1) + 1) * np.pi / 4
    g = np.array([np.cos(th), np.sin(th)]) * math.sqrt(2)
    return stereo(x) * g if x.ndim == 2 else np.stack([x * g[0], x * g[1]], axis=1)


def widen(x: np.ndarray, ms: float = 8.0) -> np.ndarray:
    """Haas widening: the right channel delayed by `ms` (output same length)."""
    s = stereo(x).copy()
    d = n_samples(ms / 1000)
    if 0 < d < len(s):
        s[d:, 1] = s[:-d, 1].copy()
        s[:d, 1] = 0
    return s


def delay(x: np.ndarray, time: float = 0.25, feedback: float = 0.35, mix_: float = 0.3,
          taps: int = 6, tone: float = 5000) -> np.ndarray:
    """Feedback echo (each repeat darker); the output is longer by the echo tail."""
    d = n_samples(time)
    out = pad_to(x, len(x) + d * taps)
    rep = x
    for k in range(1, taps + 1):
        rep = lowpass(rep, tone)
        place(out, rep * mix_ * feedback ** (k - 1), k * d)
    return out


def impulse_response(decay: float = 1.5, predelay: float = 0.01, tone: float = 7000,
                     seed: int = 0, early: bool = True) -> np.ndarray:
    """A stereo reverb impulse response: decorrelated noise falling 60 dB over `decay` s, darker as it goes."""
    r = rng(seed, "ir")
    n = n_samples(decay * 1.1 + predelay)
    t = np.arange(n) / SR
    env = np.exp(-6.9 * np.clip(t - predelay, 0, None) / decay) * (t >= predelay)
    ir = np.stack([r.standard_normal(n), r.standard_normal(n)], axis=1) * env[:, None]
    bright, dark = ir, lowpass(ir, tone * 0.25)
    w = np.clip(t / decay, 0, 1)[:, None]
    ir = bright * (1 - w) + dark * w
    ir = lowpass(ir, tone)
    if early:
        for k in range(1, 7):
            i = n_samples(predelay * 0.3 + k * 0.007 * (1 + 0.37 * k))
            if i < n:
                ir[i] += (0.6 ** k) * r.choice([-1, 1], 2) * 2
    return ir / np.sqrt(np.sum(ir ** 2) / 2)


def reverb(x: np.ndarray, decay: float = 1.5, mix_: float = 0.25, predelay: float = 0.01,
           tone: float = 7000, seed: int = 0, circular: bool = False) -> np.ndarray:
    """Convolution reverb, returns stereo dry + `mix_` x wet.

    Linear (default) the output is longer by the tail. `circular=True` keeps
    the length and wraps the tail into the start — use it on loop buffers.
    """
    ir = impulse_response(decay, predelay, tone, seed)
    s = stereo(x)
    if circular:
        n = len(s)
        if len(ir) >= n:
            ir = ir[:n]
        wet = np.fft.irfft(np.fft.rfft(s, n, axis=0) * np.fft.rfft(ir, n, axis=0), n, axis=0)
        return s + mix_ * wet
    wet = signal.fftconvolve(s, ir, axes=0)
    return pad_to(s, len(wet)) + mix_ * wet


def make_loop(x: np.ndarray, length: float | int, crossfade: float = 0.5) -> np.ndarray:
    """Cut a seamless loop of `length` from `x` (which must be `length + crossfade` long).

    The material after the loop point is cross-faded (equal power) into the
    start, so the last sample flows into the first exactly as recorded.
    """
    L = n_samples(length) if isinstance(length, float) else int(length)
    xf = min(n_samples(crossfade), len(x) - L)
    if xf < 0:
        raise ValueError("make_loop needs length + crossfade samples of material")
    out = x[:L].copy()
    if xf:
        th = np.linspace(0, np.pi / 2, xf)
        fi, fo = np.sin(th), np.cos(th)
        if x.ndim == 2:
            fi, fo = fi[:, None], fo[:, None]
        out[:xf] = x[:xf] * fi + x[L:L + xf] * fo
    return out


def wrap_tail(x: np.ndarray, length: int) -> np.ndarray:
    """Fold everything past `length` samples back onto the start (seamless loop of `length`)."""
    out = x[:length].copy()
    place(out, x[length:], 0, wrap=True)
    return out


def trim_silence(x: np.ndarray, floor_db: float = -60.0, keep: float = 0.02) -> np.ndarray:
    """Cut the tail once it stays `floor_db` below the peak (keeps `keep` s more)."""
    a = np.abs(stereo(x)).max(axis=1)
    if not len(a) or a.max() == 0:
        return x
    above = np.nonzero(a > a.max() * db_to_amp(floor_db))[0]
    end = min(len(x), above[-1] + n_samples(keep)) if len(above) else len(x)
    return x[:end]


# -------------------------------------------------------------- dynamics ----

def limit(x: np.ndarray, ceiling_db: float = -1.0, lookahead: float = 0.004, loop: bool = False) -> np.ndarray:
    """Look-ahead peak limiter: no sample exceeds `ceiling_db`; gain moves smoothly over `lookahead`."""
    c = db_to_amp(ceiling_db)
    pk = np.abs(stereo(x)).max(axis=1)
    raw = np.minimum(1.0, c / np.maximum(pk, 1e-9))
    w = max(1, n_samples(lookahead))
    mode = "wrap" if loop else "nearest"
    g = minimum_filter1d(raw, size=2 * w + 1, mode=mode)
    g = uniform_filter1d(g, size=w | 1, mode=mode)
    g = np.minimum(g, raw)
    y = x * (g[:, None] if x.ndim == 2 else g)
    return np.clip(y, -c, c)


_KW = None


def _kweight(x: np.ndarray) -> np.ndarray:
    global _KW
    if _KW is None:  # ITU-R BS.1770 K-weighting, designed for SR
        _KW = (biquad("highshelf", 1681.97, 0.7072, 3.9998), biquad("highpass", 38.135, 0.5003))
    (b1, a1), (b2, a2) = _KW
    return signal.lfilter(b2, a2, signal.lfilter(b1, a1, x, axis=0), axis=0)


def loudness(x: np.ndarray) -> float:
    """Integrated loudness in LUFS (BS.1770 K-weighting and gating; whole-file block if under 0.4 s)."""
    s = _kweight(stereo(x))
    ms = (s ** 2).sum(axis=1)
    n = len(ms)
    if n == 0:
        return -120.0
    blk = min(n, n_samples(0.4))
    hop = max(1, blk // 4)
    c = np.concatenate([[0.0], np.cumsum(ms)])
    starts = np.arange(0, n - blk + 1, hop)
    z = (c[starts + blk] - c[starts]) / blk
    lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    z = z[lk > -70]
    if not len(z):
        return -120.0
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z2 = z[-0.691 + 10 * np.log10(z) > rel]
    return float(-0.691 + 10 * np.log10(z2.mean() if len(z2) else z.mean()))


def peak_db(x: np.ndarray) -> float:
    """Sample peak in dBFS."""
    return amp_to_db(float(np.abs(x).max(initial=0)))


def rms_db(x: np.ndarray) -> float:
    """RMS level in dBFS (both channels)."""
    return amp_to_db(float(np.sqrt(np.mean(x ** 2)))) if len(x) else -120.0


def centroid(x: np.ndarray) -> float:
    """Spectral centroid in Hz (magnitude-weighted mean frequency; low = dark, high = bright)."""
    m = mono(x)
    if len(m) < 64:
        return 0.0
    f, p = signal.welch(m, SR, nperseg=min(4096, len(m)))
    mag = np.sqrt(p)
    mag[mag < mag.max() * 1e-3] = 0  # ignore the noise floor / dither (> 60 dB down)
    return float((f * mag).sum() / mag.sum()) if mag.sum() > 0 else 0.0


def seam(x: np.ndarray) -> dict:
    """How the end of a loop meets its start: `ratio` = jump / 99.9th-percentile step; ok <= 1.5."""
    s = stereo(x)
    steps = np.abs(np.diff(s, axis=0))
    typical = float(np.percentile(steps, 99.9)) if len(steps) else 0.0
    jump = float(np.abs(s[0] - s[-1]).max()) if len(s) else 0.0
    # The slope across the seam should also continue: compare second differences.
    curve = float(np.abs(s[1] - 2 * s[0] + s[-1]).max()) if len(s) > 2 else 0.0
    typical2 = float(np.percentile(np.abs(np.diff(s, 2, axis=0)), 99.9)) if len(s) > 3 else 0.0
    ratio = jump / typical if typical > 0 else 0.0
    return {"jump": jump, "typical_step": typical, "ratio": ratio,
            "curve_ratio": curve / typical2 if typical2 > 0 else 0.0,
            "ok": ratio <= 1.5 and (typical2 == 0 or curve <= 1.5 * typical2)}


def measure(x: np.ndarray, loop: bool = False) -> dict:
    """Everything to check without listening: duration, peak, RMS, LUFS, centroid, DC, clipping, edges, seam."""
    s = stereo(x)
    edge = n_samples(0.001)
    out = {"duration": round(len(s) / SR, 4), "samples": len(s), "peak_db": round(peak_db(s), 2),
           "rms_db": round(rms_db(s), 2), "lufs": round(loudness(s), 2), "centroid_hz": round(centroid(s), 1),
           "dc": round(float(np.abs(s.mean(axis=0)).max()) if len(s) else 0.0, 5),
           "clipped": int((np.abs(s) >= 0.9999).sum()),
           "start_db": round(peak_db(s[:edge]), 1), "end_db": round(peak_db(s[-edge:]), 1)}
    if loop:
        out["seam"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in seam(s).items()}
    return out


def hf_stripes(x: np.ndarray, *, frame: float = 0.005, window: float = 0.05, cutoff: float = 12000.0,
               audible_db: float = -40.0, ratio: float = 8.0) -> list[tuple[float, float, float]]:
    """Mid-file clicks: 5 ms frames whose >12 kHz energy jumps `ratio`x over its +-50 ms and is audible; [(t, ratio, dB)]."""
    # "Audible" is within `audible_db` of the loudest full-band frame. A ratio alone flags every
    # soft note onset of a dark sound at -80 dB; a buffer-end click is both a jump and loud.
    m = mono(x)
    hp = highpass(m, cutoff, order=4)
    n = n_samples(frame)
    starts = range(0, len(hp) - n, n)
    e = np.array([float(np.sum(hp[i:i + n] ** 2)) for i in starts])
    full = np.array([float(np.sum(m[i:i + n] ** 2)) for i in starts])
    if not len(e) or full.max() == 0:
        return []
    w = max(1, int(window / frame))
    out = []
    for i in range(len(e)):
        level = 10 * np.log10(e[i] / full.max() + 1e-20)
        nb = np.concatenate([e[max(0, i - w):i], e[i + 1:i + 1 + w]])
        jump = e[i] / (np.median(nb) + 1e-20) if len(nb) else 0.0
        if level > audible_db and jump > ratio:
            out.append((round(i * frame, 3), round(float(jump), 1), round(float(level), 1)))
    return out


def ogg_matches(wav: np.ndarray, ogg: np.ndarray, *, min_corr: float = 0.98, silence_db: float = -60.0) -> list[str]:
    """Problems with a decoded OGG against its WAV: misaligned at sample 0, uncorrelated, or a length difference that is not silence."""
    # Opus via ffmpeg pads the encoder pre-skip (a few hundred near-silent samples) or
    # drops ~128 trailing ones of short files; neither is audible, so only non-silent
    # excess counts.
    a, b = mono(wav), mono(ogg)
    n = min(len(a), len(b))
    out = []
    corr = float(np.dot(a[:n], b[:n]) / (np.linalg.norm(a[:n]) * np.linalg.norm(b[:n]) + 1e-12))
    if corr < min_corr:
        out.append(f"ogg correlates {corr:.3f} with the wav at lag 0")
    rest = a[n:] if len(a) > n else b[n:]
    if len(rest) and amp_to_db(float(np.abs(rest).max())) > silence_db:
        out.append(f"ogg/wav length differ by {len(b) - len(a)} samples that are not silent")
    return out


def master(x: np.ndarray, lufs: float = -16.0, ceiling_db: float = -1.0, max_limit_db: float = 6.0,
           loop: bool = False, fade_in: float = 0.002, fade_out: float = 0.015) -> np.ndarray:
    """Final stage: stereo, DC removed, gain to `lufs` (but never > `max_limit_db` into the limiter), limit, fade.

    Loops get no fades and a wrap-around limiter so the seam is untouched.
    """
    s = stereo(np.asarray(x, float))
    s = s - s.mean(axis=0)
    if not loop:
        s = fade(s, fade_in, fade_out)
    cur = loudness(s)
    pk = peak_db(s)
    if cur <= -119 or pk <= -119:
        return s
    gain = min(lufs - cur, ceiling_db + max_limit_db - pk)
    s = s * db_to_amp(gain)
    s = limit(s, ceiling_db, loop=loop)
    if not loop:
        s = fade(s, fade_in, fade_out)
    return s


# -------------------------------------------------------------------- io ----

def write_wav(path: str | Path, x: np.ndarray, dither: bool = True) -> Path:
    """Write 16-bit PCM WAV (stereo if `x` is), TPDF-dithered; values are clipped to +-1."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    s = np.asarray(x, float)
    ch = 1 if s.ndim == 1 else s.shape[1]
    q = s * 32767.0
    if dither:
        d = rng(len(s), "dither")
        q = q + (d.random(q.shape) - d.random(q.shape))
    pcm = np.clip(np.round(q), -32767, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def read_wav(path: str | Path) -> np.ndarray:
    """Read a 16-bit PCM WAV as float (mono `(n,)` or `(n, ch)`)."""
    with wave.open(str(path)) as w:
        ch, n = w.getnchannels(), w.getnframes()
        data = np.frombuffer(w.readframes(n), "<i2").astype(float) / 32767.0
    return data if ch == 1 else data.reshape(-1, ch)


def ffmpeg_path() -> str | None:
    """The ffmpeg binary (PATH, then /opt/homebrew/bin), or None."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    return FFMPEG_FALLBACK if Path(FFMPEG_FALLBACK).is_file() else None


def encode_ogg(wav_path: str | Path, ogg_path: str | Path | None = None, bitrate: int = 96) -> Path:
    """Encode a WAV to Ogg Opus with ffmpeg (`bitrate` in kbps, constrained VBR; 96 is about
    60% the size of the Vorbis q5 it replaced, and better for both music and one-shots)."""
    wav_path = Path(wav_path)
    ogg = Path(ogg_path) if ogg_path else wav_path.with_suffix(".ogg")
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found (PATH or /opt/homebrew/bin/ffmpeg) — needed for OGG")
    subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(wav_path), "-c:a", "libopus",
                    "-b:a", f"{bitrate}k", "-vbr", "constrained", "-compression_level", "10",
                    str(ogg)], check=True)
    return ogg


def decode(path: str | Path) -> np.ndarray:
    """Decode any audio file ffmpeg reads (e.g. the OGG you wrote) to float stereo at SR.

    Long files come back sample-exact; for one-shots under ~1 s ffmpeg's own
    decoder may drop or add up to ~1000 samples at the end (the OGG's granule
    position, which browsers use, is exact).
    """
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found")
    raw = subprocess.run([exe, "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, "<f4").astype(float).reshape(-1, 2)


def write_pair(stem: str | Path, x: np.ndarray, bitrate: int = 96) -> tuple[Path, Path]:
    """Write `<stem>.wav` and `<stem>.ogg`; returns both paths."""
    wav = write_wav(Path(str(stem) + ".wav"), x)
    return wav, encode_ogg(wav, bitrate=bitrate)


# --------------------------------------------------------------- preview ----

def spectrogram_png(x: np.ndarray, path: str | Path, width: int = 900, height: int = 300,
                    max_hz: float = 16000, floor_db: float = -90) -> Path:
    """Spectrogram (time left to right, log-ish frequency bottom to top) plus waveform strip, as PNG."""
    from PIL import Image, ImageDraw

    m = mono(x)
    nper = 2048
    if len(m) < nper:
        m = pad_to(m, nper)
    f, _, z = signal.stft(m, SR, nperseg=nper, noverlap=nper * 3 // 4)
    mag = 20 * np.log10(np.abs(z) + 1e-9)
    mag -= mag.max()
    fy = np.geomspace(30, max_hz, height)[::-1]
    rows = np.searchsorted(f, fy).clip(0, len(f) - 1)
    img = mag[rows]
    cols = np.linspace(0, img.shape[1] - 1, width).astype(int)
    img = np.clip((img[:, cols] - floor_db) / -floor_db, 0, 1)
    rgb = np.stack([img ** 0.6, img ** 1.5, (1 - img) * img * 3 + img ** 4], axis=-1).clip(0, 1)
    wave_h = 60
    canvas = Image.new("RGB", (width, height + wave_h), (12, 12, 18))
    canvas.paste(Image.fromarray((rgb * 255).astype(np.uint8)), (0, 0))
    d = ImageDraw.Draw(canvas)
    seg = np.array_split(np.abs(m), width)
    for i, s in enumerate(seg):
        a = float(s.max(initial=0))
        h = int(a * (wave_h / 2 - 2))
        mid = height + wave_h // 2
        d.line([(i, mid - h), (i, mid + h)], fill=(120, 200, 255) if a < 0.99 else (255, 60, 60))
    for hz in (100, 1000, 10000):
        y = int(np.argmin(np.abs(fy - hz)))
        d.line([(0, y), (8, y)], fill=(255, 255, 255))
        d.text((10, y - 6), f"{hz if hz < 1000 else str(hz // 1000) + 'k'}", fill=(255, 255, 255))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
    return Path(path)
