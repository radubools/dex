"""Procedural sound effects for Many Builders: one seeded generator per game event,
several variations each, loopable ambiences, and packaging into the game's asset format.

    from utils import sfx
    x = sfx.render("shotgun-fire", seed=3, variation=1)        # stereo float array, mastered
    rep = sfx.build_sfx("shotgun-fire", "out/shotgun-fire")      # wavs + oggs + manifest, validated
    reps = sfx.build_all("out/sfx")                              # every sound in SOUNDS

Each sound is a function `gen(r, **params) -> mono or stereo array` in `SOUNDS`;
`render` seeds it per (seed, name, variation), then masters it to the category's
loudness target (`CATEGORIES`). Ambiences (`loop=True`) come out as exact,
seamless loops.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio as A
from . import catalog

#: Loudness target (integrated LUFS-ish, see `audio.loudness`) and peak ceiling per category.
CATEGORIES = {
    "ui": {"lufs": -22.0, "ceiling_db": -3.0},
    "building": {"lufs": -18.0, "ceiling_db": -1.0},
    "combat": {"lufs": -16.0, "ceiling_db": -1.0},
    "boss": {"lufs": -14.0, "ceiling_db": -1.0},
    "event": {"lufs": -16.0, "ceiling_db": -1.0},
    "ambience": {"lufs": -26.0, "ceiling_db": -6.0},
}
#: How far below target a short, spiky sound may land rather than be squashed (dB of limiting allowed).
MAX_LIMIT_DB = 6.0


@dataclass
class Sound:
    """A registered generator: its category, whether it loops, and a one-line description."""

    gen: Callable[..., np.ndarray]
    category: str
    description: str
    loop: bool = False
    params: dict = field(default_factory=dict)


SOUNDS: dict[str, Sound] = {}


def sound(name: str, category: str, description: str, loop: bool = False, **params):
    """Decorator registering a generator under `name` (kebab-case)."""
    def deco(fn):
        SOUNDS[name] = Sound(fn, category, description, loop, params)
        return fn
    return deco


def _j(r: np.random.Generator, base: float, spread: float) -> float:
    """`base` jittered by +-`spread` (fraction)."""
    return base * (1 + spread * (2 * r.random() - 1))


def _click(r, dur=0.004, lo=2000.0, hi=9000.0):
    return A.bandpass(A.noise(dur, "white", r), lo, hi) * A.perc(dur, 0.0003, dur / 3)


# ================================================================ building ===

@sound("hammer-hit", "building", "Hammer on a nail into timber: wood thud, nail ping, click.")
def _hammer_hit(r, pitch=1.0, material="wood"):
    d = 0.45
    p = pitch * _j(r, 1, 0.08)
    wood = A.modal([p * f for f in (190, 430, 720, 1150)], [0.06, 0.035, 0.02, 0.012], [1, 0.6, 0.35, 0.2], d, r)
    nail_f = p * _j(r, 2300, 0.15)
    ring = 0.35 if material == "wood" else 0.9
    nail = A.modal([nail_f * k for k in (1, 2.41, 3.93, 5.6)], [0.09 * ring, 0.05, 0.03, 0.02],
                   [0.5, 0.3, 0.18, 0.1], d, r)
    x = wood + nail + A.pad_to(_click(r) * 1.5, A.n_samples(d))
    x = A.reverb(A.pan(x, r.uniform(-0.3, 0.3)), decay=0.35, mix_=0.12, seed=int(r.integers(1e6)))
    return x


@sound("saw-stroke", "building", "One push-pull stroke of a hand saw through a plank.")
def _saw_stroke(r, strokes=1):
    out = []
    for _ in range(strokes):
        d = _j(r, 0.55, 0.15)
        t = A.times(d)
        teeth = 0.55 + 0.45 * np.abs(np.sin(2 * np.pi * _j(r, 38, 0.2) * t))
        shape = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 0.7
        n = A.bandpass(A.noise(d, "white", r), 1800, 7000) * teeth * shape
        tone = A.resonate(A.noise(d, "white", r), A.sweep(_j(r, 2400, 0.1), _j(r, 3100, 0.1), d), 18) * shape
        out.append(n + 0.8 * tone)
        out.append(np.zeros(A.n_samples(0.04)))
    x = np.concatenate(out)
    return A.reverb(A.pan(x, r.uniform(-0.4, 0.4)), decay=0.4, mix_=0.1, seed=int(r.integers(1e6)))


@sound("building-complete", "event", "Landmark finished: rising bell arpeggio, sparkle and a warm thump.")
def _building_complete(r):
    d = 1.9
    out = np.zeros((A.n_samples(d), 2))
    root = 72 + int(r.choice([0, 2, 5]))
    notes = [0, 4, 7, 12] if r.random() < 0.6 else [0, 7, 4, 12]
    for i, iv in enumerate(notes):
        f = A.midi_hz(root + iv)
        b = A.modal([f, f * 2.76, f * 5.4, f * 2], [0.9, 0.35, 0.15, 0.6], [1, 0.35, 0.15, 0.25], d - i * 0.09, r)
        A.place(out, A.pan(b, -0.4 + i * 0.27), i * 0.09)
    spark = A.highpass(A.noise(d, "white", r), 6000) * A.perc(d, 0.25, 0.25) * 0.25
    tw = np.zeros(A.n_samples(d))
    for _ in range(14):
        f = A.midi_hz(root + 24 + int(r.choice([0, 4, 7, 12])))
        A.place(tw, A.modal([f], [0.08], [0.25], 0.3, r), float(r.uniform(0.2, 1.2)))
    thump = A.osc(A.sweep(140, 60, 0.3), 0.3) * A.perc(0.3, 0.003, 0.08) * 0.8
    x = A.mix(out, A.widen(spark + tw, 11), thump)
    return A.reverb(x, decay=1.4, mix_=0.25, seed=int(r.integers(1e6)))


@sound("recruit-builder", "event", "A builder arrives: cheery two-note door chime and a 'hup!' grunt.")
def _recruit(r, squad=False):
    d = 0.9
    root = 76 + int(r.choice([0, 2, -1]))
    a, b = A.midi_hz(root), A.midi_hz(root + (4 if r.random() < 0.5 else 5))
    c1 = A.modal([a, a * 2.0, a * 3.01], [0.45, 0.2, 0.1], [1, 0.3, 0.12], d, r)
    c2 = A.modal([b, b * 2.0, b * 3.01], [0.5, 0.2, 0.1], [1, 0.3, 0.12], d - 0.13, r)
    x = A.pad_to(c1, A.n_samples(d))
    A.place(x, c2, 0.13)
    vd = 0.16
    f0 = A.sweep(_j(r, 190, 0.15), _j(r, 260, 0.15), vd)
    hup = A.formant(A.osc(f0, vd, "saw"), "u" if r.random() < 0.5 else "a") * A.adsr(vd - 0.06, 0.01, 0.05, 0.6, 0.06)
    hup = A.pad_to(hup, A.n_samples(vd))
    A.place(x, hup * 1.6, 0.3)
    reps = 3 if squad else 1
    out = np.zeros((A.n_samples(d + 0.12 * (reps - 1)), 2))
    for k in range(reps):
        A.place(out, A.pan(x, -0.3 + 0.3 * k), k * 0.12)
    return A.reverb(out, decay=0.6, mix_=0.15, seed=int(r.integers(1e6)))


# ================================================================== combat ===

@sound("shotgun-fire", "combat", "Builder's shotgun: blast, low thump, forest slap-back, pump rack.")
def _shotgun(r, pump=True):
    d = 1.1
    n = A.n_samples(d)
    blast_d = 0.35
    blast = A.lowpass(A.noise(blast_d, "white", r), _j(r, 5500, 0.15)) * A.perc(blast_d, 0.0005, _j(r, 0.045, 0.2))
    crack = A.highpass(A.noise(0.02, "white", r), 3000) * A.perc(0.02, 0.0002, 0.004) * 2
    thump = A.drive(A.osc(A.sweep(_j(r, 130, 0.1), 42, 0.3), 0.3) * A.perc(0.3, 0.001, 0.09), 2.5)
    x = np.zeros(n)
    A.place(x, blast * 2.2, 0)
    A.place(x, crack, 0)
    A.place(x, thump * 1.3, 0)
    slap = A.lowpass(A.pad_to(x, n), 2500)
    xs = A.pan(x, r.uniform(-0.15, 0.15))
    A.place(xs, A.pan(slap, -0.6) * 0.18, _j(r, 0.17, 0.2))
    A.place(xs, A.pan(slap, 0.6) * 0.12, _j(r, 0.26, 0.2))
    if pump:
        t0 = _j(r, 0.5, 0.08)
        for k, (f, g) in enumerate(((_j(r, 2900, 0.1), 1.0), (_j(r, 2300, 0.1), 1.2))):
            slide = A.bandpass(A.noise(0.06, "white", r), 1200, 5000) * A.perc(0.06, 0.01, 0.02) * 0.6
            clk = A.modal([f, f * 1.7, f * 2.9], [0.012, 0.008, 0.005], [1, 0.6, 0.4], 0.08, r) + A.pad_to(_click(r, 0.006), A.n_samples(0.08)) * 1.5
            A.place(xs, A.pan(A.pad_to(slide, len(clk)) + clk * g, 0.25) * 0.5, t0 + k * 0.13)
    return A.reverb(xs, decay=0.9, mix_=0.18, tone=4500, seed=int(r.integers(1e6)))


@sound("turret-fire", "combat", "Auto turret: snappy 'pew' zap, mechanical clack, servo whir.")
def _turret(r):
    d = 0.4
    zd = 0.09
    zap = A.osc(A.sweep(_j(r, 1700, 0.12), _j(r, 260, 0.12), zd), zd, "square", pw=0.35) * A.perc(zd, 0.0005, 0.03)
    zap = A.lowpass(zap, 6000)
    body = A.lowpass(A.noise(0.12, "white", r), 2500) * A.perc(0.12, 0.0005, 0.02)
    clack = A.modal([_j(r, 3400, 0.1), 5100, 7300], [0.01, 0.007, 0.004], [0.5, 0.3, 0.2], 0.05, r)
    wd = 0.22
    whir = A.lowpass(A.osc(A.sweep(110, 75, wd), wd, "saw"), 600) * A.adsr(wd - 0.08, 0.02, 0.05, 0.6, 0.08)[:A.n_samples(wd)] * 0.2
    x = np.zeros(A.n_samples(d))
    A.place(x, zap, 0)
    A.place(x, body * 1.2, 0)
    A.place(x, clack, 0.004)
    A.place(x, A.pad_to(whir, A.n_samples(wd)), 0.07)
    return A.reverb(A.pan(x, r.uniform(-0.3, 0.3)), decay=0.5, mix_=0.15, seed=int(r.integers(1e6)))


@sound("wave-warning", "event", "Clone wave announced: comic two-note war horn over a taiko hit.")
def _wave_warning(r):
    d = 2.3
    out = np.zeros((A.n_samples(d), 2))
    base = _j(r, 98, 0.04)
    for k, (f, t0, nd) in enumerate(((base, 0.0, 0.55), (base * 4 / 3, 0.65, 1.1))):
        fr = A.vibrato(f, nd + 0.25, rate=_j(r, 5, 0.1), depth=0.15, delay=0.2)
        horn = A.supersaw(fr * A.sweep(0.97, 1.0, nd + 0.25), nd + 0.25, 3, 10, r)
        cut = A.envelope([(0, 300), (0.12, 1800), (nd, 1300), (nd + 0.25, 400)], nd + 0.25)
        horn = A.lowpass(horn, cut, q=1.2) * A.adsr(nd, 0.06, 0.2, 0.85, 0.25)[:A.n_samples(nd + 0.25)]
        horn = A.formant(horn, "o", 3) * 1.5 + horn * 0.4
        A.place(out, A.pan(horn, -0.15 + 0.3 * k), t0)
    taiko = A.osc(A.sweep(85, 52, 0.8), 0.8) * A.perc(0.8, 0.002, 0.22) + \
        A.lowpass(A.noise(0.8, "white", r), 400) * A.perc(0.8, 0.001, 0.04) * 2
    A.place(out, A.stereo(taiko) * 0.9, 0.0)
    A.place(out, A.stereo(taiko) * 1.1, 0.65)
    return A.reverb(out, decay=1.8, mix_=0.3, tone=5000, seed=int(r.integers(1e6)))


@sound("clone-spawn", "combat", "A Patrick clone pops into the world: rubbery bloop.")
def _clone_spawn(r):
    d = 0.35
    f = A.sweep(_j(r, 180, 0.2), _j(r, 650, 0.2), d)
    f = f * (1 + 0.08 * np.sin(2 * np.pi * _j(r, 22, 0.2) * A.times(d)))
    x = A.osc(f, d) * A.envelope([(0, 0), (0.01, 1), (0.18, 0.6), (d, 0)], d)
    x += 0.3 * A.osc(f * 2, d, "triangle") * A.perc(d, 0.005, 0.06)
    squish = A.resonate(A.noise(d, "white", r), A.sweep(500, 1600, d), 4) * A.perc(d, 0.01, 0.07) * 0.8
    return A.reverb(A.pan(x + squish, r.uniform(-0.5, 0.5)), decay=0.4, mix_=0.12, seed=int(r.integers(1e6)))


@sound("clone-hit", "combat", "A clone takes a hit: soft thwack and a squelch.")
def _clone_hit(r):
    d = 0.3
    thwack = A.lowpass(A.noise(d, "white", r), 1800) * A.perc(d, 0.0005, 0.025) * 2.5
    squelch = A.resonate(A.noise(d, "pink", r), A.sweep(_j(r, 1400, 0.2), _j(r, 350, 0.2), d), 6) * A.perc(d, 0.004, 0.08) * 3
    squeak = A.osc(A.sweep(_j(r, 900, 0.2), _j(r, 1300, 0.2), 0.08), 0.08, "triangle") * A.perc(0.08, 0.002, 0.03) * 0.3
    x = thwack + squelch + A.pad_to(squeak, A.n_samples(d))
    return A.reverb(A.pan(x, r.uniform(-0.4, 0.4)), decay=0.3, mix_=0.1, seed=int(r.integers(1e6)))


@sound("clone-death", "combat", "A clone deflates like a balloon, then pops.")
def _clone_death(r):
    d = _j(r, 0.6, 0.15)
    t = A.times(d)
    f = A.sweep(_j(r, 950, 0.15), _j(r, 260, 0.15), d) * (1 + 0.06 * np.sin(2 * np.pi * _j(r, 17, 0.2) * t))
    tone = A.osc(f, d, "square", pw=0.3)
    tone = A.formant(A.lowpass(tone, 3000), "e", 5) * A.envelope([(0, 0), (0.02, 1), (d * 0.8, 0.7), (d, 0)], d)
    air = A.bandpass(A.noise(d, "white", r), 1500, 6000) * np.linspace(0.4, 0.1, len(t)) * 0.4
    pd = 0.12
    pop = A.lowpass(A.noise(pd, "white", r), 4000) * A.perc(pd, 0.0003, 0.012) * 3
    x = A.pad_to(tone + air, A.n_samples(d + pd))
    A.place(x, pop, d - 0.01)
    return A.reverb(A.pan(x, r.uniform(-0.4, 0.4)), decay=0.5, mix_=0.15, seed=int(r.integers(1e6)))


@sound("bunker-damage", "combat", "The bunker takes a blow: metal clang, crunch, low thud.")
def _bunker_damage(r):
    d = 1.0
    f0 = _j(r, 310, 0.15)
    clang = A.modal([f0 * k for k in (1, 2.53, 3.97, 5.81, 8.4, 11.2)], [0.45, 0.32, 0.24, 0.16, 0.1, 0.06],
                    [1, 0.7, 0.5, 0.35, 0.2, 0.12], d, r)
    t = A.times(d)
    grit = (np.abs(np.sin(2 * np.pi * _j(r, 60, 0.3) * t)) > 0.6).astype(float)
    crunch = A.bandpass(A.noise(d, "white", r), 500, 2500) * A.perc(d, 0.001, 0.09) * (0.5 + grit) * 1.4
    thud = A.osc(A.sweep(110, 45, d), d) * A.perc(d, 0.001, 0.1) * 1.2
    x = clang * 0.6 + crunch + thud
    return A.reverb(A.pan(x, r.uniform(-0.2, 0.2)), decay=0.8, mix_=0.2, seed=int(r.integers(1e6)))


# ==================================================================== boss ===

@sound("boss-footstep", "boss", "Angry Patrick approaching: a distant, ground-shaking footfall.")
def _boss_footstep(r, distance=0.5):
    d = 1.6
    sub = A.osc(A.sweep(_j(r, 62, 0.1), 30, d), d) * A.perc(d, 0.004, 0.35)
    rumble = A.lowpass(A.noise(d, "brown", r), 180) * A.perc(d, 0.02, 0.5) * 2.5
    thud = A.lowpass(A.noise(0.2, "white", r), 600 * (1.5 - distance)) * A.perc(0.2, 0.001, 0.03) * 2
    x = sub * 1.4 + rumble
    A.place(x, thud, 0)
    for _ in range(int(r.integers(4, 9))):  # debris rattle
        g = A.bandpass(A.noise(0.03, "white", r), 2000, 6000) * A.perc(0.03, 0.001, 0.006) * r.uniform(0.05, 0.2)
        A.place(x, g, float(r.uniform(0.05, 0.5)))
    x = A.lowpass(x, 2500 * (1.2 - distance))
    return A.reverb(A.pan(x, r.uniform(-0.3, 0.3)), decay=2.0, mix_=0.25, tone=2500, seed=int(r.integers(1e6)))


@sound("boss-roar", "boss", "Angry Patrick bellows: growling formant roar with a comic wobble.")
def _boss_roar(r):
    d = _j(r, 1.6, 0.1)
    t = A.times(d)
    f = A.envelope([(0, _j(r, 85, 0.1)), (0.25, _j(r, 120, 0.1)), (d * 0.7, 105), (d, 70)], d)
    f = f * (1 + 0.04 * np.sin(2 * np.pi * 6 * t) + 0.02 * r.standard_normal(len(t)).cumsum() / np.sqrt(len(t)))
    src = A.supersaw(f, d, 3, 25, r) * (0.7 + 0.3 * np.sin(2 * np.pi * _j(r, 31, 0.1) * t))
    vowel = A.formant(src, "a", 4) * 1.2 + A.formant(src, "o", 4) * np.linspace(0, 1, len(t))
    breath = A.bandpass(A.noise(d, "white", r), 600, 3500) * 0.4
    x = A.drive((vowel + breath) * A.adsr(d - 0.3, 0.08, 0.3, 0.85, 0.3)[:len(t)], 2.0)
    sub = A.osc(f / 2, d) * A.adsr(d - 0.3, 0.1, 0.3, 0.7, 0.3)[:len(t)] * 0.5
    return A.reverb(A.pan(x + sub, 0), decay=1.6, mix_=0.3, tone=5000, seed=int(r.integers(1e6)))


@sound("boss-slam", "boss", "Angry Patrick's ground slam: sub drop, blast, crack, falling debris.")
def _boss_slam(r):
    d = 2.6
    n = A.n_samples(d)
    sub = A.drive(A.osc(A.sweep(_j(r, 75, 0.1), 24, 1.2), 1.2) * A.perc(1.2, 0.002, 0.35), 2)
    blast = A.lowpass(A.noise(1.2, "white", r), A.sweep(4000, 300, 1.2)) * A.perc(1.2, 0.001, 0.15) * 2.4
    crack = A.highpass(A.noise(0.05, "white", r), 2500) * A.perc(0.05, 0.0002, 0.01) * 2.5
    x = np.zeros(n)
    for s in (sub * 1.5, blast, crack):
        A.place(x, s, 0)
    for _ in range(int(r.integers(18, 30))):
        t0 = float(r.uniform(0.15, 1.8))
        g = A.bandpass(A.noise(0.05, "white", r), r.uniform(800, 2500), 7000) * A.perc(0.05, 0.0005, 0.01)
        A.place(x, g * r.uniform(0.1, 0.4) * (1 - t0 / 2.2), t0)
    xs = A.pan(x, 0)
    return A.reverb(xs, decay=2.4, mix_=0.35, tone=3500, seed=int(r.integers(1e6)))


# ====================================================================== ui ===

@sound("ui-click", "ui", "Button press: tiny wooden tick.")
def _ui_click(r):
    d = 0.07
    f = _j(r, 2600, 0.06)
    x = A.modal([f, f * 1.6, 420], [0.008, 0.005, 0.012], [0.7, 0.3, 0.5], d, r) + A.pad_to(_click(r, 0.003), A.n_samples(d)) * 0.6
    return A.pan(x, 0)


def _blips(r, up: bool, d=0.16):
    a, b = (_j(r, 880, 0.03), _j(r, 1320, 0.03)) if up else (_j(r, 1320, 0.03), _j(r, 880, 0.03))
    x = np.zeros(A.n_samples(d))
    for i, f in enumerate((a, b)):
        A.place(x, A.osc(f, 0.07, "triangle") * A.perc(0.07, 0.002, 0.025), i * 0.06)
    return A.pan(A.lowpass(x, 5000), 0)


@sound("ui-toggle-on", "ui", "Toggle or panel opens: two blips going up.")
def _ui_on(r):
    return _blips(r, True)


@sound("ui-toggle-off", "ui", "Toggle or panel closes: two blips going down.")
def _ui_off(r):
    return _blips(r, False)


@sound("ui-deny", "ui", "Not allowed / not enough funds: soft low double buzz.")
def _ui_deny(r):
    d = 0.3
    x = np.zeros(A.n_samples(d))
    f = _j(r, 175, 0.05)
    for i in range(2):
        A.place(x, A.lowpass(A.osc(f, 0.09, "square"), 1200) * A.adsr(0.06, 0.005, 0.05, 0.8, 0.03)[:A.n_samples(0.09)], i * 0.12)
    return A.pan(x, 0)


def _tape(r, down: bool, d=0.45):
    d = _j(r, d, 0.1)
    hi, lo = _j(r, 660, 0.06), _j(r, 160, 0.06)
    f = A.sweep(*((hi, lo) if down else (lo, hi)), d)
    chord = sum(A.osc(f * k, d, "triangle") * g for k, g in ((1, 1), (1.5, 0.5), (2, 0.3)))
    env = A.envelope([(0, 0), (0.01, 1), (d * 0.7, 0.6 if down else 1), (d, 0)], d)
    cut = A.sweep(*((4000, 300) if down else (300, 4000)), d)
    return A.pan(A.lowpass(chord * env, cut), 0)


@sound("pause", "ui", "Game paused: tape-stop slide down.")
def _pause(r):
    return _tape(r, True)


@sound("unpause", "ui", "Game resumed: slide back up to speed.")
def _unpause(r):
    return _tape(r, False)


@sound("save-game", "ui", "Game saved: pencil scribble and a settled two-note chime.")
def _save(r):
    d = 0.9
    x = np.zeros(A.n_samples(d))
    for k in range(3):
        sd = 0.07
        s = A.bandpass(A.noise(sd, "white", r), 2500, 8000) * A.envelope([(0, 0), (0.02, 1), (sd, 0)], sd) * 0.35
        A.place(x, s, k * 0.06)
    for i, m in enumerate((76, 81)):
        f = A.midi_hz(m + int(r.choice([0, 0, 2])))
        A.place(x, A.modal([f, f * 2, f * 3], [0.4, 0.15, 0.08], [1, 0.25, 0.1], d - 0.2 - i * 0.12, r) * 0.6, 0.2 + i * 0.12)
    return A.reverb(A.pan(x, 0), decay=0.6, mix_=0.15, seed=int(r.integers(1e6)))


@sound("load-game", "ui", "Save loaded: page whoosh into a rising three-note shimmer.")
def _load(r):
    d = 1.0
    wd = 0.35
    whoosh = A.lowpass(A.noise(wd, "pink", r), A.sweep(400, 5000, wd)) * A.envelope([(0, 0), (wd * 0.7, 1), (wd, 0)], wd) * 0.6
    x = A.pad_to(whoosh, A.n_samples(d))
    root = 72 + int(r.choice([0, 2]))
    for i, iv in enumerate((0, 7, 12)):
        f = A.midi_hz(root + iv)
        A.place(x, A.modal([f, f * 2.76, f * 2], [0.35, 0.12, 0.2], [1, 0.25, 0.2], d - 0.3 - i * 0.07, r) * 0.6, 0.28 + i * 0.07)
    return A.reverb(A.widen(x, 7), decay=0.8, mix_=0.2, seed=int(r.integers(1e6)))


@sound("day-tick", "ui", "A new day on the calendar: very soft wood-block tick with a page flick.")
def _day_tick(r):
    d = 0.18
    f = _j(r, 1150, 0.05)
    x = A.modal([f, f * 2.3], [0.03, 0.012], [1, 0.3], d, r) * 0.6
    flick = A.bandpass(A.noise(0.05, "white", r), 1800, 6000) * A.perc(0.05, 0.003, 0.012) * 0.12
    A.place(x, flick, 0.03)
    return A.pan(x, r.uniform(-0.1, 0.1))


# ================================================================== events ===

@sound("christmas-deadline", "event", "Countdown warning (days to Christmas): a deep church-bell toll with jingle shimmer.")
def _deadline(r):
    d = 4.0
    f = _j(r, 196, 0.03)
    ratios = (0.5, 1.0, 1.183, 1.506, 2.0, 2.514, 2.662, 3.011, 4.166)
    decays = (2.5, 2.0, 1.4, 1.1, 0.9, 0.6, 0.55, 0.45, 0.3)
    amps = (0.6, 1.0, 0.5, 0.45, 0.5, 0.25, 0.2, 0.15, 0.1)
    bell = A.modal([f * k for k in ratios], decays, amps, d, r)
    bell = bell * (1 + 0.15 * np.sin(2 * np.pi * 2.2 * A.times(d)))  # slow beating
    strike = A.lowpass(A.noise(0.03, "white", r), 3000) * A.perc(0.03, 0.0005, 0.006) * 0.8
    A.place(bell, strike, 0)
    jingle = _jingle(r, 0.8) * 0.2
    x = A.mix(A.pan(bell, 0), A.widen(A.pad_to(jingle, A.n_samples(1.0)), 9))
    return A.reverb(x, decay=2.8, mix_=0.3, tone=5000, seed=int(r.integers(1e6)))


def _jingle(r, dur=0.5, density=40):
    """Sleigh-bell shake: many tiny high bells with randomised onsets."""
    x = np.zeros(A.n_samples(dur + 0.15))
    for _ in range(int(density * dur)):
        f = r.uniform(4200, 8500)
        b = A.modal([f, f * 1.47], [r.uniform(0.02, 0.06), 0.02], [1, 0.4], 0.12, r)
        A.place(x, b * r.uniform(0.2, 1), float(r.uniform(0, dur)))
    return x


@sound("victory", "event", "Tokyo finished by Christmas: party-horn toot, confetti pops, sleigh bells.")
def _victory(r):
    d = 2.6
    out = np.zeros((A.n_samples(d), 2))
    hd = 0.45
    horn = A.formant(A.osc(A.sweep(_j(r, 520, 0.05), 700, hd), hd, "square", pw=0.4), "i", 4)
    horn = horn * A.envelope([(0, 0), (0.02, 1), (hd - 0.05, 0.9), (hd, 0)], hd)
    A.place(out, A.pan(horn * 1.4, 0), 0)
    for _ in range(int(r.integers(6, 10))):
        pd = 0.06
        p = A.bandpass(A.noise(pd, "white", r), 800, 6000) * A.perc(pd, 0.0003, 0.008)
        A.place(out, A.pan(p * r.uniform(0.5, 1.2), r.uniform(-0.8, 0.8)), float(r.uniform(0.35, 1.0)))
    root = 72
    for i, iv in enumerate((0, 4, 7, 12, 16)):
        f = A.midi_hz(root + iv)
        A.place(out, A.pan(A.modal([f, f * 2.76, f * 2], [0.8, 0.3, 0.5], [1, 0.3, 0.2], 1.6, r) * 0.5, -0.5 + i * 0.25), 0.45 + i * 0.08)
    A.place(out, A.widen(_jingle(r, 1.4, 50) * 0.5, 9), 0.4)
    return A.reverb(out, decay=1.6, mix_=0.25, seed=int(r.integers(1e6)))


@sound("defeat", "event", "Game over: sad trombone 'wah wah wah waaah'.")
def _defeat(r):
    notes = [(58, 0.42), (57, 0.42), (56, 0.42), (55, 1.4)]
    out = []
    for i, (m, nd) in enumerate(notes):
        f = A.midi_hz(m) * A.sweep(1.02, 1.0, nd)
        if i == 3:
            f = A.vibrato(f, nd, rate=5.0, depth=0.35, delay=0.3)
        src = A.supersaw(f, nd, 2, 6, r)
        wah = A.envelope([(0, 350), (0.12, 1600), (nd, 500)], nd)
        tone = A.lowpass(src, wah, q=3) * A.adsr(nd - 0.08, 0.03, 0.1, 0.85, 0.08)[:A.n_samples(nd)]
        out.append(A.formant(tone, "o", 3) * 0.8 + tone * 0.5)
        out.append(np.zeros(A.n_samples(0.03)))
    x = np.concatenate(out)
    return A.reverb(A.pan(x, 0), decay=1.2, mix_=0.2, seed=int(r.integers(1e6)))


# =============================================================== ambiences ===

@sound("ambience-wind", "ambience", "Snowy wind loop: gusting whoosh, howling edges, faint snow hiss.", loop=True)
def _amb_wind(r, seconds=20.0, intensity=0.6):
    L = A.n_samples(seconds)
    ext = A.n_samples(seconds + 1)
    chans = []
    shared = A.smooth_random(seconds, 0.15, r, loop=True)

    def periodic(c):  # a loop-periodic control curve stretched over the crossfade margin
        return np.tile(c, 2)[:ext]

    for c in range(2):
        g = 0.7 * shared + 0.3 * A.smooth_random(seconds, 0.35, r, loop=True)
        gust = 0.12 + 0.88 * g ** 1.6 * (0.6 + 0.6 * intensity)
        whoosh = A.lowpass(A.noise(seconds + 1, "pink", r), periodic(250 + 1400 * gust), q=0.9)
        howl = A.resonate(A.noise(seconds + 1, "pink", r), periodic(350 + 700 * gust), 2.5)
        wf = periodic(650 + 1100 * A.smooth_random(seconds, 0.25, r, loop=True))
        whistle = A.resonate(A.noise(seconds + 1, "white", r), wf, 14)
        hiss = A.lowpass(A.highpass(A.noise(seconds + 1, "white", r), 4000), 9000)
        body = A.make_loop(whoosh, L, 1.0) * (0.3 + gust) + A.make_loop(howl, L, 1.0) * gust * 1.2
        chans.append(body + A.make_loop(whistle, L, 1.0) * gust ** 3 * 1.5
                     + A.make_loop(hiss, L, 1.0) * 0.03 * (0.5 + gust))
    return np.stack(chans, axis=1)


def _loop_events(r, seconds, every, make, spread=0.8):
    """Events at random times (wrapped into the loop) from `make(r) -> stereo`."""
    L = A.n_samples(seconds)
    out = np.zeros((L, 2))
    t = float(r.uniform(0, every))
    while t < seconds:
        A.place(out, make(r), A.n_samples(t), wrap=True)
        t += every * r.uniform(1 - spread, 1 + spread)
    return out


def _bird(r):
    # Chickadee-ish "fee-bee" whistle, or a short chirp trill.
    if r.random() < 0.5:
        f = r.uniform(3200, 4200)
        x = np.zeros(A.n_samples(0.7))
        for i, (ff, nd) in enumerate(((f, 0.22), (f * 0.85, 0.28))):
            A.place(x, A.osc(A.vibrato(ff, nd, 30, 0.2), nd) * A.envelope([(0, 0), (0.03, 1), (nd - 0.04, 0.8), (nd, 0)], nd), i * 0.3)
    else:
        x = np.zeros(A.n_samples(0.5))
        for i in range(int(r.integers(3, 7))):
            nd = 0.05
            A.place(x, A.osc(A.sweep(r.uniform(3500, 5000), r.uniform(2500, 3500), nd), nd) * A.perc(nd, 0.005, 0.02), i * 0.065)
    return A.pan(A.lowpass(x, 7000) * r.uniform(0.08, 0.2), r.uniform(-0.9, 0.9))


def _creak(r):
    d = r.uniform(0.5, 1.0)
    f = r.uniform(70, 140) * (1 + 0.15 * A.smooth_random(d, 6, r))
    x = A.bandpass(A.osc(f, d, "saw"), 300, 1800) * A.envelope([(0, 0), (0.1, 1), (d - 0.1, 0.7), (d, 0)], d)
    return A.pan(x * r.uniform(0.04, 0.08), r.uniform(-0.8, 0.8))


def _woodpecker(r):
    x = np.zeros(A.n_samples(0.9))
    f = r.uniform(900, 1300)
    for i in range(int(r.integers(8, 16))):
        A.place(x, A.modal([f, f * 2.2], [0.012, 0.006], [1, 0.4], 0.04, r), i * 0.045)
    return A.pan(A.lowpass(x, 4000) * 0.05, r.uniform(-0.9, 0.9))


@sound("ambience-forest", "ambience", "Winter forest loop: soft rustle, distant birds, tree creaks, a woodpecker.", loop=True)
def _amb_forest(r, seconds=20.0, birds=1.0):
    L = A.n_samples(seconds)
    bed = []
    for c in range(2):
        rustle = A.make_loop(A.bandpass(A.noise(seconds + 1, "pink", r), 900, 6000), L, 1.0)
        breeze = A.make_loop(A.bandpass(A.noise(seconds + 1, "pink", r), 120, 700), L, 1.0)
        m = 0.3 + 0.7 * A.smooth_random(seconds, 0.25, r, loop=True)
        bed.append(rustle * m ** 2 * 0.22 + breeze * 0.35 * (0.6 + 0.4 * m))
    x = np.stack(bed, axis=1)
    x += _loop_events(r, seconds, 3.0 / max(birds, 0.1), _bird)
    x += _loop_events(r, seconds, 7.0, _creak)
    if r.random() < 0.8:
        x += _loop_events(r, seconds, 12.0, _woodpecker, 0.3)
    return A.reverb(x, decay=2.0, mix_=0.25, tone=6000, seed=int(r.integers(1e6)), circular=True)


def _far(x, r):
    return A.pan(A.lowpass(A.stereo(x) if x.ndim == 1 else x, 2500), r.uniform(-0.9, 0.9))


@sound("ambience-construction", "ambience", "Building-site bustle loop: distant hammering, saws, clanks and crew murmur.", loop=True)
def _amb_construction(r, seconds=20.0, busy=1.0):
    L = A.n_samples(seconds)
    chans = []
    for c in range(2):
        src = A.noise(seconds + 1, "pink", r)
        syll = 0.3 + 0.7 * (0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(3.5 + 2 * A.smooth_random(seconds + 1, 1.5, r)) / A.SR))
        murmur = A.formant(src, ("a", "o")[c], 3) * syll
        murmur = A.make_loop(A.bandpass(murmur, 250, 2000), L, 1.0) * 0.6
        bed = A.make_loop(A.bandpass(A.noise(seconds + 1, "brown", r), 60, 300), L, 1.0) * 0.3
        chans.append(murmur + bed)
    x = np.stack(chans, axis=1)
    hammer = SOUNDS["hammer-hit"].gen
    saw = SOUNDS["saw-stroke"].gen

    def hammering(r):
        n = int(r.integers(2, 6))
        gap = r.uniform(0.3, 0.5)
        out = np.zeros((A.n_samples(n * gap + 0.6), 2))
        for i in range(n):
            A.place(out, A.stereo(A.mono(hammer(r))), i * gap * r.uniform(0.95, 1.05))
        return _far(out * r.uniform(0.15, 0.35), r)

    def clank(r):
        f = r.uniform(400, 700)
        y = A.modal([f, f * 2.5, f * 4.1], [0.25, 0.15, 0.08], [1, 0.5, 0.3], 0.8, r)
        return _far(y * 0.12, r)

    x += _loop_events(r, seconds, 2.2 / busy, hammering)
    x += _loop_events(r, seconds, 6.0 / busy, lambda r: _far(A.mono(saw(r, strokes=int(r.integers(2, 4)))) * 0.15, r))
    x += _loop_events(r, seconds, 5.0 / busy, clank)
    return A.reverb(x, decay=1.6, mix_=0.3, tone=4000, seed=int(r.integers(1e6)), circular=True)


# =================================================================== build ===

def render(name: str, seed: int = 0, variation: int = 0, master: bool = True, **params) -> np.ndarray:
    """One mastered (stereo, category-loudness, faded or seamlessly looped) take of sound `name`."""
    s = SOUNDS[name]
    r = A.rng(seed, name, variation)
    x = np.asarray(s.gen(r, **{**s.params, **params}), float)
    if not s.loop:
        x = A.trim_silence(x, -60, 0.03)
    if not master:
        return A.stereo(x)
    cat = CATEGORIES[s.category]
    return A.master(x, cat["lufs"], cat["ceiling_db"], MAX_LIMIT_DB, loop=s.loop,
                    fade_out=0.02 if not s.loop else 0)


def build_sfx(name: str, out_dir: str | Path, variations: int = 3, seed: int = 0, previews: bool = True,
              **params) -> dict:
    """Write `<name>_<n>.wav/.ogg` (n = 1..variations) and a `kind="sfx"` manifest; returns `catalog.validate_package`."""
    s = SOUNDS[name]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files, takes = [], []
    for v in range(variations):
        x = render(name, seed, v, **params)
        stem = out / f"{name}_{v + 1}"
        wav, ogg = A.write_pair(stem, x)
        files += [wav.name, ogg.name]
        m = A.measure(x, loop=s.loop)
        take = {"wav": wav.name, "ogg": ogg.name, **{k: m[k] for k in ("duration", "peak_db", "rms_db", "lufs", "centroid_hz")}}
        if s.loop:
            take.update(loop_start=0, loop_end=m["samples"], seam_ratio=m["seam"]["ratio"])
        takes.append(take)
        if previews and v == 0:
            A.spectrogram_png(x, out / "previews" / f"{name}_1.png")
    cat = CATEGORIES[s.category]
    durs = [t["duration"] for t in takes]
    catalog.write_manifest(
        out, name=name, kind="sfx", files=files, title=name.replace("-", " ").title(),
        description=s.description, category=s.category, loop=s.loop, variations=variations,
        duration=max(durs), durations=durs, sample_rate=A.SR, channels=2, seed=seed, params=params,
        loudness={"target_lufs": cat["lufs"], "ceiling_db": cat["ceiling_db"],
                  "lufs": [t["lufs"] for t in takes], "peak_db": [t["peak_db"] for t in takes]},
        takes=takes)
    return catalog.validate_package(out)


def build_all(out_root: str | Path, variations: int = 3, seed: int = 0, names: list[str] | None = None) -> dict:
    """Build every sound in `SOUNDS` (or `names`) into `<out_root>/<name>/`; returns `{name: report}`."""
    root = Path(out_root)
    reports = {}
    for name in names or list(SOUNDS):
        t0 = time.time()
        rep = build_sfx(name, root / name, variations, seed)
        rep["seconds"] = round(time.time() - t0, 2)
        reports[name] = rep
    (root / "sfx-index.json").write_text(json.dumps(
        {n: {"category": SOUNDS[n].category, "loop": SOUNDS[n].loop, "ok": r["ok"], "description": SOUNDS[n].description}
         for n, r in reports.items()}, indent=2) + "\n")
    return reports


def describe() -> list[dict]:
    """Name, category, loop flag and description of every registered sound."""
    return [{"name": n, "category": s.category, "loop": s.loop, "description": s.description} for n, s in SOUNDS.items()]
