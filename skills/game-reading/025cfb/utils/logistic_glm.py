"""Unpenalized logistic regression by IRLS (ESL 4.26-4.28): coefficients, standard errors, Z scores and deviance, numpy only."""
import numpy as np


def irls(A, y, offset=0.0, tol=1e-12, max_iter=100):
    """Maximum-likelihood coefficients of logit p = offset + A beta by IRLS from beta = 0; returns (beta, fitted p)."""
    beta = np.zeros(A.shape[1])
    for _ in range(max_iter):
        p = 1 / (1 + np.exp(-(offset + A @ beta)))
        w = p * (1 - p)
        z = A @ beta + (y - p) / w                     # adjusted response (4.27), offset removed
        new = np.linalg.solve(A.T @ (w[:, None] * A), A.T @ (w * z))
        done = np.max(np.abs(new - beta)) < tol
        beta = new
        if done:
            break
    return beta, 1 / (1 + np.exp(-(offset + A @ beta)))


def deviance(y, p):
    """Residual deviance of fitted probabilities p for 0/1 outcomes y: -2 times the log-likelihood."""
    return float(-2 * np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit(A, y, offset=0.0, tol=1e-12, max_iter=100):
    """IRLS fit of design A (include a column of ones for an intercept): {beta, se, z, p, cov, dev}, se from (A^T W A)^-1."""
    beta, p = irls(A, y, offset, tol, max_iter)
    w = p * (1 - p)
    cov = np.linalg.inv(A.T @ (w[:, None] * A))
    se = np.sqrt(np.diag(cov))
    return {"beta": beta, "se": se, "z": beta / se, "p": p, "cov": cov, "dev": deviance(y, p)}
