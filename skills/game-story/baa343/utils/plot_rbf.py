"""Gaussian radial basis functions for the llm-math plot board: bumps, their sum, renormalized shares (ESL 6.30) and weighted fits as plot.function formulas."""


def _num(v, digits=4):
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def bump_fn(c, lam="lam", var="x"):
    """The Gaussian bump e^{-((var - c)/lam)^2/2} centred at c as a formula; lam is a parameter name or a number."""
    return f"exp(-(({var} - {_num(c)})/{lam})^2/2)"


def sum_fn(centres, lam="lam", var="x"):
    """The sum of the Gaussian bumps at `centres` as a formula."""
    return " + ".join(bump_fn(c, lam, var) for c in centres)


def renorm_fn(c, centres, lam="lam", var="x"):
    """The renormalized bump at c, its share of the sum of the bumps at `centres` (ESL 6.30), as a formula."""
    return f"({bump_fn(c, lam, var)})/({sum_fn(centres, lam, var)})"


def fit_fn(betas, centres, lam="lam", var="x", digits=4):
    """sum_j beta_j times the bump at centre_j as a formula, the betas written to `digits` decimals."""
    return " + ".join(f"{_num(b, digits)}*{bump_fn(c, lam, var)}" for b, c in zip(betas, centres))
