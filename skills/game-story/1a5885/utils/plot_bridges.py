"""Brownian bridges for the llm-math plot board: a noisy path from a to b over t in [0, 1] as one plot.function formula."""
import math

# NaN outside 0 <= t <= 1, so a path drawn in a wider window stops at its two ends.
GUARD = " + 0*sqrt({v} + 1e-9) + 0*sqrt(1 - {v} + 1e-9)"


def bridge_path_fn(a, b, draws, *, scale="sqrt(eps/2)", var="t", digits=4):
    """The formula (1 - t)a + tb + scale * sqrt(2) sum_k z_k sin(k pi t)/(k pi): a Brownian bridge with fixed draws z_k, its noise `scale` itself a formula in the curve's parameters."""
    terms = " + ".join(f"{math.sqrt(2) * z / (k * math.pi):.{digits}f}*sin({k}*pi*{var})" for k, z in enumerate(draws, 1))
    path = f"(1 - {var})*{a} + {var}*{b} + {scale}*({terms})".replace("+ -", "- ")
    return path + GUARD.format(v=var)
