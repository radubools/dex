"""Zero contours of a gridded function as evenly spaced floor points, for drawing a curve as a row of spawned markers."""
import numpy as np


def contour_dots(g, f, gap=0.25, limit=4.85, digits=2):
    """[x, z] points of the zero contour of f (f[iz, ix] sampled at g[ix], g[iz]), at least `gap` apart and within ±limit.

    Every sign change along a grid edge is placed by linear interpolation; the crossings are then
    thinned greedily in sorted order. Spawns cannot rotate, so a row of small markers at these points
    is how a story draws a curved boundary (a Bayes boundary, a level set) on the floor.
    """
    g = np.asarray(g, float)
    f = np.asarray(f, float)
    h = g[1] - g[0]
    cross = []
    for axis in (0, 1):
        a = f[:-1, :] if axis == 0 else f[:, :-1]
        b = f[1:, :] if axis == 0 else f[:, 1:]
        for iz, ix in zip(*np.nonzero(np.sign(a) != np.sign(b))):
            u = a[iz, ix] / (a[iz, ix] - b[iz, ix])
            cross.append((g[ix] + (0 if axis == 0 else u * h), g[iz] + (u * h if axis == 0 else 0)))
    cross.sort()
    kept = []
    for p in cross:
        if max(abs(p[0]), abs(p[1])) > limit:
            continue
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= gap * gap for q in kept):
            kept.append(p)
    return [[round(float(x), digits), round(float(z), digits)] for x, z in kept]
