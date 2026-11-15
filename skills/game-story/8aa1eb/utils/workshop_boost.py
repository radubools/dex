"""Model Workshop's regression gradient boosting in Python (stumps on x; squared, absolute and Huber loss), line for line with src/ml/boosting.js and actions/boost.js."""
import statistics

SQ, AB, HU = "squared", "absolute", "huber"


def fit_stump(xs, rs):
    """boosting.js fitStump: (threshold, left mean, right mean, squared error) of the best x <= threshold split of rs."""
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    total, tq = sum(rs), sum(r * r for r in rs)
    n = len(idx)
    best = (float("inf"), total / n, 0.0, float("inf"))
    ls = lq = 0.0
    for k in range(n - 1):
        r = rs[idx[k]]
        ls += r
        lq += r * r
        a, b = xs[idx[k]], xs[idx[k + 1]]
        if a == b:
            continue
        nl, nr = k + 1, n - k - 1
        rsum = total - ls
        err = (lq - ls * ls / nl) + (tq - lq - rsum * rsum / nr)
        if err < best[3] - 1e-12:
            best = ((a + b) / 2, ls / nl, rsum / nr, err)
    return best


def sign(v):
    """-1, 0 or 1, as boost.js sgn."""
    return (v > 0) - (v < 0)


def pseudo(r, kind=SQ, delta=1.0):
    """boost.js pseudoResidual: -dL/dF at residual r (r, sign(r), or r clipped to +-delta)."""
    if kind == AB:
        return sign(r)
    if kind == HU:
        return r if abs(r) <= delta else delta * sign(r)
    return r


def point_loss(r, kind=SQ, delta=1.0):
    """boost.js pointLoss: r^2/2, |r|, or Huber (r^2/2 inside delta, delta(|r| - delta/2) outside)."""
    a = abs(r)
    if kind == AB:
        return a
    if kind == HU:
        return 0.5 * r * r if a <= delta else delta * (a - 0.5 * delta)
    return 0.5 * r * r


def leaf(rs, kind=SQ, delta=1.0):
    """boost.js leafValue: a leaf's constant for residuals rs (mean, median, or median plus mean clipped deviation)."""
    if not rs:
        return 0.0
    if kind == AB:
        return statistics.median(rs)
    if kind == HU:
        m = statistics.median(rs)
        return m + sum(sign(r - m) * min(delta, abs(r - m)) for r in rs) / len(rs)
    return sum(rs) / len(rs)


class Booster:
    """The game's booster on points (xs, ys): F0 the mean (squared) or median, then step() adds lr times one stump."""

    def __init__(self, xs, ys, kind=SQ, delta=1.0, lr=1.0):
        self.xs, self.ys, self.kind, self.delta, self.lr = xs, ys, kind, delta, lr
        self.base = sum(ys) / len(ys) if kind == SQ else statistics.median(ys)
        self.stumps = []

    def predict(self, x):
        """F(x): the base plus each stored (already shrunk) stump's side."""
        return self.base + sum(l if x <= t else r for t, l, r in self.stumps)

    def residuals(self):
        """y - F(x) at every point."""
        return [y - self.predict(x) for x, y in zip(self.xs, self.ys)]

    def pseudo(self):
        """-dL/dF at every point: what the next stump fits."""
        return [pseudo(r, self.kind, self.delta) for r in self.residuals()]

    def step(self):
        """Add one stump (boost.js lossStep): split on the pseudo-residuals, leaves under the loss, shrunk by lr; returns it."""
        r = self.residuals()
        t, l, rr, _ = fit_stump(self.xs, self.pseudo())
        if self.kind != SQ:
            l = leaf([v for x, v in zip(self.xs, r) if x <= t], self.kind, self.delta)
            rr = leaf([v for x, v in zip(self.xs, r) if x > t], self.kind, self.delta)
        self.stumps.append((t, self.lr * l, self.lr * rr))
        return self.stumps[-1]

    def mse(self):
        """Mean squared residual (the game's $model.loss.mse)."""
        r = self.residuals()
        return sum(v * v for v in r) / len(r)

    def loss(self):
        """Mean loss per point under the booster's loss."""
        r = self.residuals()
        return sum(point_loss(v, self.kind, self.delta) for v in r) / len(r)
