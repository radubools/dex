## `compose`

A small seeded procedural composer for game music: seamless loops in layered

- `SCALES` — constant
- `PITCH` — constant
- `ROMAN` — constant
- `STEMS` — constant
- `MUSIC_LUFS` — constant
- `STINGER_LUFS` — constant
- `CEILING_DB` — constant
- `STEM_BALANCE` — constant
- `class Note` — One event: start and length in beats, MIDI pitch (ignored for drums), velocity, instrument, pan.
- `class Song` — A composed loop: its parameters and the notes of each stem.
- `class Key` — A key: tonic pitch class and a 7-note scale, with degree <-> MIDI helpers.
- `melody(key: Key, chords: list[int], meter: int, r: np.random.Generator, inst: str, register: tuple[int, int], density: float=0.5) -> list[Note]` — Motif-based melody over one chord per bar: phrases of 4 bars as A, A', B, A'' + cadence.
- `voicing(key: Key, chords: list[int], lo: int=52, hi: int=69) -> list[list[int]]` — Close voice-led triad voicings for a progression, kept between `lo` and `hi`.
- `compose_song(mood: str='title', bpm: float | None=None, bars: int | None=None, seed: int=0, root: str | None=None, scale: str | None=None, meter: int | None=None, **overrides) -> Song` — Compose a loop for `mood` (see `MOODS`); any preset field can be overridden by keyword.
- `render_song(song: Song) -> tuple[np.ndarray, dict[str, np.ndarray]]` — Render a song's stems (exact loop length, wrap-around tails and reverb) and their mix, mastered together.
- `write_midi(path: str | Path, song: Song, ppq: int=480) -> Path` — Write the song as a type-1 Standard MIDI File, one track per stem+instrument (drums on channel 10).
- `build_track(name: str, out_dir: str | Path, mood: str='title', bpm: float | None=None, bars: int | None=None, seed: int=0, midi: bool=True, previews: bool=True, **overrides) -> dict` — Compose, render and package a looping track with stems; returns `catalog.validate_package`.
- `STINGERS` — constant
- `stinger(kind: str, seed: int=0, root: str='D') -> np.ndarray` — A short non-looping musical cue: "wave-incoming", "building-complete", "victory" or "defeat".
- `build_stinger(name: str, out_dir: str | Path, kind: str, seed: int=0, root: str='D') -> dict` — Package one stinger (`<name>.wav/.ogg`, kind "music", loop false); returns `catalog.validate_package`.
- `STARTER_TRACKS` — constant
- `build_all(out_root: str | Path, seed: int=0) -> dict` — Build the starter tracks and all stingers into `<out_root>/<name>/`; returns `{name: report}`.
- `song_dict(song: Song) -> dict` — A JSON-able view of a song (parameters and every note).

## `instruments`

Instrument timbres for procedural game music, built on `audio` (from game-sfx).

- `NYQ` — constant
- `pluck(freq: float, dur: float, vel: float=0.8) -> np.ndarray` — Plucked string (harp / guitar-ish): bright attack, upper partials die first.
- `marimba(freq: float, dur: float, vel: float=0.8) -> np.ndarray` — Wooden bar: strong fundamental, 4x and 10x partials, soft mallet click.
- `bell(freq: float, dur: float, vel: float=0.8) -> np.ndarray` — Glockenspiel / celesta bell: inharmonic sparkle, long shimmering decay.
- `music_box(freq: float, dur: float, vel: float=0.8) -> np.ndarray` — Music-box tine: pure, bright and short.
- `pad(freq: float, dur: float, vel: float=0.6) -> np.ndarray` — Warm synth pad: detuned saws through a soft lowpass, slow swell.
- `strings(freq: float, dur: float, vel: float=0.7) -> np.ndarray` — String section (legato): bowed saws, delayed vibrato, gentle bite.
- `spiccato(freq: float, dur: float, vel: float=0.7) -> np.ndarray` — Short bowed strings for ostinatos: quick bite, no sustain.
- `bass(freq: float, dur: float, vel: float=0.8) -> np.ndarray` — Round plucked bass (upright-ish): sine body with a short harmonic thumb.
- `synth_bass(freq: float, dur: float, vel: float=0.8) -> np.ndarray` — Driving saw bass for combat: filter snaps open on each note, slight drive.
- `brass(freq: float, dur: float, vel: float=0.8) -> np.ndarray` — Brass section: saws whose brightness swells with the attack.
- `flute(freq: float, dur: float, vel: float=0.7) -> np.ndarray` — Breathy flute / whistle lead.
- `choir(freq: float, dur: float, vel: float=0.6) -> np.ndarray` — Soft 'ooh' choir pad.
- `PITCHED` — constant
- `kick(vel: float=0.9, seed: int=0) -> np.ndarray` — Kick drum: pitch-dropping sine thump with a click.
- `snare(vel: float=0.8, seed: int=0) -> np.ndarray` — Snare: tone plus bright rattle.
- `brush(vel: float=0.5, seed: int=0) -> np.ndarray` — Brushed snare swish for gentle grooves.
- `hat(vel: float=0.6, seed: int=0) -> np.ndarray` — Closed hi-hat.
- `open_hat(vel: float=0.6, seed: int=0) -> np.ndarray` — Open hi-hat.
- `crash(vel: float=0.7, seed: int=0) -> np.ndarray` — Crash cymbal wash.
- `tom(vel: float=0.8, seed: int=0, freq: float=110.0) -> np.ndarray` — Tom: pitched membrane thump.
- `taiko(vel: float=0.9, seed: int=0) -> np.ndarray` — Big low drum for the boss: long boom, skin slap.
- `shaker(vel: float=0.4, seed: int=0) -> np.ndarray` — Shaker grain.
- `clap(vel: float=0.7, seed: int=0) -> np.ndarray` — Hand clap: three quick bursts and a tail.
- `woodblock(vel: float=0.6, seed: int=0) -> np.ndarray` — Wood block tick.
- `sleigh(vel: float=0.6, seed: int=0) -> np.ndarray` — Sleigh-bell shake: a cluster of tiny jingles.
- `DRUMS` — constant
- `GM_PROGRAM` — constant
- `GM_DRUM` — constant
- `note(name: str, midi: float, dur: float, vel: float=0.8) -> np.ndarray` — A pitched note (mono, read-only, cached on rounded duration/velocity).
- `hit(name: str, vel: float=0.8, seed: int=0) -> np.ndarray` — A drum hit (mono, read-only, cached); `seed` 0-3 gives slightly different takes.
- `names() -> dict` — The instrument names: `{"pitched": [...], "drums": [...]}`.