"""Natural cubic spline bases (ESL (5.4)-(5.5)) as numpy matrices, for spline and functional-data lessons."""
import numpy as np


def ncs_basis(x, knots):
    """The N x K natural cubic spline basis at x for K knots: N1 = 1, N2 = x, N_{k+2} = d_k - d_{K-1} (ESL (5.4)-(5.5))."""
    x = np.asarray(x, float)
    knots = np.asarray(knots, float)
    K = len(knots)
    xk = knots[-1]

    def d(k):
        return (np.maximum(x - knots[k], 0) ** 3 - np.maximum(x - xk, 0) ** 3) / (xk - knots[k])

    cols = [np.ones_like(x), x] + [d(k) - d(K - 2) for k in range(K - 2)]
    return np.column_stack(cols)
