---
name: music-synth
description: Render a parsed MIDI to stereo audio with a per-instrument additive synthesiser.
---

# Synth

`utils/synth.py` turns a parsed MIDI into stereo audio.

It exists because **pretty_midi's own `synthesize()` ignores MIDI programs** —
a four-voice canon comes back as one timbre, which is not a rendering of the
piece that was written. This gives each instrument its own voice.

## Using it

```python
from utils import synth
```

Read `utils/API.md` in the project directory for the signatures; it is
regenerated from the docstrings on every run.

Needs `numpy` and `scipy`.

## What it will not do

It does not know what a *package* is — validating a score sidecar, measuring
the audio, building the player are `music-packages`. Keep it that way: this
module is useful to anything making audio, and it stops being so the moment it
imports a manifest.
