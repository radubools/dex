"""List the pitch pairs that sound at the same time in a parsed MIDI.

A note-by-note check cannot see a clash: every note can be a tone of its own
chord while two voices still collide a semitone apart. This reports what
sounds together and leaves the judgement to the caller.
"""

from __future__ import annotations


def simultaneous_intervals(midi, min_overlap: float = 0.05):
    """Every pair of pitches overlapping in time, as (seconds, low, high, overlap).

    Reports across all instruments, sorted by onset. Whether an interval is a
    problem is the caller's to decide: a calm piece may reject semitones a
    fifth apart that a dense one is happy with.
    """
    notes = sorted((note.start, note.end, note.pitch)
                   for instrument in midi.instruments for note in instrument.notes)

    pairs = []
    for index, (start, end, pitch) in enumerate(notes):
        for other_start, other_end, other_pitch in notes[index + 1:]:
            # Sorted by onset, so once one starts too late every later one does.
            if other_start >= end - min_overlap:
                break
            overlap = min(end, other_end) - other_start
            if overlap < min_overlap:
                continue
            low, high = sorted((pitch, other_pitch))
            pairs.append((other_start, low, high, overlap))
    return sorted(pairs)
