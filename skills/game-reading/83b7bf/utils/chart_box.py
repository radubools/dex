"""Boxplots for chart.Panel figures: quartile box, median, 1.5-IQR whiskers, outlier dashes, off-scale counts."""
import numpy as np

from .chart import INK, hexrgb

OFF_SCALE = hexrgb("#D55E00")


def boxplot(p, x, vals, color=hexrgb("#56B4E9"), w=0.32, off_color=OFF_SCALE):
    """An R-style boxplot of `vals` at data x on chart.Panel `p` (half-width w); values above the panel are counted under a triangle at its top."""
    v = np.sort(np.asarray(vals, float))
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    iqr = q3 - q1
    lo, hi = v[v >= q1 - 1.5 * iqr].min(), v[v <= q3 + 1.5 * iqr].max()
    c = p.c
    p.clip()
    p.line([x, x], [lo, q1], INK, 1.0, dash=[3, 2])
    p.line([x, x], [q3, hi], INK, 1.0, dash=[3, 2])
    for y in (lo, hi):
        p.line([x - w / 2, x + w / 2], [y, y], INK, 1.0)
    (x0, y0), (x1, y1) = p.px(x - w, q3), p.px(x + w, q1)
    c.rectangle(x0, y0, x1 - x0, max(y1 - y0, 1.2))
    c.set_source_rgb(*color)
    c.fill_preserve()
    c.set_source_rgb(*INK)
    c.set_line_width(0.9)
    c.stroke()
    p.line([x - w, x + w], [med, med], INK, 2.0)
    for y in v[(v < lo) | (v > hi)]:
        p.line([x - w / 2, x + w / 2], [y, y], INK, 1.4)
    p.unclip()
    off = int(np.sum(v > p.ylim[1]))
    if off:
        px, py = p.px(x, p.ylim[1])
        c.move_to(px - 4, py + 8)
        c.line_to(px + 4, py + 8)
        c.line_to(px, py + 2)
        c.close_path()
        c.set_source_rgb(*off_color)
        c.fill()
        p.cv.text(px, py + 10, str(off), 8.5, off_color, anchor="ct", bold=True)
