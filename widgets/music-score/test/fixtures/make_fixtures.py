"""Build the MIDI and audio the music-score tests run against.

Standard MIDI Files are binary, so the fixtures are generated rather than
hand-written, and this script is kept beside them so the next person can see
exactly what they contain and regenerate them:

    .venv/bin/python3 widgets/music-score/test/fixtures/make_fixtures.py

`piece` is the ordinary case: two named tracks, a tempo change partway
through, accidentals, and a WAV that is a faithful synthesis of that exact
MIDI. `piano` is the awkward one: format 0, running status, 2/4, a key
signature of three flats, one track spanning both staves, and no audio at all.
"""

import json
import math
import struct
import wave
from pathlib import Path

DIV = 480
OUT = Path(__file__).resolve().parent
#: Low, but a real render: the tests decode it, they do not listen to it.
RATE = 11025


def vlq(n: int) -> bytes:
    """A MIDI variable-length quantity."""
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack(">I", len(body)) + body


def track(events: list[tuple[int, bytes]], name: str | None = None) -> bytes:
    """An MTrk chunk from (tick, event bytes) pairs."""
    body = bytearray()
    if name:
        body += vlq(0) + b"\xFF\x03" + vlq(len(name)) + name.encode()
    last = 0
    for tick, data in sorted(events, key=lambda e: e[0]):
        body += vlq(tick - last) + data
        last = tick
    body += vlq(0) + b"\xFF\x2F\x00"
    return chunk(b"MTrk", bytes(body))


def note_events(notes: list[tuple[int, int, int, int]], channel: int) -> list[tuple[int, bytes]]:
    events = []
    for start, dur, pitch, velocity in notes:
        events.append((start, bytes([0x90 | channel, pitch, velocity])))
        events.append((start + dur, bytes([0x80 | channel, pitch, 0])))
    return events


# --------------------------------------------------------------- piece.mid
Q = DIV
melody: list[tuple[int, int, int, int]] = []
tick = 0
for pitch, beats in [
    (72, 1), (74, 1), (76, 2), (78, 1), (76, 1), (72, 2), (69, 1), (71, 1),
    (74, 2), (70, 1), (72, 1), (76, 4), (79, 1), (77, 1), (76, 1), (74, 1), (72, 4),
]:
    melody.append((tick, int(beats * Q) - 10, pitch, 96))
    tick += int(beats * Q)
melody_end = tick

bass: list[tuple[int, int, int, int]] = []
tick = 0
for pitch in [36, 41, 43, 36, 38, 43, 36, 36]:
    bass.append((tick, 4 * Q - 20, pitch, 80))
    tick += 4 * Q
    if tick >= melody_end:
        break

TEMPOS = [(0, 96), (8 * Q, 120)]
conductor = [
    (0, b"\xFF\x58\x04\x04\x02\x18\x08"),
    (0, b"\xFF\x59\x02\x00\x00"),
    *[(at, b"\xFF\x51\x03" + (60_000_000 // bpm).to_bytes(3, "big")) for at, bpm in TEMPOS],
]
midi = (
    chunk(b"MThd", struct.pack(">HHH", 1, 3, DIV))
    + track(conductor, "Test Piece")
    + track(note_events(melody, 0), "Melody")
    + track(note_events(bass, 1), "Bass")
)
(OUT / "piece.mid").write_bytes(midi)


def seconds(at: int) -> float:
    """The same tempo map the file carries, so the render cannot drift."""
    total, last_tick, bpm = 0.0, 0, TEMPOS[0][1]
    for change, new in TEMPOS:
        if change >= at:
            break
        total += (change - last_tick) * (60 / bpm) / DIV
        last_tick, bpm = change, new
    return total + (at - last_tick) * (60 / bpm) / DIV


sounded = [(seconds(s), seconds(s + d), p, v) for s, d, p, v in melody + bass]
length = max(end for _, end, _, _ in sounded) + 0.6
samples = [0.0] * int(length * RATE)
for start, end, pitch, velocity in sounded:
    frequency = 440 * 2 ** ((pitch - 69) / 12)
    first, last = int(start * RATE), min(int(end * RATE), len(samples))
    span = max(1, last - first)
    for i in range(first, last):
        k = i - first
        envelope = (
            min(1.0, k / (0.01 * RATE))
            * min(1.0, (span - k) / (0.06 * RATE))
            * math.exp(-2.2 * k / RATE)
        )
        tone = math.sin(2 * math.pi * frequency * k / RATE) + 0.35 * math.sin(4 * math.pi * frequency * k / RATE)
        samples[i] += tone * envelope * (velocity / 127) * 0.16

with wave.open(str(OUT / "piece.wav"), "w") as out:
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(RATE)
    out.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(v * 32767)))) for v in samples))

(OUT / "piece.score.json").write_text(
    json.dumps({"title": "Test Piece", "midi": "piece.mid", "audio": "piece.wav"}, indent=2) + "\n"
)

# --------------------------------------------------------------- piano.mid
PDIV = 240
keys: list[tuple[int, int, int]] = []
tick = 0
for bar in range(4):
    for i, pitch in enumerate([76, 79, 83, 86]):
        keys.append((tick + i * PDIV // 2, PDIV // 2 - 5, pitch + bar))
    for i, pitch in enumerate([40, 47]):
        keys.append((tick + i * PDIV, PDIV - 5, pitch + bar))
    tick += 2 * PDIV

flat = []
for start, dur, pitch in keys:
    flat.append((start, pitch, 90))
    flat.append((start + dur, pitch, 0))
flat.sort(key=lambda e: e[0])

body = bytearray(vlq(0) + b"\xFF\x03\x05Piano")
body += vlq(0) + b"\xFF\x58\x04\x02\x02\x18\x08"
body += vlq(0) + b"\xFF\x59\x02\xFD\x00"
body += vlq(0) + b"\xFF\x51\x03" + (60_000_000 // 132).to_bytes(3, "big")
last, status = 0, None
for at, pitch, velocity in flat:
    body += vlq(at - last)
    if status != 0x90:
        body += bytes([0x90])
        status = 0x90
    body += bytes([pitch, velocity])
    last = at
body += vlq(0) + b"\xFF\x2F\x00"
(OUT / "piano.mid").write_bytes(
    chunk(b"MThd", struct.pack(">HHH", 0, 1, PDIV)) + chunk(b"MTrk", bytes(body))
)
(OUT / "piano.score.json").write_text(json.dumps({"title": "Running Status Study"}, indent=2) + "\n")

print(f"piece.mid {len(midi)}B, piece.wav {(OUT / 'piece.wav').stat().st_size}B, piano.mid {(OUT / 'piano.mid').stat().st_size}B")
