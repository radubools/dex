"""Optimal scoring and flexible discriminant analysis (ESL 12.5.1) for a linear-basis regression, as numpy arrays."""
import numpy as np


def optimal_scoring(H, g, K=None):
    """FDA's steps 1-2 on basis matrix H (N x M, constant column included) and labels g in 0..K-1: {theta, mu, B, eta, cent, A, D}.

    A = Y^T S Y / N (S = H (H^T H)^-1 H^T, never formed) and D = Y^T Y / N = D_pi. The generalized
    eigenproblem A theta = mu D theta is solved with Theta^T D Theta = I, columns ordered by mu, largest
    first: column 0 is the trivial constant score (mu = 1), the others the discriminant scorings. Each
    column's sign is chosen so the last class scores positive. B = (H^T H)^-1 H^T Y Theta are the
    regression coefficients of each scoring, eta = H B the fitted discriminant functions (one column per
    scoring) and cent[k] the mean of eta over class k. With H = [1, X] this is LDA (Exercise 12.8).
    """
    H = np.asarray(H, float)
    g = np.asarray(g, int)
    K = int(g.max()) + 1 if K is None else K
    N = len(H)
    Y = np.eye(K)[g]
    HtH = H.T @ H
    HY = H.T @ Y
    A = HY.T @ np.linalg.solve(HtH, HY) / N
    D = Y.T @ Y / N
    Dm = np.diag(1 / np.sqrt(np.diag(D)))
    vals, vecs = np.linalg.eigh(Dm @ A @ Dm)
    order = np.argsort(vals)[::-1]
    Theta = Dm @ vecs[:, order]
    Theta = Theta * np.where(Theta[-1] < 0, -1.0, 1.0)
    B = np.linalg.solve(HtH, HY @ Theta)
    eta = H @ B
    cent = np.array([eta[g == k].mean(axis=0) for k in range(K)])
    return {"theta": Theta, "mu": vals[order], "B": B, "eta": eta, "cent": cent, "A": A, "D": D}
