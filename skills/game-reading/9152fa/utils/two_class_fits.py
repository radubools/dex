"""Two-class LDA and logistic regression fits (ESL Sections 4.3 and 4.4.1), numpy only, returning boundary coefficients."""
import numpy as np


def lda_fit(X, g):
    """Two-class LDA on rows X, labels g in {0, 1}: (alpha, alpha0, (mu0, mu1, Sigma)) of (4.33), class 1 vs 0, Sigma pooled over N - 2."""
    X = np.asarray(X, float)
    X = X[:, None] if X.ndim == 1 else X
    g = np.asarray(g)
    N = len(X)
    m0, m1 = X[g == 0].mean(0), X[g == 1].mean(0)
    W = ((X[g == 0] - m0).T @ (X[g == 0] - m0) + (X[g == 1] - m1).T @ (X[g == 1] - m1)) / (N - 2)
    a = np.linalg.solve(W, m1 - m0)
    a0 = np.log(np.mean(g == 1) / np.mean(g == 0)) - 0.5 * (m1 + m0) @ a
    return a, a0, (m0, m1, W)


def logistic_fit(X, g, iters=60, tol=1e-10):
    """Two-class logistic regression by Newton-Raphson (IRLS) on rows X, labels g in {0, 1}: (beta, beta0); stops after `iters` steps on separable data."""
    X = np.asarray(X, float)
    X = X[:, None] if X.ndim == 1 else X
    A = np.c_[np.ones(len(X)), X]
    b = np.zeros(A.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(A @ b, -700, 700)))
        H = A.T @ (A * (p * (1 - p))[:, None])
        step = np.linalg.solve(H + 1e-10 * np.eye(len(b)), A.T @ (g - p))
        b = b + step
        if np.abs(step).max() < tol:
            break
    return b[1:], b[0]
