"""Gaussian contours Δ² = c of N(0, [[1, ρ], [ρ, 1]]) for the llm-math plot board: the plot.function acts and the equal-scale window that draw one."""

# The plot board is 9 wide and 4 tall (see plot_balls). This window keeps one
# unit the same length both ways, so ρ = 0 draws circles, and holds the
# contour Δ² = 4 (it reaches x2 = ±2 for every ρ).
GAUSS_X = [-4.95, 4.95]
GAUSS_Y = [-2.2, 2.2]

# The two halves of the edge, x2 = ρ x1 ± sqrt((1 - ρ²)(c - x1²)). The root is
# clamped at 0 and the curve masked a little past the tip x1 = ±√c ("+ 0
# sqrt(…)" is undefined beyond it), so a board sample lands where both halves
# equal ρ x1 and they join instead of leaving a gap at each tip.
GAUSS_TOP = "ρ x + sqrt(max((1 - ρ^2)(c - x^2), 0)) + 0 sqrt(sqrt(c) + {tail} - |x|)"
GAUSS_BOTTOM = "ρ x - sqrt(max((1 - ρ^2)(c - x^2), 0)) - 0 sqrt(sqrt(c) + {tail} - |x|)"


def rho_slider(value, *, lo=-0.95, hi=0.95, step=0.05, label="$$\\rho$$"):
    """A plot.function parameter spec for the correlation ρ: its value and the slider's range."""
    return {"value": value, "min": lo, "max": hi, "step": step, "label": label}


def contour_acts(c, rho, *, ids=None, color="class-0", label=None, sliders=True, clear=False,
                 x_label="$$x_1$$", y_label="$$x_2$$", tail=0.1):
    """The two plot.function acts drawing the contour Δ² = c of the unit-variance Gaussian with correlation ρ (a number, or a rho_slider spec when sliders)."""
    top, bottom = ids or (f"c{c}-top", f"c{c}-bot")
    if sliders:
        r = rho if isinstance(rho, dict) else rho_slider(rho)
    else:
        r = rho["value"] if isinstance(rho, dict) else rho
    acts = []
    for id_, fn in ((top, GAUSS_TOP), (bottom, GAUSS_BOTTOM)):
        args = {"id": id_, "fn": fn.format(tail=tail), "params": {"ρ": dict(r) if isinstance(r, dict) else r, "c": c},
                "x": GAUSS_X, "y": GAUSS_Y, "color": color}
        if sliders:
            args["sliders"] = ["ρ"]
        acts.append({"action": "plot.function", "args": args})
    acts[0]["args"].update({"xLabel": x_label, "yLabel": y_label, "clear": clear})
    if label is not None:
        acts[0]["args"]["label"] = label
    return acts
