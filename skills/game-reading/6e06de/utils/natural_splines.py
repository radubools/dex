"""Natural cubic spline bases (ESL 5.4-5.5) evaluated at given points, plain and orthonormalised, numpy only."""
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
