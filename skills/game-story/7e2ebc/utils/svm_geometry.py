"""Hard-margin SVM geometry for Model Workshop's svm.line convention: signed sides, streets, the widest street and its posts."""
import math


def normal(angle):
    """The unit normal (−sin a, cos a) of a line whose direction is `angle` degrees from +x toward +z (svm.js)."""
    a = math.radians(angle)
    return (-math.sin(a), math.cos(a))


def side(p, angle, offset):
    """Signed distance of point p ([x, z, ...]) from the line n·p = offset at `angle` (svm.line's angle, offset)."""
    n = normal(angle)
    return n[0] * p[0] + n[1] * p[1] - offset


def street(points, angle):
    """(half-width, offset) of the widest street between class 0 and class 1 rows [x, z, cls] at `angle`; half-width < 0 when they overlap."""
    hi0 = max(side(p, angle, 0) for p in points if p[2] == 0)
    lo1 = min(side(p, angle, 0) for p in points if p[2] == 1)
    return (lo1 - hi0) / 2, (lo1 + hi0) / 2


def widest(points, step=0.1, refine=60):
    """The hard-margin SVM by an angle scan (step degrees) and golden-section refinement: (angle, offset, half-width)."""
    n = int(round(180 / step))
    best = max((street(points, i * step)[0], i * step) for i in range(-n, n))
    lo, hi = best[1] - 2 * step, best[1] + 2 * step
    for _ in range(refine):                      # the half-width is unimodal near its peak
        m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if street(points, m1)[0] < street(points, m2)[0]:
            lo = m1
        else:
            hi = m2
    angle = (lo + hi) / 2
    half, offset = street(points, angle)
    return angle, offset, half


def support(points, angle, offset, half, tol=1e-3):
    """The rows sitting on the street's edges (|side| within `tol` of `half`): the support vectors."""
    return [p for p in points if abs(abs(side(p, angle, offset)) - half) < tol]


def clearance(points, angle, offset):
    """(distance, row) of the point nearest the line: the half-width of a street centred on it, and the seed that stops it."""
    return min(((abs(side(p, angle, offset)), p) for p in points), key=lambda t: t[0])


def separates(points, angle, offset):
    """Whether the line puts every class 1 row on its positive side and every other row on its negative side."""
    return all((side(p, angle, offset) > 0) == (p[2] == 1) for p in points)
