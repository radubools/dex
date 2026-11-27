"""Tangent distance for small grey-scale images (ESL 13.3.3, Ex. 13.4), numpy only: kernel-smoothed derivatives, affine tangent images, two-sided distance."""
import numpy as np


def grid(side=16):
    """Paper coordinates (x1 right, x2 up) of a side x side image's pixel centres, row-major from the top, origin at the image centre."""
    r, c = np.divmod(np.arange(side * side), side)
    centre = (side - 1) / 2
    return np.stack([c - centre, centre - r], axis=1).astype(float)


def smoother(image, lam=0.9):
    """A Gaussian Nadaraya-Watson smoother of a square image's pixels (bandwidth lam pixels): F(z) -> (value, dF/dx1, dF/dx2) at points z (M, 2)."""
    f = np.asarray(image, float)
    Z = grid(int(round(np.sqrt(f.size))))

    def F(z):
        z = np.atleast_2d(z)
        d = z[:, None, :] - Z[None, :, :]
        w = np.exp(-(d ** 2).sum(-1) / (2 * lam ** 2))
        sw, swf = w.sum(1), w @ f
        grads = []
        for k in range(2):
            dw = -w * d[:, :, k] / lam ** 2
            grads.append(((dw @ f) * sw - swf * dw.sum(1)) / sw ** 2)
        return swf / sw, grads[0], grads[1]
    return F


def affine_image(image, theta=0.0, s1=1.0, s2=1.0, h=0.0, c=(0.0, 0.0), lam=0.9):
    """The smoothed image F(c + x0 + A(x - x0)), A = Q(theta) [[s1, h], [0, s2]], sampled at the pixel centres (identity = smoothed image)."""
    f = np.asarray(image, float)
    Q = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    A = Q @ np.array([[s1, h], [0.0, s2]])
    z = np.asarray(c) + grid(int(round(np.sqrt(f.size)))) @ A.T
    return smoother(f, lam)(z)[0]


def tangents(image, lam=0.9):
    """The six tangent images of ESL Ex. 13.4 at the identity: {rotation, scale 1, scale 2, shear, translation 1, translation 2} -> pixel vectors."""
    f = np.asarray(image, float)
    d = grid(int(round(np.sqrt(f.size))))
    _, F1, F2 = smoother(f, lam)(d)
    d1, d2 = d[:, 0], d[:, 1]
    return {
        "rotation": -d2 * F1 + d1 * F2,
        "scale 1": d1 * F1,
        "scale 2": d2 * F2,
        "shear": d2 * F1,
        "translation 1": F1,
        "translation 2": F2,
    }


def tangent_distance(x, Tx, y, Ty):
    """Two-sided tangent distance min over a, b of ||(x + Tx a) - (y + Ty b)|| (tangents as columns), by least squares: (distance, a, b)."""
    T = np.hstack([Tx, -Ty])
    coef, *_ = np.linalg.lstsq(T, y - x, rcond=None)
    k = Tx.shape[1]
    r = (x + Tx @ coef[:k]) - (y + Ty @ coef[k:])
    return float(np.linalg.norm(r)), coef[:k], coef[k:]
