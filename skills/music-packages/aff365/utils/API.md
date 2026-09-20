## `harmony`

List the pitch pairs that sound at the same time in a parsed MIDI.

- `simultaneous_intervals(midi, min_overlap: float=0.05)` — Every pair of pitches overlapping in time, as (seconds, low, high, overlap).

## `player`

Build a package's self-contained player.html from its score sidecar.

- `DEFAULT_TEMPLATE` — constant
- `build_player(sidecar_path, out_path=None, subtitle: str='', template=DEFAULT_TEMPLATE) -> Path` — Write a standalone player.html beside a .score.json, with its MIDI inlined.

## `scorepack`

Inspect a music package: validate its score sidecar and measure its audio.

- `SIDECAR_FIELDS` — constant
- `problems_with_sidecar(path, fields: tuple[str, ...]=SIDECAR_FIELDS) -> list[str]` — List what is wrong with a .score.json: missing fields, or files it cannot reach.
- `read_wav_mono(path) -> tuple[np.ndarray, int]` — Read a 16-bit WAV as a mono float array in [-1, 1], with its sample rate.
- `dominant_hz(samples: np.ndarray, sample_rate: int) -> float` — The strongest spectral peak of a window of samples, in Hz.