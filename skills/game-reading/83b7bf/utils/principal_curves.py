"""Principal curves and surfaces by the alternating algorithm (ESL 14.62), numpy only.

Step (a) is a local linear Gaussian-kernel smoother of each coordinate against lambda;
step (b) projects every point onto the fitted curve (a polyline through its fitted values)
or, for a surface, onto a fine grid of the fitted surface.
"""
import numpy as np


def local_linear(lam, y, at, h):
    """Local linear Gaussian-kernel smooth of y (n,) or (n, p) on lam (n, d), evaluated at `at` (m, d)."""
    lam = np.atleast_2d(lam.T).T
    at = np.atleast_2d(at.T).T
    out = []
    for a in at:
        d = lam - a
        w = np.exp(-0.5 * (d ** 2).sum(1) / h ** 2)
        A = np.hstack([np.ones((len(lam), 1)), d])
        AtW = A.T * w
        beta = np.linalg.solve(AtW @ A + 1e-9 * np.eye(A.shape[1]), AtW @ y)
        out.append(beta[0])
    return np.array(out)


def project_polyline(X, P):
    """Closest point on the polyline P (m, p) for each row of X: (arc length along P, the point, squared distance)."""
    seg = P[1:] - P[:-1]
    L = np.r_[0, np.cumsum(np.linalg.norm(seg, axis=1))]
    lam, pts, d2 = [], [], []
    for x in X:
        t = np.clip(((x - P[:-1]) * seg).sum(1) / np.maximum((seg ** 2).sum(1), 1e-12), 0, 1)
        q = P[:-1] + t[:, None] * seg
        dd = ((x - q) ** 2).sum(1)
        k = int(np.argmin(dd))
        lam.append(L[k] + t[k] * np.linalg.norm(seg[k]))
        pts.append(q[k])
        d2.append(dd[k])
    return np.array(lam), np.array(pts), np.array(d2)


def first_pc(X):
    """Mean and leading right singular vector of X."""
    mu = X.mean(0)
    v = np.linalg.svd(X - mu, full_matrices=False)[2][0]
    return mu, v


def principal_curve(X, h, iters=10, grid=200):
    """Fit a principal curve from the first principal component line.

    h is the kernel width as a fraction of the current lambda range. Returns a list of
    passes, each {lam, curve (grid, p), proj (n, p), d2 (total squared distance)};
    pass 0 is the principal component line.
    """
    mu, v = first_pc(X)
    lam = (X - mu) @ v
    proj = mu + lam[:, None] * v
    t = np.linspace(lam.min(), lam.max(), grid)
    hist = [dict(lam=lam - lam.min(), curve=mu + t[:, None] * v, proj=proj,
                 d2=float(((X - proj) ** 2).sum()))]
    for _ in range(iters):
        t = np.linspace(lam.min(), lam.max(), grid)
        curve = local_linear(lam, X, t, h * (lam.max() - lam.min()))      # step (a)
        lam, proj, d2 = project_polyline(X, curve)                          # step (b), lambda = arc length
        hist.append(dict(lam=lam, curve=curve, proj=proj, d2=float(d2.sum())))
    return hist


def principal_surface(X, h, iters=8, grid=41):
    """Fit a two-dimensional principal surface from the first two principal components.

    h is the kernel width in lambda units (lambda scaled to the PC plane's units). Returns
    {lam (n, 2), surface (grid, grid, p), axes (g1, g2), d2 per pass}.
    """
    mu = X.mean(0)
    V = np.linalg.svd(X - mu, full_matrices=False)[2][:2].T
    lam = (X - mu) @ V
    d2s = [float((((X - mu) - lam @ V.T) ** 2).sum())]
    for _ in range(iters):
        g1 = np.linspace(lam[:, 0].min(), lam[:, 0].max(), grid)
        g2 = np.linspace(lam[:, 1].min(), lam[:, 1].max(), grid)
        G = np.array([(a, b) for a in g1 for b in g2])
        S = local_linear(lam, X, G, h)                                       # step (a)
        d = ((X[:, None, :] - S[None, :, :]) ** 2).sum(2)                    # step (b) on the grid
        k = d.argmin(1)
        lam = G[k]
        d2s.append(float(d[np.arange(len(X)), k].sum()))
    return dict(lam=lam, surface=S.reshape(grid, grid, -1), axes=(g1, g2), d2=d2s)


def linear_smoother_directions(X, v0, iters):
    """(14.62) with a least-squares straight-line smoother on centred X: the direction after each pass."""
    X = X - X.mean(0)
    v = v0 / np.linalg.norm(v0)
    out = [v]
    for _ in range(iters):
        lam = X @ v                          # step (b): projection onto the current line
        b = X.T @ lam / (lam @ lam)          # step (a): slope of each coordinate on lambda
        v = b / np.linalg.norm(b)
        out.append(v)
    return out


def batch_som_1d(X, K, passes=30, r0=None, seed=0):
    """A one-dimensional batch SOM (ESL 14.48): K prototypes on a chain, Gaussian neighbourhood shrinking to 0.5."""
    mu, v = first_pc(X)
    s = (X - mu) @ v
    m = mu + np.linspace(s.min(), s.max(), K)[:, None] * v
    r0 = r0 or K / 3
    for it in range(passes):
        r = r0 + (0.5 - r0) * it / max(passes - 1, 1)
        j = ((X[:, None, :] - m[None]) ** 2).sum(2).argmin(1)
        W = np.exp(-0.5 * (np.arange(K)[:, None] - j[None, :]) ** 2 / r ** 2)   # w_k for prototype j, point k
        m = (W @ X) / W.sum(1, keepdims=True)
    return m
