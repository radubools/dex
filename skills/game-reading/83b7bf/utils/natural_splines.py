"""Natural cubic spline bases (ESL 5.4-5.5) evaluated at given points, plain and orthonormalised, with their smoothing-spline penalty matrices, numpy only."""
import numpy as np


def ns_basis(x, knots):
    """Natural cubic spline basis at points x for the given sorted knots (boundary knots first and last): (len(x), len(knots))."""
    x = np.asarray(x, float)
    xi, K = np.asarray(knots, float), len(knots)

    def d(k):
        return (np.maximum(x - xi[k], 0) ** 3 - np.maximum(x - xi[-1], 0) ** 3) / (xi[-1] - xi[k])

    cols = [np.ones_like(x), x] + [d(k) - d(K - 2) for k in range(K - 2)]
    return np.stack(cols, 1)


def orthonormal_ns_basis(x, knots):
    """An orthonormal basis (Q of a QR decomposition) of the same column space as ns_basis(x, knots)."""
    Q, _ = np.linalg.qr(ns_basis(x, knots))
    return Q


def ns_basis_d2(x, knots):
    """Second derivatives of the ns_basis(x, knots) columns at points x (piecewise linear): (len(x), len(knots))."""
    x = np.asarray(x, float)
    xi, K = np.asarray(knots, float), len(knots)

    def d2(k):
        return 6 * (np.maximum(x - xi[k], 0) - np.maximum(x - xi[-1], 0)) / (xi[-1] - xi[k])

    cols = [np.zeros_like(x), np.zeros_like(x)] + [d2(k) - d2(K - 2) for k in range(K - 2)]
    return np.stack(cols, 1)


def ns_penalty(knots):
    """The smoothing-spline penalty matrix Omega_jk = int N_j''(t) N_k''(t) dt of ns_basis (ESL 5.11), exact."""
    xi = np.asarray(knots, float)
    O = np.zeros((len(xi), len(xi)))
    for a, b in zip(xi, xi[1:]):
        # the integrand is quadratic on each knot interval, so Simpson's rule is exact there
        t = np.array([a, (a + b) / 2, b])
        D = ns_basis_d2(t, xi)
        wts = np.array([1, 4, 1]) * (b - a) / 6
        O += D.T @ (wts[:, None] * D)
    return O


def reinsch_K(x):
    """The Reinsch penalty matrix K = Q R^-1 Q^T (ESL 5.17-5.18) for distinct sorted x: f^T K f = int g''^2 for the natural interpolant g."""
    x = np.asarray(x, float)
    n, h = len(x), np.diff(x)
    Q, R = np.zeros((n, n - 2)), np.zeros((n - 2, n - 2))
    for j in range(n - 2):
        Q[j, j], Q[j + 1, j], Q[j + 2, j] = 1 / h[j], -1 / h[j] - 1 / h[j + 1], 1 / h[j + 1]
        R[j, j] = (h[j] + h[j + 1]) / 3
        if j < n - 3:
            R[j, j + 1] = R[j + 1, j] = h[j + 1] / 6
    return Q @ np.linalg.solve(R, Q.T)
