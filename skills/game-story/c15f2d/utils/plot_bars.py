"""Bars for the llm-math plot board: a bar of a given height over [x0, x1] as one plot.function formula, labelled at its own foot."""


def plot_bar(x0: float, x1: float, height: str | float, edge: float = 0.15) -> str:
    """A plot.function formula in x: `height` (a number or formula) flat over [x0, x1], sides sloped over `edge`, undefined outside."""
    a, b = round(x0 - edge, 4), round(x1 + edge, 4)
    return f"{height}*min(1, (x - {a})/{edge}, ({b} - x)/{edge}) + 0*sqrt((x - {a})*({b} - x))"
