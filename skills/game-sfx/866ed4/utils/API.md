## `audio`

Shared DSP for game audio: oscillators, noise, envelopes, filters, effects,

- `SR` — constant
- `FFMPEG_FALLBACK` — constant
- `n_samples(dur: float) -> int` — Number of samples in `dur` seconds.
- `times(dur: float) -> np.ndarray` — Time in seconds of each sample of a `dur`-second signal.
- `rng(*seed: int | str) -> np.random.Generator` — A seeded generator; strings are hashed so `rng(0, "shotgun", 2)` is stable.
- `db_to_amp(db: float) -> float` — Decibels to linear gain.
- `amp_to_db(a: float) -> float` — Linear amplitude to decibels (-120 for silence).
- `midi_hz(m: float) -> float` — MIDI note number to frequency in Hz (69 = A4 = 440).
- `mono(x: np.ndarray) -> np.ndarray` — Mix stereo down to mono (mono passes through).
- `stereo(x: np.ndarray) -> np.ndarray` — Mono to identical-channel stereo (stereo passes through).
- `pad_to(x: np.ndarray, n: int) -> np.ndarray` — Zero-pad (or cut) to `n` samples.
- `mix(*xs: np.ndarray) -> np.ndarray` — Sum signals of any lengths (mono ones are made stereo if any is stereo).
- `place(dst: np.ndarray, src: np.ndarray, at: float | int, gain: float=1.0, wrap: bool=False) -> np.ndarray` — Add `src` into `dst` starting at `at` (seconds if float, samples if int), in place.
- `sweep(f0: float, f1: float, dur: float, curve: str='exp') -> np.ndarray` — A per-sample frequency glide from `f0` to `f1` ("exp" or "lin").
- `vibrato(freq: float | np.ndarray, dur: float, rate: float=5.5, depth: float=0.03, delay: float=0.0) -> np.ndarray` — Frequency array with sinusoidal vibrato of `depth` semitones, fading in after `delay`.
- `osc(freq: float | np.ndarray, dur: float, shape: str='sine', phase: float=0.0, pw: float=0.5) -> np.ndarray` — Oscillator: "sine", "triangle", "saw" or "square" (band-limited PolyBLEP); `freq` may be an array.
- `supersaw(freq: float | np.ndarray, dur: float, voices: int=3, detune_cents: float=12, r: np.random.Generator | None=None) -> np.ndarray` — Several detuned saws with random phases, normalised to about +-1.
- `noise(dur: float, color: str='white', r: np.random.Generator | None=None) -> np.ndarray` — Noise at RMS 0.25: "white", "pink" (-3 dB/oct) or "brown" (-6 dB/oct), nothing below 20 Hz.
- `smooth_random(dur: float, rate: float, r: np.random.Generator | None=None, loop: bool=False) -> np.ndarray` — A slow random control curve in 0..1 changing about `rate` times a second (periodic if `loop`).
- `modal(freqs: Sequence[float], decays: Sequence[float], amps: Sequence[float], dur: float, r: np.random.Generator | None=None) -> np.ndarray` — Sum of exponentially decaying sines (bells, wood, metal); `decays` are 1/e times in seconds.
- `partial(freq: float | np.ndarray, decay: float, amp: float, dur: float, r: np.random.Generator, attack: float=0.001) -> np.ndarray` — One decaying sine (1/e time `decay`) that lands on zero, with zero slope, at its buffer end; `freq` may glide.
- `bar(freqs: Sequence[float], decays: Sequence[float], amps: Sequence[float], dur: float, r: np.random.Generator, attack: float=0.001) -> np.ndarray` — A struck bar, bell or block as a sum of `partial`s: `modal` without the click at the buffer end.
- `envelope(points: Sequence[tuple[float, float]], dur: float) -> np.ndarray` — Piecewise-linear envelope through `(time, level)` breakpoints.
- `adsr(dur: float, a: float=0.01, d: float=0.1, s: float=0.7, r: float=0.2) -> np.ndarray` — Attack-decay-sustain-release; `dur` is the gate (held) time, the result is `dur + r` long.
- `perc(dur: float, attack: float=0.002, decay: float=0.2) -> np.ndarray` — Percussive envelope: linear attack, exponential decay (1/e time `decay`), forced to 0 at the end.
- `fade(x: np.ndarray, fade_in: float=0.002, fade_out: float=0.01) -> np.ndarray` — Raised-cosine fades at both ends so a one-shot never starts or stops with a click.
- `smooth_end(env: np.ndarray, frac: float=0.3, longest: float=0.12) -> np.ndarray` — Multiply the last `frac` of an envelope (at most `longest` s) by a half-cosine to 0.
- `biquad(kind: str, freq: float, q: float=0.7071, gain_db: float=0.0) -> tuple[np.ndarray, np.ndarray]` — RBJ biquad coefficients `(b, a)`: lowpass, highpass, bandpass, peak, lowshelf, highshelf.
- `lowpass(x: np.ndarray, cutoff: float | np.ndarray, order: int=2, q: float | None=None) -> np.ndarray` — Lowpass (Butterworth; `q` for a resonant biquad; an array `cutoff` sweeps over time).
- `highpass(x: np.ndarray, cutoff: float | np.ndarray, order: int=2, q: float | None=None) -> np.ndarray` — Highpass (Butterworth; `q` for a resonant biquad; an array `cutoff` sweeps over time).
- `bandpass(x: np.ndarray, lo: float, hi: float, order: int=2) -> np.ndarray` — Butterworth band-pass between `lo` and `hi` Hz.
- `resonate(x: np.ndarray, freq: float | np.ndarray, q: float=8.0) -> np.ndarray` — Resonant band-pass (constant 0 dB peak) at `freq`, which may sweep (array).
- `eq(x: np.ndarray, kind: str, freq: float, gain_db: float, q: float=0.7071) -> np.ndarray` — One EQ band: "peak", "lowshelf" or "highshelf".
- `formant(x: np.ndarray, vowel: str='a', q: float=6.0) -> np.ndarray` — Vowel-ish colouring via three parallel resonances ("a", "e", "i", "o", "u").
- `drive(x: np.ndarray, amount: float=2.0) -> np.ndarray` — Soft saturation (tanh), level-compensated so +-1 stays +-1.
- `pan(x: np.ndarray, position: float=0.0) -> np.ndarray` — Constant-power pan, -1 left .. +1 right; on stereo input it balances.
- `widen(x: np.ndarray, ms: float=8.0) -> np.ndarray` — Haas widening: the right channel delayed by `ms` (output same length).
- `delay(x: np.ndarray, time: float=0.25, feedback: float=0.35, mix_: float=0.3, taps: int=6, tone: float=5000) -> np.ndarray` — Feedback echo (each repeat darker); the output is longer by the echo tail.
- `impulse_response(decay: float=1.5, predelay: float=0.01, tone: float=7000, seed: int=0, early: bool=True) -> np.ndarray` — A stereo reverb impulse response: decorrelated noise falling 60 dB over `decay` s, darker as it goes.
- `reverb(x: np.ndarray, decay: float=1.5, mix_: float=0.25, predelay: float=0.01, tone: float=7000, seed: int=0, circular: bool=False) -> np.ndarray` — Convolution reverb, returns stereo dry + `mix_` x wet.
- `make_loop(x: np.ndarray, length: float | int, crossfade: float=0.5) -> np.ndarray` — Cut a seamless loop of `length` from `x` (which must be `length + crossfade` long).
- `wrap_tail(x: np.ndarray, length: int) -> np.ndarray` — Fold everything past `length` samples back onto the start (seamless loop of `length`).
- `trim_silence(x: np.ndarray, floor_db: float=-60.0, keep: float=0.02) -> np.ndarray` — Cut the tail once it stays `floor_db` below the peak (keeps `keep` s more).
- `limit(x: np.ndarray, ceiling_db: float=-1.0, lookahead: float=0.004, loop: bool=False) -> np.ndarray` — Look-ahead peak limiter: no sample exceeds `ceiling_db`; gain moves smoothly over `lookahead`.
- `loudness(x: np.ndarray) -> float` — Integrated loudness in LUFS (BS.1770 K-weighting and gating; whole-file block if under 0.4 s).
- `peak_db(x: np.ndarray) -> float` — Sample peak in dBFS.
- `rms_db(x: np.ndarray) -> float` — RMS level in dBFS (both channels).
- `centroid(x: np.ndarray) -> float` — Spectral centroid in Hz (magnitude-weighted mean frequency; low = dark, high = bright).
- `seam(x: np.ndarray) -> dict` — How the end of a loop meets its start: `ratio` = jump / 99.9th-percentile step; ok <= 1.5.
- `measure(x: np.ndarray, loop: bool=False) -> dict` — Everything to check without listening: duration, peak, RMS, LUFS, centroid, DC, clipping, edges, seam.
- `hf_stripes(x: np.ndarray, *, frame: float=0.005, window: float=0.05, cutoff: float=12000.0, audible_db: float=-40.0, ratio: float=8.0) -> list[tuple[float, float, float]]` — Mid-file clicks: 5 ms frames whose >12 kHz energy jumps `ratio`x over its +-50 ms and is audible; [(t, ratio, dB)].
- `ogg_matches(wav: np.ndarray, ogg: np.ndarray, *, min_corr: float=0.98, silence_db: float=-60.0) -> list[str]` — Problems with a decoded OGG against its WAV: misaligned at sample 0, uncorrelated, or a length difference that is not silence.
- `master(x: np.ndarray, lufs: float=-16.0, ceiling_db: float=-1.0, max_limit_db: float=6.0, loop: bool=False, fade_in: float=0.002, fade_out: float=0.015) -> np.ndarray` — Final stage: stereo, DC removed, gain to `lufs` (but never > `max_limit_db` into the limiter), limit, fade.
- `write_wav(path: str | Path, x: np.ndarray, dither: bool=True) -> Path` — Write 16-bit PCM WAV (stereo if `x` is), TPDF-dithered; values are clipped to +-1.
- `read_wav(path: str | Path) -> np.ndarray` — Read a 16-bit PCM WAV as float (mono `(n,)` or `(n, ch)`).
- `ffmpeg_path() -> str | None` — The ffmpeg binary (PATH, then /opt/homebrew/bin), or None.
- `encode_ogg(wav_path: str | Path, ogg_path: str | Path | None=None, quality: float=5) -> Path` — Encode a WAV to Ogg Vorbis with ffmpeg (`quality` 0-10, 5 is about 160 kbps stereo).
- `decode(path: str | Path) -> np.ndarray` — Decode any audio file ffmpeg reads (e.g. the OGG you wrote) to float stereo at SR.
- `write_pair(stem: str | Path, x: np.ndarray, quality: float=5) -> tuple[Path, Path]` — Write `<stem>.wav` and `<stem>.ogg`; returns both paths.
- `spectrogram_png(x: np.ndarray, path: str | Path, width: int=900, height: int=300, max_hz: float=16000, floor_db: float=-90) -> Path` — Spectrogram (time left to right, log-ish frequency bottom to top) plus waveform strip, as PNG.

## `clicks`

Mid-file click detection for one-shots that skips intended strikes: the check `audio.measure` cannot make.

- `click_score(x: np.ndarray, *, cutoff: float=10000.0, frame: float=0.005) -> tuple[float, float]` — (score, seconds) of the worst non-onset click: a frame's >`cutoff` energy over the median of +-10 frames.

## `oneshot`

Assembly helpers for one-shot sounds: strict placement, fit to length with a fade, per-sample pan.

- `put(dst: np.ndarray, src: np.ndarray, at: float | int) -> np.ndarray` — `audio.place`, but raise instead of silently cutting `src` off past the end of `dst` (a mid-file click).
- `pan_to(x: np.ndarray, pos: float | np.ndarray) -> np.ndarray` — Mono `x` to stereo with constant-power pan; `pos` (-1 left .. +1 right) may be per-sample.
- `fit(x: np.ndarray, dur: float, fade_out: float) -> np.ndarray` — Pad or cut to `dur` seconds with a raised-cosine fade over the last `fade_out` seconds.

## `sfx`

Procedural sound effects for Many Builders: one seeded generator per game event,

- `CATEGORIES` — constant
- `MAX_LIMIT_DB` — constant
- `class Sound` — A registered generator: its category, whether it loops, and a one-line description.
- `sound(name: str, category: str, description: str, loop: bool=False, **params)` — Decorator registering a generator under `name` (kebab-case).
- `render(name: str, seed: int=0, variation: int=0, master: bool=True, **params) -> np.ndarray` — One mastered (stereo, category-loudness, faded or seamlessly looped) take of sound `name`.
- `build_sfx(name: str, out_dir: str | Path, variations: int=3, seed: int=0, previews: bool=True, **params) -> dict` — Write `<name>_<n>.wav/.ogg` (n = 1..variations) and a `kind="sfx"` manifest; returns `catalog.validate_package`.
- `build_all(out_root: str | Path, variations: int=3, seed: int=0, names: list[str] | None=None) -> dict` — Build every sound in `SOUNDS` (or `names`) into `<out_root>/<name>/`; returns `{name: report}`.
- `describe() -> list[dict]` — Name, category, loop flag and description of every registered sound.