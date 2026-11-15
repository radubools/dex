"""Gaussian kernel density estimates of a sample for the llm-math plot board, as one plot.function formula."""
from collections import Counter


def kde_fn(values, lam="lam", var="x"):
    """The Gaussian KDE (1/(N lam)) sum_i phi((var - x_i)/lam) of `values` as one formula; repeated values become one weighted term, `lam` a number or a parameter name."""
    counts = sorted(Counter(values).items())
    terms = " + ".join(f"{c}*exp(-({var} - {v})^2/(2*{lam}^2))" if c > 1 else f"exp(-({var} - {v})^2/(2*{lam}^2))"
                       for v, c in counts)
    return f"({terms})/({len(values)}*{lam}*sqrt(2*pi))"
