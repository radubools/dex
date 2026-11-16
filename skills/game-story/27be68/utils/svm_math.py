"""Model Workshop's SVM maths in Python (max margin, Pegasos soft margin, RBF lift and cut), checkable against the game's own JS.

Mirrors <game>/src/ml/svm.js (svmFit by Pegasos on the engine's makeRng,
maxMarginSeparator, supportVectors, hinge, rbfLift, liftedPlaneCut,
ringBoundary) and the plain-line pieces of src/actions/svm.js (weightsOf,
normLine, pickSide). Floating-point operations run in the same order as the
JavaScript, so results agree with the game to the last bit or very nearly.
Points are dicts {"x", "z", "cls"} (and "y" for a lifted point); a model is
{"w1", "w2", "b"} with f(p) = w1·x + w2·z + b, class 1 where f > 0. The
game's num() for printing these is story.fmt_fixed.
"""
from __future__ import annotations

import math

from .rng import make_rng

RAD = math.pi / 180


def label(p) -> int:
    """A point's SVM label y: +1 for class 1, −1 for any other class."""
    return 1 if p["cls"] == 1 else -1


def score(m, p) -> float:
    """f(p) = w1·x + w2·z + b for model m at point p."""
    return m["w1"] * p["x"] + m["w2"] * p["z"] + m["b"]


def svm_fit(points, C=1.0, epochs=200, seed=1) -> dict:
    """svm.js svmFit: Pegasos on λ/2‖w‖² + mean hinge, λ = 1/(C n), bias as a feature, the second half averaged."""
    n = len(points)
    rng = make_rng(seed)
    lam = 1 / (C * n)
    w = [0.0, 0.0, 0.0]
    avg = [0.0, 0.0, 0.0]
    T = epochs * n
    counted = 0
    for t in range(1, T + 1):
        p = points[math.floor(rng() * n)]
        f = [p["x"], p["z"], 1]
        y = label(p)
        eta = 1 / (lam * t)
        m = y * (w[0] * f[0] + w[1] * f[1] + w[2] * f[2])
        w = [(1 - eta * lam) * wi + (eta * y * f[i] if m < 1 else 0) for i, wi in enumerate(w)]
        if t > T / 2:
            for i in range(3):
                avg[i] += w[i]
            counted += 1
    return {"w1": avg[0] / counted, "w2": avg[1] / counted, "b": avg[2] / counted}


def margin_width(m) -> float:
    """svm.js svmMargin: the street's width 2/‖w‖."""
    return 2 / math.hypot(m["w1"], m["w2"])


def support_vectors(points, m, tol=1e-2) -> list:
    """svm.js supportVectors: the points with y·f(p) ≤ 1 + tol (the svm actions pass tol 0.02)."""
    return [p for p in points if label(p) * score(m, p) <= 1 + tol]


def hinge(points, m, C=1.0) -> float:
    """svm.js hinge: the soft-margin objective ½‖w‖² + C·Σ max(0, 1 − y·f(p))."""
    loss = sum(max(0, 1 - label(p) * score(m, p)) for p in points)
    return 0.5 * (m["w1"] ** 2 + m["w2"] ** 2) + C * loss


def _gap_at(points, angle):
    c, s = math.cos(angle), math.sin(angle)
    lo, hi = math.inf, -math.inf
    for p in points:
        v = c * p["x"] + s * p["z"]
        if label(p) > 0:
            lo = min(lo, v)
        else:
            hi = max(hi, v)
    return (lo - hi) / 2, (lo + hi) / 2


def max_margin_separator(points) -> dict:
    """svm.js maxMarginSeparator: the hard-margin model by an angle scan and ternary refine, svm_fit(C=100) if not separable."""
    best_angle, best_half = 0.0, -math.inf
    N = 720
    for i in range(N):
        angle = (2 * math.pi * i) / N
        half, _ = _gap_at(points, angle)
        if half > best_half:
            best_angle, best_half = angle, half
    lo, hi = best_angle - (2 * math.pi) / N, best_angle + (2 * math.pi) / N
    for _ in range(60):
        a = lo + (hi - lo) / 3
        b = hi - (hi - lo) / 3
        if _gap_at(points, a)[0] < _gap_at(points, b)[0]:
            lo = a
        else:
            hi = b
    angle = (lo + hi) / 2
    half, mid = _gap_at(points, angle)
    if not half > 0:
        return svm_fit(points, C=100, epochs=400)
    return {"w1": math.cos(angle) / half, "w2": math.sin(angle) / half, "b": -mid / half}


def rbf_height(p, gamma=0.3, centre=(0.0, 0.0)) -> float:
    """svm.js rbfLift for one point: the height 4·e^(−γ‖p − centre‖²) (the game's lift uses the centre (0, 0))."""
    return 4 * math.exp(-gamma * ((p["x"] - centre[0]) ** 2 + (p["z"] - centre[1]) ** 2))


def lifted_plane_cut(pts) -> tuple:
    """svm.js liftedPlaneCut on points carrying "y": (height, above class, errors), the first best in the game's scan order."""
    ys = sorted(set(p["y"] for p in pts))
    cuts = [ys[0] - 0.1] + [(ys[i] + y) / 2 for i, y in enumerate(ys[1:])] + [ys[-1] + 0.1]
    best = None
    for height in cuts:
        for above in (0, 1):
            errors = sum(1 for p in pts if (p["y"] > height) != ((1 if p["cls"] == 1 else 0) == above))
            if best is None or errors < best[2]:
                best = (height, above, errors)
    return best


def ring_boundary(height, gamma=0.3):
    """svm.js ringBoundary: the radius where the RBF lift meets a cut at `height`, None outside (0, 4)."""
    if height <= 0 or height >= 4:
        return None
    return math.sqrt(-math.log(height / 4) / gamma)


def line_weights(angle_deg, offset, side) -> dict:
    """actions/svm.js weightsOf for a plain line (svm.line): a unit w, so f is the signed distance."""
    a = angle_deg * RAD
    return {"w1": -side * math.sin(a), "w2": side * math.cos(a), "b": -side * offset}


def norm_line(angle, offset, side=1) -> tuple:
    """actions/svm.js normLine: the same line with its angle in [0, 180), as (angle, offset, side)."""
    a = ((angle % 360) + 360) % 360
    o, s = offset, side
    if a >= 180:
        a, o, s = a - 180, -o, -s
    return a, o, s


def pick_side(points, angle, offset, side) -> int:
    """actions/svm.js pickSide: the side (±1) that puts class 1 where it makes fewer mistakes; a tie keeps `side`."""
    def errors(sd):
        w = line_weights(angle, offset, sd)
        return sum(1 for p in points if (1 if score(w, p) > 0 else 0) != p["cls"])
    plus, minus = errors(1), errors(-1)
    if plus != minus:
        return 1 if plus < minus else -1
    return side
