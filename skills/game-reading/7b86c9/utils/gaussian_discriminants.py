"""Gaussian discriminant analysis of ESL Section 4.3, numpy only: class estimates, QDA scores (4.12), RDA covariances (4.13)-(4.14), decision-boundary contours."""
import numpy as np

from .contours import chain, marching_segments_xy


def gauss_fit(X, g, K):
    """Estimates of Section 4.3 from rows X, labels g in 0..K-1: (priors N_k/N, means (K, p), class covariances over N_k - 1, pooled covariance over N - K)."""
    N = len(X)
    pis = np.array([np.mean(g == k) for k in range(K)])
    mus = np.array([X[g == k].mean(0) for k in range(K)])
    Sk = [np.cov(X[g == k].T) for k in range(K)]
    Sp = sum((X[g == k] - mus[k]).T @ (X[g == k] - mus[k]) for k in range(K)) / (N - K)
    return pis, mus, Sk, Sp


def qda_scores(X, pis, mus, Sigmas):
    """Quadratic discriminant functions (4.12) of the rows of X, one column per class, evaluated through each covariance's eigendecomposition (Section 4.3.2)."""
    out = []
    for pi, mu, Sg in zip(pis, mus, Sigmas):
        d, U = np.linalg.eigh(Sg)
        r = (X - mu) @ U
        out.append(-0.5 * np.log(d).sum() - 0.5 * (r ** 2 / d).sum(1) + np.log(pi))
    return np.stack(out, 1)


def rda_sigmas(Sk, Sp, alpha, gamma=1.0):
    """RDA covariances Sigma_k(alpha) (4.13), the pooled Sigma first shrunk by (4.14) to gamma Sigma + (1 - gamma) sigma^2 I with sigma^2 = trace(Sigma)/p."""
    p = Sp.shape[0]
    s2 = np.trace(Sp) / p
    Sg = gamma * Sp + (1 - gamma) * s2 * np.eye(p)
    return [alpha * S_ + (1 - alpha) * Sg for S_ in Sk]


def region_boundaries(score, xlim, ylim, n=301):
    """Polylines where the argmax of score(points (M, 2)) -> (M, K) changes over a grid on xlim x ylim, and (gx, gy, label grid) for painting regions."""
    gx, gy = np.linspace(*xlim, n), np.linspace(*ylim, n)
    XX, YY = np.meshgrid(gx, gy)
    D = score(np.column_stack([XX.ravel(), YY.ravel()])).reshape(n, n, -1)
    lab = D.argmax(-1)
    lines = []
    for k in range(D.shape[-1]):
        others = np.delete(D, k, axis=-1).max(-1)
        lines += chain(marching_segments_xy(D[..., k] - others, gx, gy))
    return lines, (gx, gy, lab)
