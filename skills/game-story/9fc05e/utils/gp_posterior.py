"""The exact posterior of a 1-D RBF Gaussian process, as numbers and as llm-math plot.function formulas."""
import math

import numpy as np


def rbf(a, b, ell=1.0):
    """The squared-exponential kernel exp(-(a - b)^2 / (2 ell^2)), signal variance 1."""
    return math.exp(-((a - b) ** 2) / (2 * ell * ell))


class Posterior:
    """GP posterior (prior mean 0, RBF kernel of length-scale ell, noise variance s) after readings (xs, ys)."""

    def __init__(self, xs, ys, s=0.01, ell=1.0):
        self.xs = [float(x) for x in xs]
        self.ys = [float(y) for y in ys]
        self.ell = ell
        K = np.array([[rbf(a, b, ell) for b in self.xs] for a in self.xs])
        self.A = np.linalg.inv(K + s * np.eye(len(self.xs)))
        self.alpha = self.A @ np.array(self.ys, float)

    def kv(self, x):
        """K(x): the kernel between x and every reading."""
        return np.array([rbf(x, a, self.ell) for a in self.xs])

    def mean(self, x):
        """mu_n(x) = K(x)^T (K + s I)^-1 Y."""
        return float(self.kv(x) @ self.alpha)

    def var(self, x):
        """k_n(x, x) = 1 - K(x)^T (K + s I)^-1 K(x), clipped at 0."""
        k = self.kv(x)
        return max(float(1 - k @ self.A @ k), 0.0)

    def sd(self, x):
        """sigma_n(x), the posterior standard deviation."""
        return math.sqrt(self.var(x))

    def ucb(self, x, beta):
        """The upper confidence bound mu_n(x) + sqrt(beta) sigma_n(x)."""
        return self.mean(x) + math.sqrt(beta) * self.sd(x)

    def argmax_ucb(self, beta, grid):
        """(x, value) of the largest UCB over the candidates in `grid` (the first one on ties)."""
        vals = [self.ucb(x, beta) for x in grid]
        i = int(np.argmax(vals))
        return float(grid[i]), vals[i]

    def mean_fn(self, var="x", digits=5):
        """mu_n as a plot.function formula in `var`, its weights alpha baked in."""
        den = f"{2 * self.ell ** 2:g}"
        terms = [f"{a:+.{digits}f}*exp(-({var} - ({xi}))^2/{den})" for a, xi in zip(self.alpha, self.xs)]
        return " ".join(terms).lstrip("+")

    def var_fn(self, var="x", digits=5):
        """k_n(x, x) as a plot.function formula in `var` (one exp per pair of readings)."""
        den = f"{2 * self.ell ** 2:g}"
        n = len(self.xs)
        terms = []
        for i in range(n):
            for j in range(i, n):
                c = self.A[i, j] * (1 if i == j else 2)
                xi, xj = self.xs[i], self.xs[j]
                terms.append(f"{-c:+.{digits}f}*exp(-(({var} - ({xi}))^2 + ({var} - ({xj}))^2)/{den})")
        return "1 " + " ".join(terms)

    def sd_fn(self, var="x", digits=5):
        """sigma_n as a plot.function formula in `var`, clipped at 0 inside the root."""
        return f"sqrt(max({self.var_fn(var, digits)}, 0))"
