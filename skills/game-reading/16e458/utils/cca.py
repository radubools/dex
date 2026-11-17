"""Canonical correlation analysis by the SVD of ESL (3.87) and reduced-rank regression (3.69), numpy only."""
import numpy as np


def msqrt(A, power):
    """A symmetric positive-definite matrix raised to a real power (0.5, -0.5, ...) through its eigen decomposition."""
    w, V = np.linalg.eigh(A)
    return (V * w ** power) @ V.T


def cca(X, Y):
    """CCA of centred X (N x p) and Y (N x K) by (3.87): (U (K x M), V (p x M), correlations c, U* the left singular vectors).

    Columns of U and V are the canonical vectors u_m, v_m with u^T Y^T Y u = v^T X^T X v = 1; M = min(K, p).
    """
    M = msqrt(Y.T @ Y, -0.5) @ (Y.T @ X) @ msqrt(X.T @ X, -0.5)
    Us, c, Vst = np.linalg.svd(M, full_matrices=False)
    return msqrt(Y.T @ Y, -0.5) @ Us, msqrt(X.T @ X, -0.5) @ Vst.T, c, Us


def reduced_rank(X, Y, m):
    """Rank-m reduced-rank regression coefficients (3.69), B-hat U_m U_m^- with Sigma = Y^T Y / N, for centred X and Y."""
    U, _, _, Us = cca(X, Y)
    B = np.linalg.solve(X.T @ X, X.T @ Y)
    return B @ U[:, :m] @ (Us[:, :m].T @ msqrt(Y.T @ Y, 0.5))
