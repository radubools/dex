"""Assembly helpers for one-shot sounds: strict placement, fit to length with a fade, per-sample pan.

    from utils import audio as A, oneshot as O
    x = np.zeros(A.n_samples(0.8))
    O.put(x, A.bar([1200, 2770], [0.02, 0.008], [1, 0.4], 0.09, r), 0.25)   # raises if it would be cut off
    y = O.fit(A.reverb(O.pan_to(x, 0.2)), 0.7, 0.08)
"""

from __future__ import annotations

import numpy as np

from . import audio as A


def put(dst: np.ndarray, src: np.ndarray, at: float | int) -> np.ndarray:
    """`audio.place`, but raise instead of silently cutting `src` off past the end of `dst` (a mid-file click)."""
    i = int(round(at * A.SR)) if isinstance(at, float) else int(at)
    if i + len(src) > len(dst):
        raise ValueError(f"event at {i / A.SR:.3f}s runs {(i + len(src) - len(dst)) / A.SR:.3f}s past the buffer")
    return A.place(dst, src, at)


def pan_to(x: np.ndarray, pos: float | np.ndarray) -> np.ndarray:
    """Mono `x` to stereo with constant-power pan; `pos` (-1 left .. +1 right) may be per-sample."""
    x = A.mono(x)
    th = (np.asarray(pos, float) + 1) * np.pi / 4
    return np.stack([x * np.cos(th), x * np.sin(th)], axis=1)


def fit(x: np.ndarray, dur: float, fade_out: float) -> np.ndarray:
    """Pad or cut to `dur` seconds with a raised-cosine fade over the last `fade_out` seconds."""
    y = A.pad_to(np.asarray(x, float), A.n_samples(dur)).copy()
    n = A.n_samples(fade_out)
    w = 0.5 * (1 + np.cos(np.linspace(0, np.pi, n)))
    y[-n:] *= w[:, None] if y.ndim == 2 else w
    return y
