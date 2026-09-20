## `synth`

Render a parsed MIDI to stereo audio with a per-instrument additive synthesiser.

- `SAMPLE_RATE` — constant
- `class Timbre` — A synthetic instrument: harmonic amplitudes, an ADSR shape, and a stereo pan.
- `DEFAULT_TIMBRE` — constant
- `midi_to_hz(pitch: int) -> float` — Equal-tempered frequency of a MIDI note number (A4 = 440 Hz).
- `envelope(n: int, timbre: Timbre, sample_rate: int) -> np.ndarray` — An ADSR amplitude envelope of n samples, with the release fitted inside n.
- `render_note(pitch: int, velocity: int, seconds: float, timbre: Timbre, sample_rate: int=SAMPLE_RATE) -> np.ndarray` — Synthesise one note as a mono float array.
- `room_impulse(sample_rate: int=SAMPLE_RATE, seconds: float=1.35) -> np.ndarray` — A short exponentially decaying noise burst, used as a room impulse response.
- `render(midi, sample_rate: int=SAMPLE_RATE, tail_seconds: float=1.8, wet: float=0.2, peak: float=0.89) -> np.ndarray` — Render a PrettyMIDI to a stereo float array, timed from its own tempo map.
- `write_wav(path, samples: np.ndarray, sample_rate: int=SAMPLE_RATE) -> None` — Write a float array in [-1, 1] as a 16-bit PCM WAV file.
- `wav_duration(path) -> float` — Duration of a WAV file in seconds, read from its own header.