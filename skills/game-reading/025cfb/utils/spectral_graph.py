"""Similarity graphs for spectral methods, numpy only: ESL's three-ring toy data, radial similarities, k-nearest-neighbour graphs and the graph Laplacian."""
import numpy as np


def three_rings(seed=7, n=150, radii=(1.0, 2.8, 5.0), sd=0.25):
    """ESL Figure 14.29's toy data: n points per ring, uniform angle, radius from `radii`, Gaussian noise sd; returns (X (N, 2), ring index per point)."""
    rng = np.random.default_rng(seed)
    X, y = [], []
    for g, r in enumerate(radii):
        t = rng.uniform(0, 2 * np.pi, n)
        X.append(np.c_[r * np.cos(t), r * np.sin(t)] + rng.normal(0, sd, (n, 2)))
        y.append(np.full(n, g))
    return np.vstack(X), np.concatenate(y)


def sqdist(X):
    """Squared Euclidean distances between the rows of X, as an N x N matrix."""
    s = (X ** 2).sum(1)
    return np.maximum(s[:, None] + s[None, :] - 2 * X @ X.T, 0)


def similarity(X, c=2.0):
    """Radial-kernel gram matrix s_ii' = exp(-d_ii'^2 / c) (ESL 14.67)."""
    return np.exp(-sqdist(X) / c)


def knn_pairs(X, k):
    """Boolean N x N: True where i is among the k nearest neighbours of i' or vice versa (ESL's symmetric set N_K)."""
    D = sqdist(X)
    np.fill_diagonal(D, np.inf)
    nn = np.argsort(D, axis=1)[:, :k]
    A = np.zeros(D.shape, bool)
    A[np.repeat(np.arange(len(X)), k), nn.ravel()] = True
    return A | A.T


def knn_weights(X, k=10, c=2.0):
    """Adjacency matrix W: radial similarities kept on the k-nearest-neighbour pairs, zero elsewhere."""
    return np.where(knn_pairs(X, k), similarity(X, c), 0.0)


def laplacian(W):
    """Unnormalized graph Laplacian L = G - W, G the diagonal of degrees (ESL 14.63)."""
    return np.diag(W.sum(1)) - W
