"""Python replicas of Model Workshop's k-means (kmeans.iterate) and data.normalize, to compute a lesson's numbers before its e2e."""
import numpy as np


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
