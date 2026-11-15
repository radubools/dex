"""Self-organizing maps as ESL Section 14.4 describes them, numpy only: online (14.46), weighted (14.47) and batch (14.48) SOMs, and K-means to compare."""
from math import pi

import numpy as np


def grid_coords(q1, q2):
    """Integer coordinates l_j in Q1 x Q2 (1-based) of a q1 x q2 grid, row by row: (K, 2)."""
    return np.array([(a, b) for a in range(1, q1 + 1) for b in range(1, q2 + 1)], float)


def pc_init(X, q1, q2, span=1.0):
    """Prototypes sewn on the first two principal component plane in a regular q1 x q2 pattern,
    reaching `span` standard deviations of each component either side of the mean.
    The grid's longer side runs along the first component (Q1 when the grid is square)."""
    mu = X.mean(0)
    U, d, Vt = np.linalg.svd(X - mu, full_matrices=False)
    sd = d[:2] / np.sqrt(len(X))
    a = np.linspace(-span, span, q1) if q1 > 1 else np.zeros(1)
    b = np.linspace(-span, span, q2) if q2 > 1 else np.zeros(1)
    L = grid_coords(q1, q2)
    A, B = a[(L[:, 0] - 1).astype(int)], b[(L[:, 1] - 1).astype(int)]
    if q2 > q1:
        A, B = B, A
    return mu + np.outer(A * sd[0], Vt[0]) + np.outer(B * sd[1], Vt[1])


def closest(X, M):
    """Index of the closest prototype (Euclidean distance in R^p) for every row of X."""
    return ((X[:, None, :] - M[None]) ** 2).sum(2).argmin(1)


def recon_error(X, M):
    """Reconstruction error: the sum over observations of ||x - m_j||^2 to the closest prototype."""
    return float(((X[:, None, :] - M[None]) ** 2).sum(2).min(1).sum())


def rectangular(r):
    """The simplest neighbourhood: weight 1 for grid distance at most r, else 0."""
    return lambda dist: (dist <= r + 1e-9).astype(float)


def gaussian(sigma):
    """A smooth neighbourhood function h: exp(-d^2 / (2 sigma^2)) of the grid distance d."""
    return lambda dist: np.exp(-dist ** 2 / (2 * sigma ** 2))


def online_som(X, M0, L, passes, R, r_end=1.0, alpha0=1.0, h=None, seed=0, track=False):
    """Online SOM: each x_i in turn moves its closest prototype's grid neighbours by (14.46);
    alpha falls linearly from alpha0 to 0 and the threshold r from R to r_end over all iterations.
    With h (a function of r giving a function of grid distance) the move is weighted as in (14.47).
    Returns (prototypes, errors after each iteration if track else None)."""
    rng = np.random.default_rng(seed)
    M = M0.copy()
    D = np.sqrt(((L[:, None] - L[None]) ** 2).sum(2))      # distances in Q1 x Q2, not in feature space
    order = np.concatenate([rng.permutation(len(X)) for _ in range(passes)])
    T = len(order)
    errs = [recon_error(X, M)] if track else None
    for t, i in enumerate(order):
        frac = t / max(T - 1, 1)
        alpha = alpha0 * (1 - frac)
        r = R + (r_end - R) * frac
        j = int(((M - X[i]) ** 2).sum(1).argmin())
        w = (D[j] <= r + 1e-9).astype(float) if h is None else h(r)(D[j])
        M += alpha * w[:, None] * (X[i] - M)
        if track:
            errs.append(recon_error(X, M))
    return M, errs


def batch_som(X, M0, L, iters, weight, tol=0.0):
    """Batch SOM (14.48): every m_j becomes the weighted mean of the points that mapped to its
    neighbours m_k, weight(grid distance) giving w_k. Returns (prototypes, error per iteration)."""
    M = M0.copy()
    D = np.sqrt(((L[:, None] - L[None]) ** 2).sum(2))
    W = weight(D)
    errs = [recon_error(X, M)]
    for _ in range(iters):
        j = closest(X, M)
        Wx = W[:, j]                                         # (K, N): w for prototype m and point x_i via its winner
        tot = Wx.sum(1)
        keep = tot > 0
        M_new = M.copy()
        M_new[keep] = (Wx[keep] @ X) / tot[keep, None]
        done = np.abs(M_new - M).max() <= tol
        M = M_new
        errs.append(recon_error(X, M))
        if done:
            break
    return M, errs


def lloyd(X, M0, iters=200):
    """K-means (Lloyd's iterations) from the centres M0: assign to the closest, move to the means.
    Returns (centres, final error)."""
    M = M0.copy()
    for _ in range(iters):
        j = closest(X, M)
        M_new = np.array([X[j == k].mean(0) if np.any(j == k) else M[k] for k in range(len(M))])
        if np.allclose(M_new, M):
            break
        M = M_new
    return M, recon_error(X, M)


def kmeans_best(X, K, starts=20, seed=0):
    """The lowest-error K-means solution over `starts` random starts (K distinct observations)."""
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(starts):
        M, e = lloyd(X, X[rng.choice(len(X), K, replace=False)])
        if best is None or e < best[1]:
            best = (M, e)
    return best


def half_sphere(sd=0.6, n=30, seed=0):
    """Exercise 14.5's data: three classes of n points near the unit sphere, noise standard deviation sd.
    Class 0 (theta near 0, about (0, 0, 1)) is the tight one; class 1 is about (1, 0, 0), class 2 about (0, 1, 0)."""
    rng = np.random.default_rng(seed)
    spec = [((-pi / 8, pi / 8), (0, 2 * pi)),
            ((pi / 2 - pi / 4, pi / 2 + pi / 4), (-pi / 4, pi / 4)),
            ((pi / 2 - pi / 4, pi / 2 + pi / 4), (pi / 2 - pi / 4, pi / 2 + pi / 4))]
    X, y = [], []
    for k, ((a, b), (c, d)) in enumerate(spec):
        th, ph = rng.uniform(a, b, n), rng.uniform(c, d, n)
        P = np.c_[np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)]
        X.append(P + rng.normal(0, sd, (n, 3)))
        y += [k] * n
    return np.vstack(X), np.array(y)


def helix(n=200, noise=0.1, seed=0):
    """Exercise 14.13's data: X = (cos s, sin s, s) + 0.1 Z at n equally spaced s in [0, 2 pi]. Returns (s, X)."""
    rng = np.random.default_rng(seed)
    s = np.linspace(0, 2 * pi, n)
    return s, np.c_[np.cos(s), np.sin(s), s] + noise * rng.normal(size=(n, 3))
