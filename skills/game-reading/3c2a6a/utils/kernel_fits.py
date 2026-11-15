"""Penalised two-class fits in a kernel or feature basis (ESL 12.25, 12.29): the hinge-loss SVM as a QP and kernel logistic regression by Newton's method."""
import numpy as np
from scipy.optimize import minimize


def hinge_qp(B, P, y, lam):
    """min over (beta0, c) of sum [1 - y_i (beta0 + B_i c)]_+ + lam/2 c^T P c (B = H, P = I for 12.25; B = P = K for 12.29), labels y in {-1, 1}, by SLSQP with slacks: (beta0, c, criterion value)."""
    n, m = B.shape
    A = np.hstack([y[:, None], y[:, None] * B, np.eye(n)])

    def J(t):
        c = t[1:1 + m]
        return t[1 + m:].sum() + lam / 2 * c @ P @ c

    def dJ(t):
        return np.concatenate([[0.0], lam * P @ t[1:1 + m], np.ones(n)])

    cons = [{"type": "ineq", "fun": lambda t: A @ t - 1, "jac": lambda t: A},
            {"type": "ineq", "fun": lambda t: t[1 + m:], "jac": lambda t: np.hstack([np.zeros((n, 1 + m)), np.eye(n)])}]
    t0 = np.concatenate([[0.0], np.zeros(m), np.full(n, 2.0)])
    r = minimize(J, t0, jac=dJ, constraints=cons, method="SLSQP", options={"ftol": 1e-14, "maxiter": 3000})
    b0, c = r.x[0], r.x[1:1 + m]
    return b0, c, float(np.maximum(0.0, 1 - y * (b0 + B @ c)).sum() + lam / 2 * c @ P @ c)


def kernel_logistic(K, y, lam, iters=100, tol=1e-12):
    """Kernel logistic regression: min over (beta0, alpha) of sum log(1 + exp(-y_i f_i)) + lam/2 alpha^T K alpha, f = beta0 + K alpha, labels y in {-1, 1}, by Newton's method in K's eigen-coordinates: (beta0, alpha, f at the training points)."""
    d, U = np.linalg.eigh(K)
    keep = d > d.max() * 1e-12
    Phi = U[:, keep] * np.sqrt(d[keep])
    A = np.hstack([np.ones((len(y), 1)), Phi])
    t = np.zeros(A.shape[1])
    pen = np.full(A.shape[1], lam)
    pen[0] = 0.0
    for _ in range(iters):
        s = 1 / (1 + np.exp(y * (A @ t)))
        g = -A.T @ (y * s) + pen * t
        H = (A * (s * (1 - s))[:, None]).T @ A + np.diag(pen)
        step = np.linalg.solve(H, g)
        t -= step
        if np.abs(step).max() < tol:
            break
    alpha = U[:, keep] @ (t[1:] / np.sqrt(d[keep]))
    return t[0], alpha, A @ t
