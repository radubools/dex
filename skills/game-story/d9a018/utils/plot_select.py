"""One plot.function formula that shows one of several precomputed curves, chosen by a slider parameter."""


def curve_selector_fn(curves, param="M"):
    """Join {whole number m: formula} into sum_m max(1 - |param - m|, 0)*(formula): exactly curve m at param = m, a blend between."""
    return " + ".join(f"max(1 - abs({param} - {m}), 0)*({fn})" for m, fn in sorted(curves.items()))
