"""Lines and ellipses on the data floor as evenly spaced [x, z] points, for drawing them as rows of spawned markers."""
import numpy as np


def line_dots(n, c, gap=0.3, keep=None, limit=4.85, digits=2, reach=15):
    """[x, z] points of the line n . p = c inside the floor (|x|, |z| <= limit), `gap` apart from s = -reach along it; keep(p) filters them."""
    n = np.asarray(n, float)
    u = np.array([-n[1], n[0]]) / np.linalg.norm(n)
    x0 = n * c / (n @ n)
    out = []
    for s in np.arange(-reach, reach, gap):
        p = x0 + s * u
        if abs(p[0]) <= limit and abs(p[1]) <= limit and (keep is None or keep(p)):
            out.append([round(float(p[0]), digits), round(float(p[1]), digits)])
    return out


def ellipse_dots(mu, S, c=5.991464547107979, gap=0.32, limit=4.85, digits=2, samples=4000):
    """[x, z] points of the ellipse (p - mu)^T S^-1 (p - mu) = c (default: a Gaussian's 95% contour), about `gap` apart, inside the floor."""
    mu = np.asarray(mu, float)
    vals, vecs = np.linalg.eigh(np.asarray(S, float))
    t = np.linspace(0, 2 * np.pi, samples, endpoint=False)
    pts = mu + (vecs @ (np.sqrt(c * vals)[:, None] * np.stack([np.cos(t), np.sin(t)]))).T
    out = [pts[0]]
    for p in pts[1:]:
        if np.linalg.norm(p - out[-1]) >= gap and np.linalg.norm(p - out[0]) >= gap * 0.8:
            out.append(p)
    return [[round(float(a), digits), round(float(b), digits)] for a, b in out if abs(a) <= limit and abs(b) <= limit]
