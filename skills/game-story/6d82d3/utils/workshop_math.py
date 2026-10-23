"""Model Workshop's regression maths in Python (rng, datasets, least squares), checkable against the game's own JS.

Mirrors ~/games/engine/src/core/rng.js (makeRng), <game>/src/ml/datasets.js (the
line, quadratic, heteroscedastic, correlated and custom generators) and
src/ml/linear.js (leastSquares, leastSquares2, solve, polyFit), so a story can
compute the numbers the game will show. `game_numbers` runs the game's own JS
through node (workshop_numbers.mjs) for the same requests, to compare. Number
formatting lives in numfmt.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME_ROOT = Path.home() / "games" / "model-workshop"
LIMIT = 4.6
MASK = 0xFFFFFFFF


def _i32(v: int) -> int:
    v &= MASK
    return v - (1 << 32) if v & 0x80000000 else v


def _imul(a: int, b: int) -> int:
    return _i32((a & MASK) * (b & MASK))


def make_rng(seed: int):
    """mulberry32, exactly as the engine's makeRng."""
    state = [_i32(seed & MASK)]

    def rng() -> float:
        a = _i32(state[0] + 0x6D2B79F5)
        state[0] = a
        t = _imul(a ^ ((a & MASK) >> 15), 1 | a)
        t = _i32(_i32(t + _imul(t ^ ((t & MASK) >> 7), 61 | t)) ^ t)
        return ((t ^ ((t & MASK) >> 14)) & MASK) / 4294967296

    return rng


def gauss(rng) -> float:
    """A standard normal sample from rng (Box–Muller), as datasets.js's gauss."""
    u = 0.0
    while u == 0:
        u = rng()
    return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * rng())


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def make_dataset(kind: str, n: int = 40, noise: float = 0.2, seed: int = 1, points=None) -> list[dict]:
    """The game's makeDataset for the regression generators (and custom points)."""
    rng = make_rng(seed)
    out = []
    xs = lambda i: (-4 + 8 * i / (n - 1) + 0.3 * (rng() - 0.5)) if n > 1 else 0.0  # noqa: E731
    if kind == "line":
        for i in range(n):
            x = xs(i)
            out.append({"x": x, "z": 0.0, "cls": 0, "y": 0.6 * x + 2.5 + noise * gauss(rng)})
    elif kind == "quadratic":
        for i in range(n):
            x = xs(i)
            out.append({"x": x, "z": 0.0, "cls": 0, "y": _clamp(0.25 * x * x + 0.5 + noise * gauss(rng), 0, 5.2)})
    elif kind == "heteroscedastic":
        for i in range(n):
            x = xs(i)
            sd = (0.05 + 0.16 * (x + 4)) * (0.6 + 2 * noise)
            out.append({"x": x, "z": 0.0, "cls": 0, "y": _clamp(0.4 * x + 2.5 + sd * gauss(rng), 0, 5.2)})
    elif kind == "correlated":
        centres = [(-1.4, -1.4), (1.4, 1.4)]
        per = [n // 2 + (1 if i < n % 2 else 0) for i in range(2)]
        for cls, m in enumerate(per):
            for _ in range(m):
                t = 1.4 * gauss(rng)
                e = (0.15 + 0.4 * noise) * gauss(rng)
                x = centres[cls][0] + t + e
                z = centres[cls][1] + t - e
                out.append({"x": x, "z": z, "cls": cls,
                            "y": _clamp(0.3 * x + 0.3 * z + 2.5 + 0.5 * noise * gauss(rng), 0, 5.2)})
    elif kind == "custom":
        for p in points or []:
            x, z, cls = p[0], p[1], (p[2] if len(p) > 2 else 0)
            q = {"x": x, "z": z, "cls": cls}
            if len(p) > 3:
                q["y"] = p[3]
            out.append(q)
    else:
        raise ValueError(kind)
    return [{"id": f"p{i}", **p, "x": _clamp(p["x"], -LIMIT, LIMIT), "z": _clamp(p["z"], -LIMIT, LIMIT)}
            for i, p in enumerate(out)]


def _mean(a):
    a = list(a)
    return sum(a) / (len(a) or 1)


def least_squares(pts) -> dict:
    """The closed-form best line {w, b} for x → y, as linear.js's leastSquares."""
    mx, my = _mean(p["x"] for p in pts), _mean(p["y"] for p in pts)
    sxy = sum((p["x"] - mx) * (p["y"] - my) for p in pts)
    sxx = sum((p["x"] - mx) ** 2 for p in pts)
    w = sxy / sxx if sxx else 0.0
    return {"w": w, "b": my - w * mx}


def solve(A, b):
    """Gaussian elimination with partial pivoting, as linear.js's solve."""
    n = len(b)
    M = [list(row) + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = c
        for r in range(c + 1, n):
            if abs(M[r][c]) > abs(M[piv][c]):
                piv = r
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r == c:
                continue
            f = M[r][c] / M[c][c]
            for k in range(c, n + 1):
                M[r][k] -= f * M[c][k]
    return [0.0 if abs(row[i]) < 1e-12 else row[n] / row[i] for i, row in enumerate(M)]


def least_squares2(pts) -> dict:
    """The best plane {w1, w2, b} for (x, z) → y, as linear.js's leastSquares2."""
    A =[[0.0] * 3 for _ in range(3)]
    r = [0.0] * 3
    for p in pts:
        f = [p["x"], p["z"], 1.0]
        for i in range(3):
            r[i] += f[i] * p["y"]
            for j in range(3):
                A[i][j] += f[i] * f[j]
    w1, w2, b = solve(A, r)
    return {"w1": w1, "w2": w2, "b": b}


def poly_fit(pts, degree: int) -> list[float]:
    """Polynomial least squares on raw x, coefs[k] multiplying x^k, as linear.js's polyFit."""
    m =degree + 1
    A = [[0.0] * m for _ in range(m)]
    r = [0.0] * m
    for p in pts:
        pw = [1.0]
        for k in range(1, 2 * m):
            pw.append(pw[k - 1] * p["x"])
        for i in range(m):
            r[i] += pw[i] * p["y"]
            for j in range(m):
                A[i][j] += pw[i + j]
    return solve(A, r)


def poly_predict(coefs, x: float) -> float:
    """The polynomial's value at x (Horner), as linear.js's polyPredict."""
    y = 0.0
    for c in reversed(coefs):
        y = y * x + c
    return y


def fit_stats(pts, f) -> dict:
    """SSE, MSE, MAE and R² of fit f(x, z) over pts, as regression.js's fitStats."""
    n = len(pts)
    ybar = _mean(p["y"] for p in pts)
    sse = sum((p["y"] - f(p["x"], p["z"])) ** 2 for p in pts)
    sst = sum((p["y"] - ybar) ** 2 for p in pts)
    mae = _mean(abs(p["y"] - f(p["x"], p["z"])) for p in pts)
    return {"n": n, "sse": sse, "mse": sse / n, "mae": mae, "r2": 1 - sse / sst if sst > 0 else float("nan")}


def line_fn(m):
    """A line model {w, b} as a function f(x, z=0) for fit_stats."""
    return lambda x, z=0.0: m["w"] * x + m["b"]


def plane_fn(m):
    """A plane model {w1, w2, b} as a function f(x, z) for fit_stats."""
    return lambda x, z: m["w1"] * x + m["w2"] * z + m["b"]


def lad_line(pts) -> dict:
    """Least-absolute-deviations line: some optimum passes through two data points."""
    best = None
    for i, p in enumerate(pts):
        for q in pts[i + 1:]:
            if abs(q["x"] - p["x"]) < 1e-12:
                continue
            w = (q["y"] - p["y"]) / (q["x"] - p["x"])
            b = p["y"] - w * p["x"]
            cost = sum(abs(r["y"] - (w * r["x"] + b)) for r in pts)
            if best is None or cost < best[0] - 1e-12:
                best = (cost, w, b)
    return {"w": best[1], "b": best[2]}


def game_numbers(requests: list[dict], game_root: str | Path = GAME_ROOT) -> list[dict]:
    """The game's own JS (via node) for requests {dataset,n,noise,seed} or {points}, plus optional fit line|plane|poly."""
    out = subprocess.run(["node", str(HERE / "workshop_numbers.mjs"), str(Path(game_root).expanduser()), json.dumps(requests)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)
