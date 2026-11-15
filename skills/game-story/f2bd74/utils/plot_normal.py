"""The standard normal CDF, its inverse and binormal ROC curves as plain plot.function formulas."""

_C = "4.330747"  # 2 / (pi * 0.147), Winitzki's erf^-1 constant (written out: the plot compiler reads pi as a parameter)


def pnorm_fn(arg):
    """The formula Phi(arg) = (1 + erf(arg / sqrt 2)) / 2, for `arg` a formula or a number."""
    return f"(0.5*(1 + erf(({arg})/sqrt(2))))"


def qnorm_fn(var="x"):
    """The formula Phi^-1(var) by Winitzki's erf^-1 approximation (|Phi(result) - var| < 2e-3 on [0.01, 0.999]); NaN at 0 and 1."""
    u = f"(2*{var}-1)"
    lg = f"ln(1-{u}^2)"
    c = f"({_C} + {lg}/2)"
    return f"(sqrt(2)*sign(2*{var}-1)*sqrt(sqrt({c}^2 - {lg}/0.147) - {c}))"


def binormal_roc_fn(a, b=1, var="x"):
    """The binormal ROC sensitivity Phi(a - b * Phi^-1(var)) over specificity `var`; its AUC is Phi(a / sqrt(1 + b^2))."""
    return pnorm_fn(f"{a} - {b}*{qnorm_fn(var)}")
