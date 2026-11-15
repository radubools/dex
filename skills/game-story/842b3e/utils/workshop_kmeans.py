"""Python replicas of Model Workshop's k-means (kmeans.iterate, kmeans.elbow) and data.normalize, to compute a lesson's numbers before its e2e."""
import math

import numpy as np

from .rng import make_rng


def kmeans_iterate(points, centroids, max_rounds=30):
    """The game's kmeans.iterate from given centroids: assign, then update + assign until no point switches; returns (labels, centroids, inertia), labels 0-based."""
    P = np.asarray(points, float)[:, :2]
    cs = np.asarray(centroids, float)
    lab = np.argmin(((P[:, None, :] - cs[None]) ** 2).sum(-1), 1)
    for _ in range(max_rounds):
        cs = np.array([P[lab == c].mean(0) if (lab == c).any() else cs[c] for c in range(len(cs))])
        new = np.argmin(((P[:, None, :] - cs[None]) ** 2).sum(-1), 1)
        if (new == lab).all():
            break
        lab = new
    return lab, cs, float(((P - cs[lab]) ** 2).sum())


def assignment_ties(points, centroids, eps=1e-9):
    """Indices of points whose two nearest centroids are within eps in squared distance (the game breaks such ties by index)."""
    P = np.asarray(points, float)[:, :2]
    d = np.sort(((P[:, None, :] - np.asarray(centroids, float)[None]) ** 2).sum(-1), 1)
    return [int(i) for i in np.nonzero(d[:, 1] - d[:, 0] <= eps)[0]] if d.shape[1] > 1 else []


def normalize(points, scale=1.8):
    """data.normalize: each of x, z to (v - mean) / sd (divisor N), times `scale` floor units; returns an N x 2 array (the game also clamps to +-4.8)."""
    P = np.asarray(points, float)[:, :2]
    return (P - P.mean(0)) / P.std(0) * scale


def _d2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _nearest(points, centroids):
    out = []
    for p in points:
        best = 0
        for c in range(1, len(centroids)):
            if _d2(p, centroids[c]) < _d2(p, centroids[best]):
                best = c
        out.append(best)
    return out


def plusplus_start(points, k, seed=1):
    """The game's k-means++ start (src/ml/kmeans.js initCentroids 'plusplus') drawn from makeRng(seed): k centroids as (x, z) tuples."""
    P = [(float(p[0]), float(p[1])) for p in points]
    n, rng = len(P), make_rng(seed)
    out = [P[math.floor(rng() * n)]]
    while len(out) < min(k, n):
        w = [min(_d2(p, c) for c in out) for p in P]
        r, i = rng() * sum(w), 0
        while i < n - 1:
            r -= w[i]
            if not r > 0:
                break
            i += 1
        out.append(P[i])
    return out


def seeded_kmeans(points, k, seed=1, iters=20):
    """The game's kmeans(points, k, {init: 'plusplus', seed}) (what kmeans.elbow runs per k): (labels 0-based, centroids, inertia)."""
    P = [(float(p[0]), float(p[1])) for p in points]
    cs = plusplus_start(P, k, seed)
    lab = _nearest(P, cs)
    for _ in range(iters):
        sums = [[0.0, 0.0, 0] for _ in cs]
        for p, c in zip(P, lab):
            sums[c][0] += p[0]
            sums[c][1] += p[1]
            sums[c][2] += 1
        nxt = [(a / m, b / m) if m else cs[c] for c, (a, b, m) in enumerate(sums)]
        nl = _nearest(P, nxt)
        done = nl == lab and all(_d2(a, b) < 1e-18 for a, b in zip(nxt, cs))
        cs, lab = nxt, nl
        if done:
            break
    return lab, cs, sum(_d2(p, cs[c]) for p, c in zip(P, lab))


def elbow(points, kmax=8, seed=1):
    """What kmeans.elbow returns as inertias: seeded_kmeans' inertia for k = 1..min(kmax, len(points))."""
    return [seeded_kmeans(points, k, seed)[2] for k in range(1, min(kmax, len(points)) + 1)]


def elbow_bend(inertias):
    """The 0-based index of the bar kmeans.elbow marks: the largest second difference of the inertias (first wins a tie)."""
    best, bend = -math.inf, 0
    for i in range(1, len(inertias) - 1):
        dd = (inertias[i - 1] - inertias[i]) - (inertias[i] - inertias[i + 1])
        if dd > best:
            best, bend = dd, i
    return bend
