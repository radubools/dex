"""Least squares with weights on the simplex (nonnegative, summing to 1): the quadratic program of constrained stacking (ESL Section 8.8)."""
import numpy as np
from scipy.optimize import minimize


def simplex_weights(F, y):
    """Weights w >= 0 with sum 1 minimizing ||y - F w||^2, for F (N x M) and y (N,); scipy SLSQP from equal weights."""
    F = np.asarray(F, dtype=float)
    y = np.asarray(y, dtype=float)
    M = F.shape[1]
    res = minimize(lambda w: np.sum((y - F @ w) ** 2), np.full(M, 1 / M),
                   jac=lambda w: -2 * F.T @ (y - F @ w), method="SLSQP",
                   bounds=[(0, 1)] * M, constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
                   options={"ftol": 1e-14, "maxiter": 1000})
    w = np.clip(res.x, 0, None)
    return w / w.sum()
