"""The 1-D fused lasso signal approximator (ESL (18.22)-(18.24)) solved exactly by its fusion path, with a generic-solver check."""
import numpy as np
from scipy.optimize import minimize

# Criterion, as the book writes it (no 1/2, beta_0 = 0):
#     sum_i (y_i - b_i)^2 + l1 sum_i |b_i| + l2 sum_i w_i |b_{i+1} - b_i|   (w_i = 1, or 1/(t_{i+1} - t_i) for (18.23)).
# With l1 = 0 the path in l2 is piecewise linear and only fuses neighbours (Friedman et al. 2007): a fused group G of
# m points has c = mean_G(y) - l2 (w_l s_l + w_r s_r) / (2m), s = sign(c - neighbour). l1 then soft-thresholds by l1/2.


def soft(v, t):
    """Soft thresholding sign(v)(|v| - t)_+, elementwise."""
    return np.sign(v) * np.maximum(np.abs(v) - t, 0)


def flsa_path(y, w=None):
    """Exact fusion events of the l1 = 0 path: [(l2, groups)] where groups = [(start, end, value at that l2, slope)]."""
    y = np.asarray(y, float)
    n = len(y)
    w = np.ones(n - 1) if w is None else np.asarray(w, float)
    groups = [[i, i] for i in range(n)]
    lam = 0.0
    vals = list(y)
    events = []

    def slopes(vals):
        out = []
        for k, (a, b) in enumerate(groups):
            m = b - a + 1
            s = 0.0
            if k > 0:
                s += w[a - 1] * np.sign(vals[k] - vals[k - 1])
            if k < len(groups) - 1:
                s += w[b] * np.sign(vals[k] - vals[k + 1])
            out.append(-s / (2 * m))
        return out

    while len(groups) > 1:
        sl = slopes(vals)
        events.append((lam, [(a, b, vals[k], sl[k]) for k, (a, b) in enumerate(groups)]))
        best, at = np.inf, None
        for k in range(len(groups) - 1):
            gap, rate = vals[k] - vals[k + 1], sl[k + 1] - sl[k]
            if gap * rate > 1e-15:   # closing
                d = gap / rate
                if d < best:
                    best, at = d, k
        if at is None:
            break
        vals = [v + best * s for v, s in zip(vals, sl)]
        lam += best
        a, _ = groups[at]
        _, b = groups[at + 1]
        mv = (vals[at] * (groups[at][1] - groups[at][0] + 1) + vals[at + 1] * (groups[at + 1][1] - groups[at + 1][0] + 1)) / (b - a + 1)
        groups[at:at + 2] = [[a, b]]
        vals[at:at + 2] = [mv]
    events.append((lam, [(a, b, vals[k], 0.0) for k, (a, b) in enumerate(groups)]))
    return events


def flsa(y, l1, l2, w=None):
    """The exact solution of (18.24) at (l1, l2): the l1 = 0 path at l2, soft-thresholded by l1/2."""
    events = flsa_path(y, w)
    n = len(y)
    out = np.empty(n)
    for k, (lam, groups) in enumerate(events):
        nxt = events[k + 1][0] if k + 1 < len(events) else np.inf
        if lam <= l2 < nxt or k == len(events) - 1:
            for a, b, v, s in groups:
                out[a:b + 1] = v + (l2 - lam) * s
            break
    return soft(out, l1 / 2)


def objective(b, y, l1, l2, w=None):
    """The value of the criterion at fit b (w: optional per-jump weights, e.g. 1/gap for divided differences)."""
    b, y = np.asarray(b, float), np.asarray(y, float)
    w = np.ones(len(y) - 1) if w is None else np.asarray(w, float)
    return ((y - b) ** 2).sum() + l1 * np.abs(b).sum() + l2 * (w * np.abs(np.diff(b))).sum()


def solve_generic(y, l1, l2, w=None):
    """The same criterion solved by SLSQP as a smooth constrained problem (b, |b| <= u, |Db| <= v): a reference to check flsa against."""
    y = np.asarray(y, float)
    n = len(y)
    w = np.ones(n - 1) if w is None else np.asarray(w, float)
    D = np.diff(np.eye(n), axis=0)

    def f(z):
        b, u, v = z[:n], z[n:2 * n], z[2 * n:]
        return ((y - b) ** 2).sum() + l1 * u.sum() + l2 * (w * v).sum()

    cons = [{"type": "ineq", "fun": lambda z: z[n:2 * n] - z[:n]},
            {"type": "ineq", "fun": lambda z: z[n:2 * n] + z[:n]},
            {"type": "ineq", "fun": lambda z: z[2 * n:] - D @ z[:n]},
            {"type": "ineq", "fun": lambda z: z[2 * n:] + D @ z[:n]}]
    z0 = np.concatenate([y, np.abs(y) + 0.1, np.abs(D @ y) + 0.1])
    r = minimize(f, z0, constraints=cons, method="SLSQP", options={"ftol": 1e-14, "maxiter": 2000})
    return r.x[:n]


def segments(b, tol=1e-9):
    """How many constant pieces a fit has: 1 + the jumps larger than tol."""
    return 1 + int((np.abs(np.diff(b)) > tol).sum())
