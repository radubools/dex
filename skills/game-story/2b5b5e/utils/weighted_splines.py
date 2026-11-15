"""Weighted cubic smoothing-spline terms for backfitting (ESL 9.1): lambda fixed by nominal df, refitted with any observation weights."""
import numpy as np

from .linear_smoothers import spline_penalty_K


class SplineTerm:
    """One input's cubic smoothing spline on its distinct values, lambda set once so trace[S(lambda)] - 1 = df with unit weights."""

    def __init__(self, x, df=4.0, *, lo=-12.0, hi=12.0, iters=80):
        self.u, self.inv = np.unique(np.asarray(x, float), return_inverse=True)
        self.m = len(self.u)
        self.counts = np.bincount(self.inv, minlength=self.m).astype(float)
        if self.m <= 2 + df:  # too few distinct values for that many df: fit each value freely
            self.K = None
            self.lam = 0.0
            return
        self.K = spline_penalty_K(self.u)
        for _ in range(iters):
            mid = (lo + hi) / 2
            if self.trace(np.exp(mid)) > df + 1:
                lo = mid
            else:
                hi = mid
        self.lam = float(np.exp((lo + hi) / 2))

    def trace(self, lam):
        """trace of the smoother on the N points (counts as weights) for a given lambda."""
        C = np.diag(self.counts)
        return float(np.trace(np.linalg.solve(C + lam * self.K, C)))

    def smooth(self, r, w):
        """(fit at the N points, fit at the distinct values u) of r on x with weights w, centred to weighted mean 0."""
        r, w = np.asarray(r, float), np.asarray(w, float)
        W = np.bincount(self.inv, weights=w, minlength=self.m)
        S = np.bincount(self.inv, weights=w * r, minlength=self.m)
        g = S / np.maximum(W, 1e-12) if self.K is None else np.linalg.solve(np.diag(W) + self.lam * self.K, S)
        f = g[self.inv]
        mean = (w * f).sum() / w.sum()
        return f - mean, g - mean
