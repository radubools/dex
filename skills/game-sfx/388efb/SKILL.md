---
name: game-sfx
description: Procedural sound effects for the games in ~/games — a DSP toolkit, one seeded generator per game event with variations, seamless ambience loops, packaged as kind "sfx".
---

# Game SFX — every sound is made from code

No samples, no downloads: every sound effect is synthesised from oscillators,
noise, filters and envelopes, seeded so it rebuilds identically. Built on
game-mesh's `catalog` (package, validate, export).

```python
from utils import audio as A, sfx, catalog
```

Read `utils/API.md` for every signature. `audio` is the shared DSP toolkit
(game-music builds on it too); `sfx` holds the generators.

## The conventions (the game relies on them)

| | |
|---|---|
| Format | 44.1 kHz (`A.SR` = `catalog.SAMPLE_RATE`), stereo, 16-bit WAV + Ogg Vorbis (q5) of the same take. |
| Names | Package name kebab-case (`shotgun-fire`); files `<name>_<n>.wav/.ogg`, n = 1..variations. |
| Variations | 3 by default. The game picks one at random per play, so repeats never sound identical. |
| Loudness | Per category, integrated LUFS (`A.loudness`, BS.1770 K-weighted, gated) and a peak ceiling — table below. |
| One-shots | Start and end at silence: 2 ms fade in, 20 ms fade out, tail trimmed at -60 dB. |
| Loops | Exact length (`loop_end` samples, manifest `takes`), no fades, last sample flows into the first. |

| category | LUFS | ceiling | used for |
|---|---|---|---|
| ui | -22 | -3 dBFS | clicks, toggles, pause, save/load, day tick |
| building | -18 | -1 | hammer, saw |
| combat | -16 | -1 | shotgun, turret, clone spawn/hit/death, bunker damage |
| event | -16 | -1 | recruit, building complete, wave warning, deadline, victory, defeat |
| boss | -14 | -1 | Angry Patrick footsteps, roar, slam |
| ambience | -26 | -6 | wind, forest, construction loops (beds under everything) |

`A.master` gains to the target but never pushes more than 6 dB into the
limiter, so spiky sounds (gunshots, slams) may land a little under target
instead of being squashed. That is intended.

## Making a new sound

1. Write a generator in `sfx.py` and register it:
   ```python
   @sound("snowball-throw", "combat", "A snowball thrown and splatting.")
   def _snowball(r, size=1.0):          # r: seeded numpy Generator — use it for every random choice
       ...
       return stereo_or_mono_array
   ```
   Draw *all* variation from `r` (pitch +-10 %, timing, which partials), so
   variation n differs from n+1 but rebuilds the same. Parameters with
   defaults are tweakable from `build_sfx(name, out, size=1.4)`.
2. Build it from `A`: `osc` (sine/triangle/saw/square, band-limited, freq may
   be an array), `sweep`, `vibrato`, `supersaw`, `noise` (white/pink/brown),
   `modal` (bells, wood, metal), envelopes (`perc`, `adsr`, `envelope`),
   filters (`lowpass`/`highpass` with array cutoff to sweep, `bandpass`,
   `resonate`, `formant`, `eq`), `drive`, `delay`, `reverb`, `pan`, `widen`,
   `place` (mix an event in at a time).
3. Loops: make material `length + 1 s` long and cut it with
   `A.make_loop(x, length, 1.0)`; drop events in with `A.place(..., wrap=True)`;
   use `A.reverb(..., circular=True)` and periodic control curves
   (`A.smooth_random(..., loop=True)`). Register with `loop=True`.
4. `sfx.build_sfx(name, out_dir, variations=3)` writes the files, a
   spectrogram in `previews/`, and the manifest; it returns
   `catalog.validate_package`, which must be `ok`. `sfx.build_all(root)`
   rebuilds every sound.

## Checking it — you cannot listen, so measure

`A.measure(x, loop=...)` gives everything; check each variation:

- **peak_db** at or under the category ceiling; **clipped** = 0; **dc** < 0.01.
- **lufs** within 1 LU of target (or up to 6 below for very spiky sounds).
- **duration** sensible for the event (UI < 0.3 s, impacts < 1.5 s, cues < 6 s,
  ambiences 20 s). One-shots: first and last samples ~0 (**start_db** /
  **end_db** low) — otherwise it clicks.
- **centroid_hz** matches the intent: rumbles and booms ~100-500 Hz, UI and
  bells 1-3 kHz, hiss and sleigh bells 4 kHz+. A "warm" sound at 8 kHz is wrong.
- Loops: **seam.ok** (jump across the loop point no bigger than a normal
  sample step; `ratio` <= 1.5) and RMS per second varying the way you meant
  (gusts, events) — not flat, not spiking.
- **Look at it.** `A.spectrogram_png(x, path)` — read the PNG: onset sharpness,
  decay length, pitch sweeps going the right way, no broadband smear where
  a tone was meant, waveform strip under the ceiling (red = full scale).

## Shipping it to the game

`catalog.export(package_dir)` copies a validated package into
`~/games/many-builders/public/assets/sfx/<name>/` and registers it in
`public/assets/index.json`. The manifest carries `category`, `loop`,
`variations`, `duration` (longest), `durations`, per-take `lufs`/`peak_db`,
and for loops `loop_start`/`loop_end` in samples — the game should loop with
those (Web Audio `loopStart/loopEnd`) rather than trusting the decoder.

## What's in the box

`sfx.describe()` lists them: hammer-hit, saw-stroke, building-complete,
recruit-builder, shotgun-fire, turret-fire, wave-warning, clone-spawn,
clone-hit, clone-death, bunker-damage, boss-footstep, boss-roar, boss-slam,
ui-click, ui-toggle-on/off, ui-deny, pause, unpause, save-game, load-game,
day-tick, christmas-deadline, victory, defeat, ambience-wind,
ambience-forest, ambience-construction. Tone: cozy winter comedy with
tension — rubbery, bright and a little silly, never gory.

Music (loops, stems, stingers) is the `game-music` skill, built on `audio`.
