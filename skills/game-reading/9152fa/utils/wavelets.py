"""Orthonormal discrete wavelet transforms (ESL Section 5.9), numpy only: Haar and symmlet-8 pyramids, soft thresholding, SureShrink.

Haar and Daubechies symmlet-8 filters, the periodic pyramid transform and its inverse,
the basis matrix W, the Haar pyramid with an operation count (Exercise 5.19), soft
thresholding (5.69) and SureShrink, and a smoothing spline chosen by leave-one-out CV.
"""
import numpy as np

SQ2 = np.sqrt(2.0)
HAAR = np.array([1.0, 1.0]) / SQ2
# Daubechies' least-asymmetric wavelet with 8 vanishing moments ("symmlet-8", 16 taps),
# scaling (low-pass) filter h(k). figures.py checks that these satisfy the conditions that
# define an orthonormal wavelet with 8 vanishing moments, to 1e-10.
SYM8 = np.array([
    -0.0033824159510061256, -0.0005421323317911481, 0.03169508781149298, 0.007607487324917605,
    -0.1432942383508097, -0.061273359067658524, 0.4813596512583722, 0.7771857517005235,
    0.3644418948353314, -0.05194583810770904, -0.027219029917056003, 0.049137179673607506,
    0.003808752013890615, -0.01495225833704823, -0.0003029205147213668, 0.0018899503327594609])


def highpass(h):
    """The wavelet (high-pass) filter g(n) = (-1)^n h(L-1-n) of a scaling filter h."""
    L = len(h)
    return np.array([(-1) ** n * h[L - 1 - n] for n in range(L)])


def analysis_step(a, h):
    """One pyramid step, periodic: (smooth, detail), each half the length of a."""
    g, n = highpass(h), len(a)
    idx = (2 * np.arange(n // 2)[:, None] + np.arange(len(h))[None, :]) % n
    return a[idx] @ h, a[idx] @ g


def synthesis_step(s, d, h):
    """The inverse of analysis_step: the length-2m signal whose step gives (s, d)."""
    g, m = highpass(h), len(s)
    out = np.zeros(2 * m)
    idx = (2 * np.arange(m)[:, None] + np.arange(len(h))[None, :]) % (2 * m)
    np.add.at(out, idx, s[:, None] * h[None, :] + d[:, None] * g[None, :])
    return out


def dwt(y, h, j0=0):
    """Wavelet transform of y (length 2^J) down to V_j0: [V_j0 coefficients, W_j0, ..., W_(J-1)]."""
    a, details = np.asarray(y, float), []
    while len(a) > 2 ** j0:
        a, d = analysis_step(a, h)
        details.append(d)
    return [a] + details[::-1]


def idwt(coeffs, h):
    """Inverse of dwt: the signal from [V_j0, W_j0, ..., W_(J-1)]."""
    a = coeffs[0]
    for d in coeffs[1:]:
        a = synthesis_step(a, d, h)
    return a


def flat(coeffs):
    """The coefficient list as one vector y* (coarse first)."""
    return np.concatenate(coeffs)


def unflat(v, j0, J):
    """A vector y* back into the list [V_j0, W_j0, ..., W_(J-1)]."""
    sizes = [2 ** j0] + [2 ** j for j in range(j0, J)]
    return np.split(np.asarray(v, float), np.cumsum(sizes)[:-1])


def basis_matrix(N, h, j0=0):
    """The N x N matrix W whose columns are the basis vectors, so that dwt(y) = W^T y."""
    J = int(np.log2(N))
    return np.column_stack([idwt(unflat(e, j0, J), h) for e in np.eye(N)])


def basis_function(N, h, level, k, j0=0, kind="psi"):
    """Basis vector of length N: psi_{level,k} (detail) or phi_{level,k} (smooth, level = j0)."""
    J = int(np.log2(N))
    c = [np.zeros(2 ** j0)] + [np.zeros(2 ** j) for j in range(j0, J)]
    if kind == "phi":
        c[0][k] = 1.0
    else:
        c[1 + level - j0][k] = 1.0
    return idwt(c, h)


def haar_pyramid(y):
    """Exercise 5.19: the Haar transform by pairwise sums and differences, counting operations.

    Returns (coefficient list [V_0, W_0, ..., W_(J-1)], levels, ops) where levels lists the
    (smooth, detail) of each step from fine to coarse and ops counts additions,
    subtractions and multiplications.
    """
    a, details, levels, ops, c = np.asarray(y, float), [], [], 0, 1 / SQ2
    while len(a) > 1:
        e, o = a[0::2], a[1::2]
        s, d = (e + o) * c, (e - o) * c
        ops += 4 * len(s)  # one add, one subtract, two multiplies per pair
        levels.append((s, d))
        details.append(d)
        a = s
    return [a] + details[::-1], levels, ops


def haar_inverse(coeffs):
    """The inverse Haar pyramid, counting operations: (signal, ops)."""
    a, ops, c = coeffs[0], 0, 1 / SQ2
    for d in coeffs[1:]:
        out = np.empty(2 * len(a))
        out[0::2], out[1::2] = (a + d) * c, (a - d) * c
        ops += 4 * len(a)
        a = out
    return a, ops


def soft(x, lam):
    """Soft thresholding (5.69): sign(x)(|x| - lam)_+."""
    return np.sign(x) * np.maximum(np.abs(x) - lam, 0.0)


def mad_sigma(d):
    """Noise standard deviation from the finest-level coefficients: median |d| / 0.6745."""
    return float(np.median(np.abs(d)) / 0.6745)


def sure_threshold(x):
    """SureShrink threshold for coefficients x of unit noise (Donoho and Johnstone's hybrid rule).

    Minimises Stein's unbiased risk estimate n - 2 #{|x_i| <= t} + sum min(x_i^2, t^2) over t
    in [0, sqrt(2 log n)], falling back to the universal sqrt(2 log n) when the level is sparse.
    """
    n = len(x)
    univ = np.sqrt(2 * np.log(n))
    if (np.sum(x ** 2) - n) / n <= np.log2(n) ** 1.5 / np.sqrt(n):
        return float(univ)
    a = np.sort(np.abs(x))
    cand = np.concatenate([[0.0], a[a <= univ]])
    risk = [n - 2 * np.sum(a <= t) + np.sum(np.minimum(a, t) ** 2) for t in cand]
    return float(cand[int(np.argmin(risk))])


def sureshrink(y, h, j0):
    """Wavelet smoothing as in Figure 5.17: (fit, coefficients, shrunk coefficients, sigma).

    Transform to V_j0, keep the V_j0 coefficients, soft-threshold each detail level at
    sigma times its SURE threshold, sigma from the finest level, and transform back.
    """
    c = dwt(y, h, j0)
    sigma = mad_sigma(c[-1])
    shrunk = [c[0].copy()] + [soft(d, sigma * sure_threshold(d / sigma)) for d in c[1:]]
    return idwt(shrunk, h), c, shrunk, sigma


def spline_cv(x, y, K, lams):
    """Smoothing spline (5.17) with lambda chosen by leave-one-out CV (5.26): (fit, lambda, df).

    K is the Reinsch penalty matrix; the eigen-decomposition (5.19)-(5.20) is used once for all lambda.
    """
    d, U = np.linalg.eigh(K)
    d = np.maximum(d, 0.0)
    uy = U.T @ y
    best = None
    for lam in lams:
        rho = 1 / (1 + lam * d)
        f = U @ (rho * uy)
        sii = (U ** 2) @ rho
        cv = np.mean(((y - f) / (1 - sii)) ** 2)
        if best is None or cv < best[0]:
            best = (cv, f, lam, float(rho.sum()))
    return best[1], best[2], best[3]
