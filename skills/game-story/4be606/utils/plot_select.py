"""One plot.function formula that shows one of several precomputed curves, chosen by a slider parameter."""


def curve_selector_fn(curves, param="M"):
    """Join {whole number m: formula} into sum_m max(1 - |param - m|, 0)*(formula): exactly curve m at param = m, a blend between."""
    return " + ".join(f"max(1 - abs({param} - {m}), 0)*({fn})" for m, fn in sorted(curves.items()))


def end_guard_fn(end, weight="1", var="x"):
    """A term to add to a formula that makes it NaN for var > end (sqrt of a negative) while `weight` > 0, and 0 elsewhere, so the curve stops at end."""
    return f"sqrt(({weight})*min(0, {end} - {var}))"


def curve_selector_until_fn(curves, ends, param="M", var="x"):
    """curve_selector_fn where curve m stops at var = ends[m]: NaN past it only while its own weight is above 0, so a short curve never blanks the others."""
    parts = []
    for m, fn in sorted(curves.items()):
        w = f"max(1 - abs({param} - {m}), 0)"
        parts.append(f"{w}*({fn}) + {end_guard_fn(ends[m], w, var)}")
    return " + ".join(parts)
