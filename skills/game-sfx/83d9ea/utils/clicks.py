"""Mid-file click detection for one-shots that skips intended strikes: the check `audio.measure` cannot make.

`audio.measure` only looks at a file's edges, and `audio.hf_stripes` flags every
strike onset too. `click_score` skips onsets, so what it finds is something cut
off: a partial stopped at its buffer end, an event placed past the end of a buffer.
Clean audio scores about 1-3; a truncated partial 10-100.
"""

from __future__ import annotations

import numpy as np

from . import audio as A


def click_score(x: np.ndarray, *, cutoff: float = 10000.0, frame: float = 0.005) -> tuple[float, float]:
    """(score, seconds) of the worst non-onset click: a frame's >`cutoff` energy over the median of +-10 frames.

    A frame where the full-band level jumps up by more than 6 dB (the two frames
    after against the two before) is an intended onset and is skipped; so is one
    where new energy at new frequencies below 9 kHz arrives (a quieter strike under
    a louder ring). A quiet strike that is neither still scores high: prove it by
    rendering the same take without it.
    """
    mono = A.mono(x)
    m = A.highpass(mono, cutoff, order=4)
    f = A.n_samples(frame)
    starts = list(range(0, len(m) - f, f))
    e = np.array([np.sum(m[i:i + f] ** 2) for i in starts]) + 1e-12
    if len(e) < 3 or A.amp_to_db(float(np.sqrt(e.max() / f))) < -70:  # nothing up there at all
        return 0.0, 0.0
    full = np.array([np.sum(mono[i:i + f] ** 2) for i in starts]) + 1e-12
    e = np.r_[e, e[-1], e[-1]]
    lowband = np.fft.rfftfreq(2 * f, 1 / A.SR) < 9000
    win = np.hanning(2 * f)

    def spectrum(a, b):
        seg = A.pad_to(mono[max(a, 0):max(b, 0)], 2 * f)
        return np.abs(np.fft.rfft(seg * win))[lowband]

    w = 10
    worst = (0.0, 0.0)
    for i in range(1, len(full) - 1):
        before, after = full[max(0, i - 2):i].mean(), full[i:i + 2].mean()
        if after > before * 4:  # +6 dB: an onset
            continue
        s0, s1 = spectrum(starts[i] - 2 * f, starts[i]), spectrum(starts[i] + f, starts[i] + 3 * f)
        if np.sum(np.maximum(s1 - s0, 0)) > 0.5 * np.sum(s0):  # a new strike under a louder ring
            continue
        # a click is brief: it stands above the frame before and what follows within two frames
        spike = e[i] / max(e[i - 1], min(e[i + 1], e[i + 2]))
        s = min(e[i] / np.median(np.r_[e[max(0, i - w):i], e[i + 1:i + 1 + w]]), spike)
        if s > worst[0]:
            worst = (float(s), starts[i] / A.SR)
    return worst
