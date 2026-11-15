"""Marks for a figure_panel.Panel: lines clipped to the axes box, dashed segments, Gaussian contour ellipses, shape markers."""
import numpy as np

INK = "#1F2328"


def clip_line(p, v, xlim, ylim):
    """The part of the line p + t v inside the box xlim x ylim, as two points (arrays), or None if it misses."""
    p, v = np.asarray(p, float), np.asarray(v, float)
    lo, hi = -np.inf, np.inf
    for k, (a, b) in enumerate((xlim, ylim)):
        if abs(v[k]) < 1e-12:
            if not a <= p[k] <= b:
                return None
            continue
        t0, t1 = (a - p[k]) / v[k], (b - p[k]) / v[k]
        lo, hi = max(lo, min(t0, t1)), min(hi, max(t0, t1))
    if lo >= hi:
        return None
    return p + lo * v, p + hi * v


def segment(panel, a, b, color, width=2.0, dash=None):
    """A straight segment between data points a and b on `panel`, dashed when dash = (on, off) in final pixels."""
    s = panel.s
    pa, pb = np.array(panel.px(*a)), np.array(panel.px(*b))
    w = max(1, int(width * s))
    if not dash:
        panel.d.line([tuple(pa), tuple(pb)], fill=color, width=w)
        return
    on, off = dash[0] * s, dash[1] * s
    length = np.linalg.norm(pb - pa)
    if length == 0:
        return
    u = (pb - pa) / length
    t = 0.0
    while t < length:
        e = min(t + on, length)
        panel.d.line([tuple(pa + t * u), tuple(pa + e * u)], fill=color, width=w)
        t = e + off


def gaussian_contour(mu, sigma, c2, n=200):
    """n points (rows) on the ellipse (x - mu)^T sigma^-1 (x - mu) = c2; c2 = 5.991 encloses 95% of a 2-D Gaussian."""
    mu = np.asarray(mu, float)
    w, V = np.linalg.eigh(np.asarray(sigma, float))
    t = np.linspace(0, 2 * np.pi, n)
    circ = np.stack([np.cos(t), np.sin(t)])
    return (mu[:, None] + V @ (np.sqrt(w * c2)[:, None] * circ)).T


def marker(panel, x, y, shape, color, r=5.0, edge=INK):
    """A filled class marker at data point (x, y): shape 'circle', 'square' or 'triangle', radius r in final pixels."""
    s = panel.s
    px, py = panel.px(x, y)
    r *= s
    if shape == "circle":
        panel.d.ellipse([px - r, py - r, px + r, py + r], fill=color, outline=edge, width=s)
    elif shape == "square":
        r *= 0.88
        panel.d.rectangle([px - r, py - r, px + r, py + r], fill=color, outline=edge, width=s)
    elif shape == "triangle":
        panel.d.polygon([(px, py - 1.15 * r), (px - r, py + 0.75 * r), (px + r, py + 0.75 * r)], fill=color, outline=edge, width=s)
    else:
        raise ValueError(f"unknown marker shape {shape!r}")


def plus(panel, x, y, color=INK, r=7.0, width=2.5):
    """A '+' (a centroid) at data point (x, y), arm length r and stroke width in final pixels."""
    s = panel.s
    px, py = panel.px(x, y)
    r *= s
    w = int(width * s)
    panel.d.line([(px - r, py), (px + r, py)], fill=color, width=w)
    panel.d.line([(px, py - r), (px, py + r)], fill=color, width=w)
