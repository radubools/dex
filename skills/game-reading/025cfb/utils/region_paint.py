"""Two-class decision regions on a chart.Panel: shade where a classifier's probability is above or below a level, and its boundary polylines."""
import cairo
import numpy as np

from .contours import chain, marching_segments_xy


def paint_regions(panel, proba, xlim, ylim, colors, *, n=181, alpha=0.22, level=0.5):
    """Shade an n x n grid over xlim x ylim on `panel` with colors[0] where proba(points (M, 2)) <= level, colors[1] above; returns the level boundary as polylines."""
    gx, gy = np.linspace(*xlim, n), np.linspace(*ylim, n)
    G = np.array([[a, b] for b in gy for a in gx])
    pr = np.asarray(proba(G), float).reshape(n, n)
    c = panel.c
    c.set_antialias(cairo.ANTIALIAS_NONE)
    dx, dy = gx[1] - gx[0], gy[1] - gy[0]
    for i, b in enumerate(gy):
        for j, a in enumerate(gx):
            x0, y0 = panel.px(a - dx / 2, b + dy / 2)
            x1, y1 = panel.px(a + dx / 2, b - dy / 2)
            c.set_source_rgba(*colors[int(pr[i, j] > level)], alpha)
            c.rectangle(x0, y0, x1 - x0, y1 - y0)
            c.fill()
    c.set_antialias(cairo.ANTIALIAS_DEFAULT)
    return chain(marching_segments_xy(pr - level, gx, gy))
