---
name: game-music
description: Procedural game music — seeded composer, synthesized instruments, sample-exact seamless loops with calm/tension/combat stems, and stingers, packaged as kind "music". Requires game-sfx.
---

# Game music — composed and played by code

Every track is composed from a seed (key, scale, progression, motif-based
melody, bass, drums, sections) and rendered by synthesised instruments. No
samples. **Requires the `game-sfx` skill enabled too** — it provides
`utils.audio`, the DSP this is built on — and game-mesh's `catalog`.

```python
from utils import compose, instruments, audio as A, catalog
```

Read `utils/API.md` for every signature.

## The conventions (the game relies on them)

| | |
|---|---|
| Format | 44.1 kHz stereo, 16-bit WAV + Ogg Vorbis, plus a `.mid` of the notes. |
| Loop | Exactly `bars x beats_per_bar` beats: `loop_end = round(bars * meter * 60 / bpm * 44100)` samples. Loop the whole file, `loopStart = 0`, `loopEnd = loop_end / 44100`. |
| Seam | Note tails and reverb past the end are wrapped onto the start, so the last sample flows into the first. The very first play therefore starts with a little tail — fade in over ~0.5 s if that matters. |
| Stems | `<name>_calm`, `<name>_tension`, `<name>_combat`: same tempo, length and key, **additive**. Play calm; add tension; add combat. `<name>.wav` is all three summed. Start them on the same sample and crossfade gains only. |
| Loudness | Full mix -16 LUFS (`compose.MUSIC_LUFS`); tension sits 3 LU and combat 1.5 LU under calm. One gain is shared by all stems, chosen so *every* cumulative combination peaks under -1 dBFS. Stingers -16 LUFS, peak <= -1. |
| Stingers | One-shot cues (`loop: false`): wave-incoming, building-complete, victory, defeat. Fade out, no seam. |

## Making a track

```python
rep = compose.build_track("night-shift", "out/night-shift", mood="winter", bpm=96, bars=16, seed=2)
```

- `mood` picks a preset in `compose.MOODS` (title, summer, autumn, winter,
  combat, boss). Override any field by keyword: `root="Bb"`,
  `scale="mixolydian"`, `meter=3`, `prog_a=[0, 5, 3, 4]` (scale degrees,
  one chord per bar), `lead="flute"`, `light="sleigh"`, `groove="drive"`,
  `reverb=(2.0, 0.3)`, `density=0.7`, `swing=0.1`.
- New mood: add a dict to `MOODS`. Instruments: `instruments.names()`
  (pluck, marimba, bell, music_box, pad, strings, spiccato, bass,
  synth_bass, brass, flute, choir; drums kick, snare, brush, hat, open_hat,
  crash, tom_hi/lo, taiko, shaker, clap, woodblock, sleigh).
- Bars: 8 or 16 (multiples of 4 — melodies are 4-bar phrases; the second
  half is section B with its own progression). Different `seed` = new tune.
- To edit notes: `song = compose.compose_song(...)`, change `song.stems`,
  then `mix, stems = compose.render_song(song)`.
- Stinger: `compose.build_stinger("stinger-victory", out, "victory", root="D")`.
- `compose.build_all(root)` makes the starter set (title-theme,
  summer-building, autumn-building, winter-christmas, combat-layer,
  boss-theme, four stingers) in ~15 s.

`build_track` / `build_stinger` return `catalog.validate_package` — must be `ok`.

## Checking it — you cannot listen, so measure

Manifest and `A.measure(x, loop=True)` give you:

- **duration / loop_end** exactly bar-aligned (formula above), and the
  written WAV has exactly `loop_end` samples.
- **seam.ok** on the mix *and* every stem (`ratio` <= 1.5: the step from the
  last sample to the first is no bigger than an ordinary step).
- **peak_db** <= -1 for the mix, every stem, calm+tension; **clipped** = 0.
- **lufs** ~ -16 for the mix (lower only when the peak ceiling bound);
  stem LUFS ordered calm > combat > tension as above.
- **centroid_hz** and band balance: a mix should fall off gently above
  1 kHz (Welch PSD bands within ~10 dB of each other from 60 Hz to 1 kHz).
  Huge energy under 60 Hz = muddy; a 6 kHz+ centroid = hats/bells too loud.
- **Pitch**: every non-drum note is in the key (`compose.Key`); instruments
  are tuned (A4 = 440 Hz).
- **Look at it.** `previews/<name>.png` (spectrogram + waveform): bar-regular
  structure, bass line visible under 200 Hz, melody above 500 Hz, no red
  (full-scale) in the waveform strip.

## Shipping it to the game

`catalog.export(package_dir)` → `~/games/many-builders/public/assets/music/<name>/`
and `index.json`. The manifest carries `bpm`, `bars`, `beats_per_bar`,
`key`, `progression`, `sections`, `loop`, `loop_start`/`loop_end` (samples),
`duration`, `mood`, `stems` (files + LUFS + peak per layer), `stem_order`.

Sound effects are the `game-sfx` skill.
