"""1-D kernel smoothers (ESL 6.1) as plain plot.function formulas: Nadaraya-Watson, local linear, and their equivalent kernels."""


def num(v, digits=4):
    """A number as plain formula text, without trailing zeros."""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def kernel_term(xi, x="x", lam="lam"):
    """The Epanechnikov weight (1 - ((x - xi)/lam)^2)_+ of a point at xi, without the 3/4 that cancels in every smoother here."""
    return f"max(0, 1 - (({x} - {num(xi)})/{lam})^2)"


def nadaraya_watson_fn(xs, ys, lam="lam"):
    """The Nadaraya-Watson fit (6.2) of points (xs, ys) as one formula in x, bandwidth parameter `lam`; NaN where no point is in reach."""
    top = " + ".join(f"{num(y)}*{kernel_term(x, lam=lam)}" for x, y in zip(xs, ys))
    den = " + ".join(kernel_term(x, lam=lam) for x in xs)
    return f"({top})/({den})"


def local_linear_fn(xs, ys, lam="lam"):
    """Local linear regression (6.7) as one formula in x: (S2 T0 - S1 T1)/(S0 S2 - S1^2), S_j = sum K d^j, T_j = sum K d^j y, d = x_i - x."""
    def s(j, with_y):
        parts = []
        for xi, yi in zip(xs, ys):
            d = "" if j == 0 else (f"*({num(xi)} - x)" if j == 1 else f"*({num(xi)} - x)^2")
            parts.append(f"{kernel_term(xi, lam=lam)}{d}{f'*{num(yi)}' if with_y else ''}")
        return "(" + " + ".join(parts) + ")"
    S0, S1, S2, T0, T1 = s(0, False), s(1, False), s(2, False), s(0, True), s(1, True)
    return f"({S2}*{T0} - {S1}*{T1})/({S0}*{S2} - {S1}^2)"


def equivalent_kernel_fn(xs, x0="x0", lam="lam"):
    """The local linear equivalent kernel at target `x0` as a curve over x: K(x0, x)(s2 - s1 (x - x0))/(s0 s2 - s1^2), moments over xs; at x_i it is l_i(x0)."""
    def s(j):
        parts = []
        for xi in xs:
            d = "" if j == 0 else (f"*({num(xi)} - {x0})" if j == 1 else f"*({num(xi)} - {x0})^2")
            parts.append(f"{kernel_term(xi, x=x0, lam=lam)}{d}")
        return "(" + " + ".join(parts) + ")"
    S0, S1, S2 = s(0), s(1), s(2)
    return f"max(0, 1 - ((x - {x0})/{lam})^2)*({S2} - {S1}*(x - {x0}))/({S0}*{S2} - {S1}^2)"


def nw_weights_fn(xs, x0="x0", lam="lam"):
    """The Nadaraya-Watson weights at target `x0` as a curve over x: K(x0, x) / sum_i K(x0, x_i); at x_i it is that point's weight."""
    return f"max(0, 1 - ((x - {x0})/{lam})^2)/(" + " + ".join(kernel_term(xi, x=x0, lam=lam) for xi in xs) + ")"
