"""A small seeded procedural composer for game music: seamless loops in layered
intensity stems, plus short stingers. Needs the game-sfx skill (`utils.audio`).

    from utils import compose
    rep = compose.build_track("winter-christmas", "out/winter-christmas", mood="winter")
    song = compose.compose_song("combat", seed=4)        # notes only (inspect / tweak)
    mix, stems = compose.render_song(song)               # stereo arrays, exact loop length
    compose.build_stinger("stinger-victory", "out/stinger-victory", "victory")
    compose.build_all("out/music")

A track is `bars` bars of `meter` beats at `bpm`; its loop is exactly
`loop_samples = round(bars * meter * 60 / bpm * SR)` long. Notes whose tails
run past the end (and the reverb tail) are wrapped onto the start, so the file
loops click-free from its last sample straight into its first.

Stems are additive layers of the same loop: `calm` (pad, bass, melody, light
percussion), `tension` (arpeggio, ostinato, pulse bass, ticking hats) and
`combat` (full drums, brass stabs, fills). Play calm alone, calm+tension, or
all three; the full mix file is all three summed.
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import audio as A
from . import catalog
from . import instruments as I

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10], "dorian": [0, 2, 3, 5, 7, 9, 10],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10], "harmonic_minor": [0, 2, 3, 5, 7, 8, 11], "lydian": [0, 2, 4, 6, 7, 9, 11],
}
PITCH = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7,
         "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII"]
STEMS = ("calm", "tension", "combat")
#: Loudness of the full mix (all stems) and of stingers; the ceiling holds for every layer combination.
MUSIC_LUFS = -16.0
STINGER_LUFS = -16.0
CEILING_DB = -1.0
#: Loudness of the tension and combat layers relative to calm (LU), before the common master gain.
STEM_BALANCE = {"tension": -3.0, "combat": -1.5}

#: Mood presets. `prog_a`/`prog_b` are scale degrees (0 = tonic), one chord per bar.
MOODS: dict[str, dict] = {
    "title": dict(root="D", scale="major", bpm=92, meter=4, bars=16, prog_a=[0, 4, 5, 3], prog_b=[3, 0, 3, 4],
                  lead="bell", lead_range=(72, 90), pad="pad", bass="bass", pulse="bass", arp="pluck",
                  ostinato="spiccato", stabs="brass", light="sleigh", light_style="sparse", groove="march",
                  reverb=(2.4, 0.35), swing=0.0, density=0.5),
    "summer": dict(root="G", scale="major", bpm=108, meter=4, bars=16, prog_a=[0, 3, 4, 3], prog_b=[5, 3, 0, 4],
                   lead="marimba", lead_range=(67, 86), pad="pad", bass="bass", pulse="bass", arp="pluck",
                   ostinato="spiccato", stabs="brass", light="shaker", light_style="eighths", groove="march",
                   reverb=(1.4, 0.22), swing=0.12, density=0.7),
    "autumn": dict(root="E", scale="dorian", bpm=84, meter=3, bars=16, prog_a=[0, 6, 5, 6], prog_b=[3, 0, 4, 6],
                   lead="flute", lead_range=(64, 84), pad="strings", bass="bass", pulse="bass", arp="pluck",
                   ostinato="spiccato", stabs="brass", light="brush", light_style="waltz", groove="waltz",
                   reverb=(2.0, 0.3), swing=0.0, density=0.45),
    "winter": dict(root="F", scale="major", bpm=100, meter=4, bars=16, prog_a=[0, 5, 3, 4], prog_b=[3, 0, 1, 4],
                   lead="bell", lead_range=(72, 91), pad="choir", bass="bass", pulse="bass", arp="music_box",
                   ostinato="spiccato", stabs="brass", light="sleigh", light_style="eighths", groove="march",
                   reverb=(2.6, 0.35), swing=0.0, density=0.55),
    "combat": dict(root="D", scale="minor", bpm=132, meter=4, bars=8, prog_a=[0, 5, 6, 0], prog_b=[0, 5, 3, 4],
                   lead="brass", lead_range=(62, 79), pad="strings", bass="bass", pulse="synth_bass", arp="pluck",
                   ostinato="spiccato", stabs="brass", light="hat", light_style="eighths", groove="drive",
                   reverb=(1.4, 0.2), swing=0.0, density=0.65),
    "boss": dict(root="C", scale="harmonic_minor", bpm=120, meter=4, bars=8, prog_a=[0, 3, 4, 0], prog_b=[5, 1, 4, 4],
                 lead="brass", lead_range=(55, 74), pad="choir", bass="synth_bass", pulse="synth_bass", arp="spiccato",
                 ostinato="spiccato", stabs="brass", light="taiko", light_style="heartbeat", groove="taiko",
                 reverb=(2.2, 0.3), swing=0.0, density=0.5),
}


@dataclass
class Note:
    """One event: start and length in beats, MIDI pitch (ignored for drums), velocity, instrument, pan."""

    beat: float
    dur: float
    midi: float
    vel: float
    inst: str
    pan: float = 0.0
    drum: bool = False


@dataclass
class Song:
    """A composed loop: its parameters and the notes of each stem."""

    mood: str
    bpm: float
    bars: int
    meter: int
    root: str
    scale: str
    seed: int
    progression: list[int]
    sections: list[dict]
    stems: dict[str, list[Note]] = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    @property
    def loop_samples(self) -> int:
        """Exact loop length in samples (bar-aligned)."""
        return int(round(self.bars * self.meter * 60.0 / self.bpm * A.SR))


class Key:
    """A key: tonic pitch class and a 7-note scale, with degree <-> MIDI helpers."""

    def __init__(self, root: str, scale: str):
        self.root = PITCH[root]
        self.steps = SCALES[scale]

    def midi(self, degree: int, octave: int = 4) -> int:
        """MIDI note of scale `degree` (any integer; 7 = an octave up) counted from the tonic in `octave`."""
        o, i = divmod(degree, 7)
        return 12 * (octave + 1 + o) + self.root + self.steps[i]

    def chord(self, degree: int, octave: int = 4, size: int = 3) -> list[int]:
        """Triad (or 7th with size=4) on `degree`, stacked in thirds within the scale."""
        return [self.midi(degree + 2 * k, octave) for k in range(size)]

    def chord_degrees(self, degree: int) -> set[int]:
        """Scale degrees (mod 7) that are chord tones of the chord on `degree`."""
        return {(degree + 2 * k) % 7 for k in range(3)}

    def degree_of(self, midi: int) -> int:
        """Nearest scale degree (as absolute degree, octave 4 = 0..6) for a MIDI note."""
        best, bd = 0, 99
        for d in range(-21, 35):
            dist = abs(self.midi(d) - midi)
            if dist < bd:
                best, bd = d, dist
        return best


# ---------------------------------------------------------------- parts ----

def _cells(meter: int) -> list[list[float]]:
    if meter == 3:
        return [[1, 1, 1], [2, 1], [1, 0.5, 0.5, 1], [1.5, 0.5, 1], [0.5, 0.5, 1, 1], [3]]
    return [[1, 1, 1, 1], [2, 1, 1], [1, 1, 2], [1.5, 0.5, 1, 1], [0.5, 0.5, 1, 2], [1, 0.5, 0.5, 2],
            [0.5, 0.5, 0.5, 0.5, 2], [3, 1], [2, 2]]


def _motif(r: np.random.Generator, meter: int, density: float) -> dict:
    cells = _cells(meter)
    w = np.array([len(c) for c in cells], float) ** (2 * density)
    rhythm = cells[int(r.choice(len(cells), p=w / w.sum()))]
    steps = [int(r.choice([-2, -1, -1, 1, 1, 2, 0, 3, -3])) for _ in range(len(rhythm) - 1)]
    return {"rhythm": rhythm, "steps": steps}


def _vary(m: dict, r: np.random.Generator, how: str) -> dict:
    if how == "invert":
        return {"rhythm": m["rhythm"], "steps": [-s for s in m["steps"]]}
    if how == "retro":
        return {"rhythm": m["rhythm"][::-1], "steps": [-s for s in m["steps"][::-1]]}
    if how == "ornament" and len(m["rhythm"]) < 6:
        i = int(np.argmax(m["rhythm"]))
        if m["rhythm"][i] >= 1:
            rh = m["rhythm"][:i] + [m["rhythm"][i] / 2] * 2 + m["rhythm"][i + 1:]
            st = m["steps"][:i] + [int(r.choice([-1, 1]))] + m["steps"][i:]
            return {"rhythm": rh, "steps": st}
    return m


def _nearest_chord_tone(key: Key, deg: int, chord: int, direction: int = 0) -> int:
    tones = key.chord_degrees(chord)
    for off in (0, -1, 1, -2, 2, -3, 3) if direction == 0 else ((0, 1, 2, 3) if direction > 0 else (0, -1, -2, -3)):
        if (deg + off) % 7 in tones:
            return deg + off
    return deg


def melody(key: Key, chords: list[int], meter: int, r: np.random.Generator, inst: str,
           register: tuple[int, int], density: float = 0.5) -> list[Note]:
    """Motif-based melody over one chord per bar: phrases of 4 bars as A, A', B, A'' + cadence."""
    lo, hi = register
    mA, mB = _motif(r, meter, density), _motif(r, meter, density)
    mid = key.degree_of((lo + hi) // 2)
    cur = mid
    notes: list[Note] = []
    bars = len(chords)
    for bar, chord in enumerate(chords):
        phrase, pos = divmod(bar, 4)
        last_phrase = phrase == (bars - 1) // 4
        if pos == 3:  # cadence bar: settle on a chord tone, long note
            rh = [meter] if r.random() < 0.4 else ([2, 2] if meter == 4 else [2, 1])
            steps = [int(r.choice([-1, 1]))] * (len(rh) - 1)
            m = {"rhythm": rh, "steps": steps}
        else:
            base = mB if phrase % 4 == 2 else mA
            m = _vary(base, r, ["same", "ornament", "invert", "retro"][(pos + phrase) % 4] if pos else "same")
        anchor = _nearest_chord_tone(key, cur + (1 if pos == 1 else 0), chord)
        if key.midi(anchor) > hi - 2:
            anchor -= 7
        if key.midi(anchor) < lo + 2:
            anchor += 7
        deg, beat = anchor, 0.0
        for i, d in enumerate(m["rhythm"]):
            if i:
                deg += m["steps"][i - 1]
            if beat in (0.0, 2.0) or d >= 2:
                deg = _nearest_chord_tone(key, deg, chord)
            while key.midi(deg) > hi:
                deg -= 7
            while key.midi(deg) < lo:
                deg += 7
            rest = i > 0 and r.random() < 0.08 * (1 - density)
            if not rest:
                vel = 0.62 + (0.12 if beat == 0 else 0.0) + r.uniform(-0.05, 0.05)
                notes.append(Note(bar * meter + beat, d * 0.92, key.midi(deg), vel, inst, pan=0.1))
            beat += d
        cur = deg
        if last_phrase and pos == 3:
            cur = mid
    return notes


def voicing(key: Key, chords: list[int], lo: int = 52, hi: int = 69) -> list[list[int]]:
    """Close voice-led triad voicings for a progression, kept between `lo` and `hi`."""
    out, prev = [], None
    for c in chords:
        pcs = [m % 12 for m in key.chord(c)]
        cands = []
        for base in range(lo, hi - 6):
            v = sorted({next(m for m in range(base, base + 12) if m % 12 == pc) for pc in pcs})
            if v[-1] <= hi:
                cands.append(v)
        if prev is None:
            pick = min(cands, key=lambda v: abs(sum(v) / 3 - (lo + hi) / 2))
        else:
            pick = min(cands, key=lambda v: sum(abs(a - b) for a, b in zip(v, prev)))
        out.append(pick)
        prev = pick
    return out


def _swing(beat: float, amount: float) -> float:
    return beat + amount / 3 if amount and abs((beat % 1) - 0.5) < 1e-6 else beat


def _groove(style: str, meter: int, bar: int, bars: int, section_start: bool, r) -> list[Note]:
    """Full drum kit for one bar (combat stem)."""
    b0 = bar * meter
    n: list[Note] = []
    last_of_section = (bar + 1) % 8 == 0 or bar == bars - 1
    if section_start:
        n.append(Note(b0, 1, 0, 0.6, "crash", 0.2, True))
    if style == "waltz":
        n.append(Note(b0, 1, 0, 0.8, "kick", 0, True))
        n += [Note(b0 + b, 1, 0, 0.45, "snare", 0, True) for b in (1, 2)]
        n += [Note(b0 + b, 0.5, 0, 0.35, "hat", 0.3, True) for b in (0.5, 1.5, 2.5)]
    elif style == "taiko":
        for b, v in ((0, 1.0), (1.5, 0.7), (2, 0.9), (3, 0.6), (3.5, 0.7)):
            n.append(Note(b0 + b, 1, 0, v, "taiko", -0.1 if b % 1 else 0.1, True))
        n += [Note(b0 + b, 1, 0, 0.5, "clap", 0, True) for b in (1, 3)]
        n += [Note(b0 + b / 4, 0.25, 0, 0.18 + 0.12 * (b % 4 == 0), "hat", 0.3, True) for b in range(16)]
    else:
        kicks = (0, 2, 2.5) if style == "drive" else (0, 2)
        n += [Note(b0 + b, 1, 0, 0.9, "kick", 0, True) for b in kicks]
        n += [Note(b0 + b, 1, 0, 0.75, "snare", 0, True) for b in (1, 3)]
        n += [Note(b0 + b / 2, 0.5, 0, 0.32 + 0.1 * (b % 2 == 0), "hat", 0.3, True) for b in range(8)]
        if style == "drive":
            n.append(Note(b0 + 3.5, 0.5, 0, 0.35, "open_hat", 0.3, True))
    if last_of_section and meter == 4:  # tom fill on beats 3-4
        n = [x for x in n if x.beat < b0 + 2 or x.inst in ("hat",)]
        for k in range(8):
            n.append(Note(b0 + 2 + k / 4, 0.25, 0, 0.5 + 0.05 * k, "tom_hi" if k < 4 else "tom_lo", -0.3 + 0.08 * k, True))
    return n


def _light(inst: str, style: str, meter: int, bar: int) -> list[Note]:
    """Light percussion for the calm stem."""
    b0 = bar * meter
    if style == "sparse":
        return [Note(b0 + b, 1, 0, 0.35, inst, 0.4, True) for b in (1, 3)]
    if style == "waltz":
        return [Note(b0 + b, 1, 0, 0.3 + 0.1 * (b == 0), inst, 0.2, True) for b in range(meter)]
    if style == "heartbeat":
        return [Note(b0, 1, 0, 0.55, inst, 0, True), Note(b0 + 0.5, 1, 0, 0.35, inst, 0, True)] if bar % 2 == 0 else []
    return [Note(b0 + b / 2, 0.5, 0, 0.22 + 0.12 * (b % 2 == 1), inst, 0.35, True) for b in range(meter * 2)]


def compose_song(mood: str = "title", bpm: float | None = None, bars: int | None = None, seed: int = 0,
                 root: str | None = None, scale: str | None = None, meter: int | None = None, **overrides) -> Song:
    """Compose a loop for `mood` (see `MOODS`); any preset field can be overridden by keyword."""
    p = {**MOODS[mood], **overrides}
    bpm = float(bpm or p["bpm"])
    meter = int(meter or p["meter"])
    bars = int(bars or p["bars"])
    root = root or p["root"]
    scale = scale or p["scale"]
    r = A.rng(seed, mood, "compose")
    key = Key(root, scale)
    half = bars // 2 if bars >= 8 else bars
    prog = [p["prog_a"][i % len(p["prog_a"])] for i in range(half)] + \
           [p["prog_b"][i % len(p["prog_b"])] for i in range(bars - half)]
    sections = [{"name": "A", "bar": 0, "bars": half}] + ([{"name": "B", "bar": half, "bars": bars - half}] if bars > half else [])
    calm, tension, combat = [], [], []

    # calm: pad, bass roots, melody, light percussion
    for bar, v in enumerate(voicing(key, prog)):
        calm += [Note(bar * meter, meter * 0.98, m, 0.5, p["pad"], pan=(-0.3, 0.0, 0.3)[i]) for i, m in enumerate(v)]
    for bar, c in enumerate(prog):
        rt = key.midi(c, 2)
        if rt < 36:
            rt += 12
        calm.append(Note(bar * meter, meter * 0.5 if meter == 4 else meter * 0.66, rt, 0.75, p["bass"]))
        if meter == 4:
            fifth = rt + (key.midi(c + 4, 2) - key.midi(c, 2))  # the scale's fifth (diminished on vii/ii)
            calm.append(Note(bar * meter + 2, 1.8, fifth if r.random() < 0.6 else rt + 12, 0.6, p["bass"]))
        calm += _light(p["light"], p["light_style"], meter, bar)
    calm += melody(key, prog, meter, r, p["lead"], tuple(p["lead_range"]), p["density"])

    # tension: arpeggio, ostinato, pulse bass, ticking hats
    pattern = [0, 1, 2, 3, 2, 1, 2, 1] if meter == 4 else [0, 1, 2, 3, 2, 1]
    for bar, c in enumerate(prog):
        sec_b = bar >= half
        tones = key.chord(c, 4 + (1 if sec_b and p["arp"] != "spiccato" else 0)) + [key.midi(c + 7, 4 + sec_b)]
        for i, k in enumerate(pattern):
            beat = _swing(bar * meter + i * 0.5, p["swing"])
            tension.append(Note(beat, 0.45, tones[k], 0.42 + 0.1 * (i % 2 == 0), p["arp"], pan=-0.45))
        rt = key.midi(c, 2)
        rt = rt + 12 if rt < 36 else rt
        for i in range(meter * 2):
            beat = bar * meter + i * 0.5
            tension.append(Note(beat, 0.4, rt + (12 if i % 2 else 0), 0.38 + 0.08 * (i % 2 == 0), p["pulse"]))
        fifth = key.midi(c + 4, 3)
        acc = [1, 0, 0, 1, 0, 0, 1, 0] if meter == 4 else [1, 0, 1, 0, 1, 0]
        for i, a in enumerate(acc):
            tension.append(Note(bar * meter + i * 0.5, 0.3, fifth if i % 2 else key.midi(c, 3), 0.4 + 0.25 * a,
                                p["ostinato"], pan=0.45))
        tension += [Note(bar * meter + b + 0.5, 0.25, 0, 0.2, "hat", 0.3, True) for b in range(meter)]

    # combat: drums, brass stabs
    for bar, c in enumerate(prog):
        combat += _groove(p["groove"], meter, bar, bars, bar in (0, half), r)
        stab_beats = (0, 1.5, 3) if meter == 4 else (0, 2)
        for v in key.chord(c, 3):
            for b in stab_beats:
                combat.append(Note(bar * meter + b, 0.45, v, 0.55 + 0.1 * (b == 0), p["stabs"], pan=-0.2 + 0.2 * (v % 3)))
    return Song(mood, bpm, bars, meter, root, scale, seed, prog, sections,
                {"calm": calm, "tension": tension, "combat": combat}, p)


# ------------------------------------------------------------- render ----

def _render_notes(notes: list[Note], n: int, bpm: float, seed: int, wrap: bool) -> np.ndarray:
    spb = 60.0 / bpm * A.SR
    buf = np.zeros((n, 2))
    r = A.rng(seed, "humanize")
    for k, nt in enumerate(notes):
        dur_s = nt.dur * 60.0 / bpm
        x = I.hit(nt.inst, nt.vel, k % 4) if nt.drum else I.note(nt.inst, nt.midi, dur_s, nt.vel)
        at = int(round(nt.beat * spb)) + int(r.integers(-60, 61))
        if not wrap:
            at = max(0, at)
        A.place(buf, A.pan(x, nt.pan), at, wrap=wrap)
    return buf


def render_song(song: Song) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Render a song's stems (exact loop length, wrap-around tails and reverb) and their mix, mastered together."""
    L = song.loop_samples
    decay, wet = song.params.get("reverb", (2.0, 0.3))
    stems = {}
    for i, name in enumerate(STEMS):
        dry = _render_notes(song.stems.get(name, []), L, song.bpm, song.seed + i, wrap=True)
        w = wet * (0.6 if name == "combat" else 1.0)
        stems[name] = A.reverb(dry, decay, w, predelay=0.02, tone=7000, seed=song.seed + 7 * i, circular=True)
        stems[name] -= stems[name].mean(axis=0)
    ref = A.loudness(stems["calm"])
    for s, off in STEM_BALANCE.items():  # layers sit at a fixed level relative to calm, whatever the mood
        cur = A.loudness(stems[s])
        if cur > -119 and ref > -119:
            stems[s] = stems[s] * A.db_to_amp(ref + off - cur)
    gain = _common_gain([stems[s] for s in STEMS], MUSIC_LUFS)
    stems = {k: v * gain for k, v in stems.items()}
    mix = sum(stems[s] for s in STEMS)
    return mix, stems


def _common_gain(layers: list[np.ndarray], lufs: float) -> float:
    """One gain for all layers: full mix at `lufs`, and no cumulative combination above the ceiling."""
    combos, acc = [], np.zeros_like(layers[0])
    for x in layers:
        acc = acc + x
        combos.append(acc.copy())
        combos.append(x)
    full = combos[-2]
    pk = max(float(np.abs(c).max()) for c in combos)
    g_l = A.db_to_amp(lufs - A.loudness(full))
    g_p = A.db_to_amp(CEILING_DB - 0.05) / max(pk, 1e-9)
    return min(g_l, g_p)


# --------------------------------------------------------------- MIDI ----

def _vlq(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    return bytes(reversed(out))


def write_midi(path: str | Path, song: Song, ppq: int = 480) -> Path:
    """Write the song as a type-1 Standard MIDI File, one track per stem+instrument (drums on channel 10)."""
    tracks = [b"\x00\xFF\x51\x03" + int(60_000_000 / song.bpm).to_bytes(3, "big") +
              b"\x00\xFF\x58\x04" + bytes([song.meter, 2, 24, 8]) + b"\x00\xFF\x2F\x00"]
    groups: dict[tuple, list[Note]] = {}
    for stem, notes in song.stems.items():
        for n in notes:
            groups.setdefault((stem, "drums" if n.drum else n.inst), []).append(n)
    ch = 0
    for (stem, inst), notes in groups.items():
        drum = inst == "drums"
        c = 9 if drum else ch
        if not drum:
            ch = ch + 1 if ch + 1 != 9 else 10
            ch %= 16
        ev = []
        for n in notes:
            pitch = I.GM_DRUM.get(n.inst, 38) if drum else int(round(n.midi))
            v = max(1, min(127, int(n.vel * 127)))
            t0 = int(round(n.beat * ppq))
            ev.append((t0, 1, bytes([0x90 | c, pitch, v])))
            ev.append((t0 + max(1, int(round(n.dur * ppq))), 0, bytes([0x80 | c, pitch, 0])))
        ev.sort(key=lambda e: (e[0], e[1]))
        name = f"{stem} {inst}".encode()
        data = b"\x00\xFF\x03" + _vlq(len(name)) + name
        if not drum:
            data += b"\x00" + bytes([0xC0 | c, I.GM_PROGRAM.get(inst, 0)])
        last = 0
        for t, _, msg in ev:
            data += _vlq(t - last) + msg
            last = t
        data += b"\x00\xFF\x2F\x00"
        tracks.append(data)
    out = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), ppq)
    for t in tracks:
        out += b"MTrk" + struct.pack(">I", len(t)) + t
    path = Path(path)
    path.write_bytes(out)
    return path


# ------------------------------------------------------------ packages ----

def build_track(name: str, out_dir: str | Path, mood: str = "title", bpm: float | None = None,
                bars: int | None = None, seed: int = 0, midi: bool = True, previews: bool = True,
                **overrides) -> dict:
    """Compose, render and package a looping track with stems; returns `catalog.validate_package`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    song = compose_song(mood, bpm, bars, seed, **overrides)
    mix, stems = render_song(song)
    files, stem_info = [], {}
    wav, ogg = A.write_pair(out / name, mix)
    files += [wav.name, ogg.name]
    m = A.measure(mix, loop=True)
    for s in STEMS:
        sw, so = A.write_pair(out / f"{name}_{s}", stems[s])
        files += [sw.name, so.name]
        ms = A.measure(stems[s], loop=True)
        stem_info[s] = {"wav": sw.name, "ogg": so.name, "lufs": ms["lufs"], "peak_db": ms["peak_db"],
                        "seam_ratio": ms["seam"]["ratio"]}
    if midi:
        files.append(write_midi(out / f"{name}.mid", song).name)
    if previews:
        A.spectrogram_png(mix, out / "previews" / f"{name}.png")
    catalog.write_manifest(
        out, name=name, kind="music", files=files, mood=mood, loop=True, bpm=song.bpm, bars=song.bars,
        beats_per_bar=song.meter, key=f"{song.root} {song.scale}", progression=[ROMAN[d] for d in song.progression],
        sections=song.sections, duration=round(song.loop_samples / A.SR, 4), sample_rate=A.SR, channels=2,
        loop_start=0, loop_end=song.loop_samples, seed=seed,
        stems=stem_info, stem_order=list(STEMS),
        layering="additive: play calm; add tension; add combat. The main file is all three summed.",
        lufs=m["lufs"], peak_db=m["peak_db"], target_lufs=MUSIC_LUFS, centroid_hz=m["centroid_hz"],
        seam=m["seam"])
    return catalog.validate_package(out)


STINGERS = ("wave-incoming", "building-complete", "victory", "defeat")


def stinger(kind: str, seed: int = 0, root: str = "D") -> np.ndarray:
    """A short non-looping musical cue: "wave-incoming", "building-complete", "victory" or "defeat"."""
    r = A.rng(seed, kind)
    notes: list[Note] = []
    if kind == "wave-incoming":
        key, bpm = Key(root, "harmonic_minor"), 120
        for b, v in ((0, 0.8), (1, 0.85), (1.5, 0.9), (2, 1.0)):
            notes.append(Note(b, 1, 0, v, "taiko", 0, True))
        for m in key.chord(0, 3):
            notes.append(Note(0, 2.6, m, 0.75, "brass"))
        for i in range(12):
            notes.append(Note(i * 0.25, 0.22, key.midi(4 + (i // 4), 4), 0.4 + 0.04 * i, "spiccato", 0.3))
        notes += [Note(3, 2, m, 0.95, "brass") for m in key.chord(4, 3)]
        notes += [Note(3, 1, 0, 0.8, "crash", 0, True), Note(3, 1, 0, 1.0, "taiko", 0, True)]
    elif kind == "building-complete":
        key, bpm = Key(root, "major"), 132
        for i, d in enumerate((0, 2, 4, 7, 9, 11, 14)):
            notes.append(Note(i * 0.25, 1.5, key.midi(d, 5), 0.7, "bell", -0.4 + 0.12 * i))
        notes += [Note(0, 2.5, m, 0.5, "pluck", 0.2) for m in key.chord(0, 4)]
        notes += [Note(2, 2, m, 0.6, "brass") for m in key.chord(0, 4)]
        notes += [Note(2, 1, 0, 0.5, "sleigh", 0.3, True), Note(2, 1, 0, 0.5, "crash", 0, True)]
    elif kind == "victory":
        key, bpm = Key(root, "major"), 120
        mel = [(0, 0.33, 4), (0.33, 0.33, 4), (0.67, 0.33, 4), (1, 1.5, 7), (2.5, 0.5, 6), (3, 0.5, 7), (3.5, 0.5, 8),
               (4, 3, 9)]
        notes += [Note(b, d, key.midi(deg, 4), 0.85, "brass", 0.1) for b, d, deg in mel]
        for b, c, d in ((0, 0, 1), (1, 3, 1.5), (2.5, 4, 1.5), (4, 0, 3.5)):
            notes += [Note(b, d, m, 0.6, "strings", -0.2) for m in key.chord(c, 3)]
            notes.append(Note(b, d, key.midi(c, 2), 0.8, "bass"))
        for i, d in enumerate((0, 2, 4, 7, 9, 11, 14, 16)):
            notes.append(Note(4 + i * 0.125, 2, key.midi(d, 5), 0.5, "bell", -0.5 + 0.14 * i))
        notes += [Note(b, 1, 0, 0.5, "sleigh", 0.3, True) for b in np.arange(0, 6, 0.5)]
        notes += [Note(b, 1, 0, 0.8, "tom_lo", 0, True) for b in (0, 1, 2.5)]
        notes += [Note(4, 1, 0, 0.9, "crash", 0, True), Note(4, 1, 0, 0.9, "kick", 0, True)]
    elif kind == "defeat":
        key, bpm = Key(root, "minor"), 70
        mel = [(0, 1, 4), (1, 1, 3), (2, 1, 2), (3, 3, 0)]
        notes += [Note(b, d, key.midi(deg, 5), 0.6, "flute", 0.1) for b, d, deg in mel]
        for b, c, d in ((0, 0, 2), (2, 3, 1), (3, 0, 3.5)):
            notes += [Note(b, d, m, 0.55, "strings", -0.2) for m in key.chord(c, 3)]
            notes.append(Note(b, d, key.midi(c, 2), 0.6, "bass"))
        notes += [Note(b, 3, key.midi(0, 3), 0.5, "bell", 0) for b in (0, 3)]
        notes.append(Note(3, 1, 0, 0.6, "taiko", 0, True))
    else:
        raise ValueError(f"unknown stinger {kind!r}; one of {STINGERS}")
    end = max(n.beat + n.dur for n in notes) * 60 / bpm
    x = _render_notes(notes, A.n_samples(end + 2.5), bpm, seed, wrap=False)
    x = A.reverb(x, 2.0, 0.3, predelay=0.02, seed=seed)
    x = A.trim_silence(x, -60, 0.05)
    return A.master(x, STINGER_LUFS, CEILING_DB, max_limit_db=3, fade_out=0.25)


def build_stinger(name: str, out_dir: str | Path, kind: str, seed: int = 0, root: str = "D") -> dict:
    """Package one stinger (`<name>.wav/.ogg`, kind "music", loop false); returns `catalog.validate_package`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x = stinger(kind, seed, root)
    wav, ogg = A.write_pair(out / name, x)
    m = A.measure(x)
    A.spectrogram_png(x, out / "previews" / f"{name}.png")
    catalog.write_manifest(out, name=name, kind="music", files=[wav.name, ogg.name], mood="stinger", stinger=kind,
                           loop=False, bpm=None, duration=m["duration"], sample_rate=A.SR, channels=2, seed=seed,
                           key=root, lufs=m["lufs"], peak_db=m["peak_db"], target_lufs=STINGER_LUFS,
                           centroid_hz=m["centroid_hz"])
    return catalog.validate_package(out)


#: The starter set `build_all` makes: package name -> build_track keyword arguments.
STARTER_TRACKS = {
    "title-theme": dict(mood="title"),
    "summer-building": dict(mood="summer"),
    "autumn-building": dict(mood="autumn"),
    "winter-christmas": dict(mood="winter"),
    "combat-layer": dict(mood="combat"),
    "boss-theme": dict(mood="boss"),
}


def build_all(out_root: str | Path, seed: int = 0) -> dict:
    """Build the starter tracks and all stingers into `<out_root>/<name>/`; returns `{name: report}`."""
    root = Path(out_root)
    reports = {}
    for name, kw in STARTER_TRACKS.items():
        t0 = time.time()
        reports[name] = build_track(name, root / name, seed=seed, **kw)
        reports[name]["seconds"] = round(time.time() - t0, 2)
    for kind in STINGERS:
        name = f"stinger-{kind}"
        t0 = time.time()
        reports[name] = build_stinger(name, root / name, kind, seed)
        reports[name]["seconds"] = round(time.time() - t0, 2)
    (root / "music-index.json").write_text(json.dumps({n: r["ok"] for n, r in reports.items()}, indent=2) + "\n")
    return reports


def song_dict(song: Song) -> dict:
    """A JSON-able view of a song (parameters and every note)."""
    d = asdict(song)
    d["loop_samples"] = song.loop_samples
    return d
