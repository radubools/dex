"""PRIM bump hunting (ESL Algorithm 9.3), numpy only: top-down peeling and bottom-up pasting of one box, and whole-point peel counts.

A box is a (p, 2) array of [low, high] limits; a point is inside when low < x_j < high for every j.
A peel removes ceil(alpha * n) whole points, the ones with the lowest (or highest) values of one
input; the face moves to midway between the last point peeled and the first point kept.
"""
import math

import numpy as np


def inside(X, box):
    """Boolean mask of the rows of X strictly inside box."""
    return np.all((X > box[:, 0]) & (X < box[:, 1]), axis=1)


def peel_candidates(X, y, box, alpha):
    """Every one-face peel of `box`: list of (mean, width, j, side, new box), side 0 = low face, 1 = high face."""
    m = inside(X, box)
    idx = np.flatnonzero(m)
    n = len(idx)
    k = math.ceil(alpha * n)
    out = []
    if k >= n:
        return out
    for j in range(X.shape[1]):
        order = idx[np.argsort(X[idx, j], kind="stable")]
        for side in (0, 1):
            seq = order if side == 0 else order[::-1]
            gone, last_kept = seq[k - 1], seq[k]
            a, b = X[gone, j], X[last_kept, j]
            if a == b:  # tied values cannot be split by a face
                continue
            nb = box.copy()
            nb[j, side] = (a + b) / 2
            keep = inside(X, nb)
            width = abs(nb[j, side] - box[j, side])
            out.append((float(y[keep].mean()), float(width), j, side, nb))
    return out


def peel(X, y, alpha=0.1, min_size=10, box=None):
    """Top-down peeling: the sequence of boxes, starting with `box` (default: all the data), each peel
    the one leaving the highest box mean (ties go to the widest strip), until at most `min_size` points remain."""
    if box is None:
        box = np.column_stack([X.min(0) - 1e-9, X.max(0) + 1e-9])
    boxes = [box]
    while inside(X, box).sum() > min_size:
        c = peel_candidates(X, y, box, alpha)
        if not c:
            break
        best = max(c, key=lambda t: (t[0], t[1]))
        box = best[4]
        boxes.append(box)
    return boxes


def paste_candidates(X, y, box, alpha, bounds):
    """Every one-face paste of `box`: the face moved out past the ceil(alpha * n) nearest outside points
    that lie within the box's other limits; list of (mean, j, side, new box). `bounds` caps each face."""
    n = int(inside(X, box).sum())
    k = max(1, math.ceil(alpha * n))
    out = []
    for j in range(X.shape[1]):
        others = np.ones(len(X), bool)
        for i in range(X.shape[1]):
            if i != j:
                others &= (X[:, i] > box[i, 0]) & (X[:, i] < box[i, 1])
        for side in (0, 1):
            if side == 0:
                cand = np.flatnonzero(others & (X[:, j] <= box[j, 0]))
                cand = cand[np.argsort(-X[cand, j], kind="stable")]
            else:
                cand = np.flatnonzero(others & (X[:, j] >= box[j, 1]))
                cand = cand[np.argsort(X[cand, j], kind="stable")]
            if len(cand) == 0:
                continue
            nb = box.copy()
            if len(cand) > k:
                a, b = X[cand[k - 1], j], X[cand[k], j]
                if a == b:
                    continue
                nb[j, side] = (a + b) / 2
            else:
                nb[j, side] = bounds[j, side]
            out.append((float(y[inside(X, nb)].mean()), j, side, nb))
    return out


def paste(X, y, box, alpha=0.1, bounds=None):
    """Bottom-up pasting: grow `box` one face at a time, taking the paste with the highest box mean,
    as long as the mean increases. Returns the boxes, `box` first."""
    if bounds is None:
        bounds = np.column_stack([X.min(0) - 1e-9, X.max(0) + 1e-9])
    boxes = [box]
    mean = y[inside(X, box)].mean()
    while True:
        c = paste_candidates(X, y, box, alpha, bounds)
        if not c:
            break
        best = max(c, key=lambda t: t[0])
        if best[0] <= mean:
            break
        mean, box = best[0], best[3]
        boxes.append(box)
    return boxes


def peel_counts(N, alpha, stop=1):
    """Points left after each peel of ceil(alpha * n) whole points, from N down to `stop` or fewer."""
    seq = [N]
    while seq[-1] > stop:
        seq.append(seq[-1] - math.ceil(alpha * seq[-1]))
    return seq
