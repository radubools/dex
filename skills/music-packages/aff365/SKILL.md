---
name: music-packages
description: The Music project's package format — score sidecar, harmony checks, and the self-contained player.html a package ships.
---

# Packages

What a Music package is, as something a script can check.

| Module | For |
|---|---|
| `utils/scorepack.py` | Validate a score sidecar and measure its audio |
| `utils/harmony.py` | The pitch pairs sounding at once, for clash checking |
| `utils/player.py` | Build a package's self-contained `player.html` |

`utils/player_template.html` is the template `player.py` fills in. It is data,
not a module — do not import it.

The `music-score` widget reads `.score.json`. Note it is bound to the
**sidecar**, not to the `.mid`: dex only hands a widget files it can read as
text, so the viewer loads the MIDI and the audio from the sidecar's paths.

## Why harmony is its own thing

A note-by-note check cannot see a clash. Every note can be a tone of its own
chord and the piece still sound wrong, so the check has to be over the pairs
that sound *at the same time* — which is what `harmony.py` produces.

## What it will not do

Rendering audio is `music-synth`. This should not import it.
