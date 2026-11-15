"""Survival curves for the llm-math plot board: Kaplan-Meier steps, the two-group log-rank P value, and the steps as one plot.function formula."""
from math import erfc, sqrt

import numpy as np

from .plot_steps import step_fn


def kaplan_meier(time, event):
    """Kaplan-Meier steps [[t, S(t)], ...] from [0, 1], one per distinct event time (event 1 = death, 0 = censored)."""
    time, event = np.asarray(time, float), np.asarray(event, float)
    steps, s = [[0.0, 1.0]], 1.0
    for t in np.unique(time[event == 1]):
        n = (time >= t).sum()
        d = ((time == t) & (event == 1)).sum()
        s *= 1 - d / n
        steps.append([float(t), float(s)])
    return steps


def logrank_p(time, event, group):
    """The two-sided log-rank P value (chi-square, 1 df) for survival differing between `group` True and False."""
    time, event, group = np.asarray(time, float), np.asarray(event, float), np.asarray(group, bool)
    O = E = V = 0.0
    for t in np.unique(time[event == 1]):
        at = time >= t
        n, n1 = at.sum(), (at & group).sum()
        d = ((time == t) & (event == 1)).sum()
        O += ((time == t) & (event == 1) & group).sum()
        E += d * n1 / n
        if n > 1:
            V += d * (n1 / n) * (1 - n1 / n) * (n - d) / (n - 1)
    return float(erfc(sqrt((O - E) ** 2 / V / 2)))


def km_points(steps, upto=None, gap=0.02):
    """The steps up to `upto` (all when None), steps closer than `gap` in t merged into one (the later value)."""
    pts = []
    for t, s in steps:
        if upto is not None and t > upto:
            break
        if pts and t - pts[-1][0] < gap:
            pts[-1][1] = s
        else:
            pts.append([t, s])
    return pts


def km_formula(steps, upto=None, param="t", *, gap=0.02, steep=100, digits=4):
    """Kaplan-Meier steps as one plain plot.function formula in `param` (km_points merged, ramps 1/steep wide before each step)."""
    return step_fn(km_points(steps, upto, gap), param=param, steep=steep, digits=digits)
