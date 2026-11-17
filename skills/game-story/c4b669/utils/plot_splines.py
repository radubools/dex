"""Cubic regression splines in the truncated power basis 1, x, x^2, x^3, (x - xi_k)_+^3 as plain plot.function formulas."""


def _num(v, digits):
    return f"{float(v):.{digits}g}"


def truncated_power_basis_fns(knots, var="x"):
    """The basis formulas 1, var, var^2, var^3, relu(var - xi_k)^3 for each knot, in that order (e.g. for plot_quadform)."""
    return ["1", var, f"{var}^2", f"{var}^3"] + [f"relu({var} - {k})^3" for k in knots]


def truncated_power_fn(beta, knots, var="x", digits=10):
    """The spline sum_j beta_j h_j(var) over truncated_power_basis_fns(knots) as one formula, coefficients to `digits` significant digits."""
    terms = truncated_power_basis_fns(knots, var)
    if len(beta) != len(terms):
        raise ValueError(f"{len(knots)} knots need {len(terms)} coefficients, got {len(beta)}")
    out = _num(beta[0], digits)
    for c, t in zip(beta[1:], terms[1:]):
        c = float(c)
        out += (" - " if c < 0 else " + ") + _num(abs(c), digits) + "*" + t
    return out


def bspline_to_truncated_power(beta, knots, lo, hi, samples=301):
    """Truncated-power coefficients (for truncated_power_fn) of the cubic B-spline with coefficients `beta`, interior `knots`, on [lo, hi]."""
    import numpy as np
    from scipy.interpolate import BSpline

    knots = [float(k) for k in knots]
    beta = np.asarray(beta, float)
    if len(beta) != len(knots) + 4:
        raise ValueError(f"{len(knots)} knots need {len(knots) + 4} B-spline coefficients, got {len(beta)}")
    t = np.linspace(lo, hi, samples)
    kn = np.r_[[float(lo)] * 4, knots, [float(hi)] * 4]
    y = BSpline(kn, beta, 3)(np.clip(t, lo, hi - 1e-12 * max(1.0, abs(hi))))
    tp = np.column_stack([np.ones_like(t), t, t ** 2, t ** 3] + [np.maximum(t - k, 0) ** 3 for k in knots])
    return np.linalg.lstsq(tp, y, rcond=None)[0].tolist()
