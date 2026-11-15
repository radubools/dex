"""Quadratic forms h(x)^T M h(x) of basis functions as one plot.function formula, e.g. a linear fit's pointwise variance."""


def quadratic_form_fn(fns, M):
    """The plain formula sum_i M_ii h_i^2 + 2 sum_{i<j} M_ij h_i h_j for basis formulas `fns` and symmetric matrix `M`, coefficients at full precision."""
    terms = []
    for i in range(len(fns)):
        for j in range(i, len(fns)):
            c = float(M[i][j]) * (1 if i == j else 2)
            terms.append(f"({c!r})*({fns[i]})*({fns[j]})")
    return " + ".join(terms)
