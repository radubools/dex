"""The exact linear soft-margin SVM (ESL 12.2) by its Wolfe dual: a reference to check the game's Pegasos fit against."""
import numpy as np


def soft_margin_dual(P, y, C, *, b0_grid=(-20.0, 20.0, 400001)):
    """Maximise the dual (12.13) over 0 <= alpha_i <= C with sum alpha_i y_i = 0: (alpha, beta, beta0); y in {-1, +1}.

    beta0 is the mean of y_i - x_i^T beta over the margin points (0 < alpha_i < C), as ESL averages them.
    When there are none, beta0 is the value on `b0_grid` (lo, hi, count) with the least summed hinge.
    """
    from scipy.optimize import minimize
    P, y = np.asarray(P, float), np.asarray(y, float)
    n = len(P)
    G = (y[:, None] * P) @ (y[:, None] * P).T
    r = minimize(lambda a: -(a.sum() - 0.5 * a @ G @ a), np.full(n, min(C, 1) / 2),
                 jac=lambda a: -(np.ones(n) - G @ a), bounds=[(0, C)] * n,
                 constraints=[{"type": "eq", "fun": lambda a: a @ y, "jac": lambda a: y}],
                 method="SLSQP", options={"ftol": 1e-15, "maxiter": 2000})
    a = r.x
    beta = (a * y) @ P
    on = (a > 1e-6 * max(C, 1)) & (a < C * (1 - 1e-6))
    f0 = P @ beta
    if on.any():
        return a, beta, float(np.mean(y[on] - f0[on]))
    bs = np.linspace(*b0_grid)
    hinge = [np.maximum(0, 1 - y * (f0 + b)).sum() for b in bs]
    return a, beta, float(bs[int(np.argmin(hinge))])
