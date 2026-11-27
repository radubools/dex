"""Arrow, label (with white pill background) and level-curve helpers for chart.Panel."""
from __future__ import annotations

import cairo
import math

import numpy as np

from . import chart
from .contours import chain, marching_segments_xy


def arrow(panel, x0, y0, x1, y1, color, width=2.0, head=9.0, alpha=1.0):
    """Draw an arrow from data point (x0, y0) to (x1, y1) on a chart.Panel; head length in final pixels."""
    s = panel.cv.scale
    c = panel.c
    pa, pb = np.array(panel.px(x0, y0)), np.array(panel.px(x1, y1))
    n = np.linalg.norm(pb - pa)
    if n < 1e-6:
        return
    u = (pb - pa) / n
    h = min(head * s, 0.6 * n)
    base = pb - h * u
    perp = np.array([-u[1], u[0]]) * h * 0.45
    w = max(1, int(width * s))
    r, g, b = color

    c.save()
    if alpha < 1.0:
        c.set_source_rgba(r, g, b, alpha)
        c.set_line_width(w)
    else:
        c.set_source_rgb(r, g, b)
        c.set_line_width(w)
    c.set_line_join(cairo.LINE_JOIN_ROUND)

    # Body line
    c.new_path()
    c.move_to(pa[0], pa[1])
    c.line_to(base[0], base[1])
    c.stroke()

    # Arrow head (filled triangle)
    c.new_path()
    c.move_to(pb[0], pb[1])
    c.line_to(base[0] + perp[0], base[1] + perp[1])
    c.line_to(base[0] - perp[0], base[1] - perp[1])
    c.close_path()
    c.fill()

    c.restore()


def label_at(cv, panel, x, y, text, color, size=9.0, anchor="lt", bold=False):
    """Draw *text* at data point (x, y) on *panel*, centred by *anchor*, with a white pill background."""
    px_val, py_val = panel.px(x, y)

    # Measure the text to size the pill background
    c = cv.c
    c.select_font_face(chart.FONT, cairo.FONT_SLANT_NORMAL,
                       cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(size)
    ext = c.text_extents(text)
    tw = ext.x_advance
    th = ext.height

    # Anchor offsets
    dx = {"l": 0, "c": -tw / 2, "r": -tw}[anchor[0]]
    dy = {"t": size * 0.75, "m": size * 0.35, "b": 0}[anchor[1]]

    # Pill background: rounded rect around the text, with padding
    pad_x, pad_y = 4.0, 3.0
    tx = px_val + dx
    ty = py_val + dy
    rx = tx - tw / 2 - pad_x
    ry = ty - th - pad_y
    rw = tw + 2 * pad_x
    rh = th + 2 * pad_y
    r = min(4.0, rh / 2)

    # Draw white pill
    c.save()
    c.new_path()
    c.move_to(rx + r, ry)
    c.arc(rx + rw - r, ry + r, r, -math.pi / 2, 0)
    c.arc(rx + rw - r, ry + rh - r, r, 0, math.pi / 2)
    c.arc(rx + r, ry + rh - r, r, math.pi / 2, math.pi)
    c.arc(rx + r, ry + r, r, math.pi, 3 * math.pi / 2)
    c.close_path()
    c.set_source_rgb(1, 1, 1)
    c.fill_preserve()
    c.set_source_rgb(*color)
    c.set_line_width(0.5)
    c.stroke()
    c.restore()

    # Draw the text at the same position
    c.save()
    c.set_source_rgb(*color)
    c.set_font_size(size)
    c.move_to(tx, ty)
    c.show_text(text)
    c.restore()


def level_curves(F, gx, gy, value):
    """Level curves of *F* at the given *value*: a list of Nx2 numpy arrays (polylines).

    F[i, j] is at (gx[j], gy[i]). Returns open and closed polylines joined by :func:`contours.chain`.
    """
    shifted = F - value
    segs = marching_segments_xy(shifted, gx, gy)
    polys = chain(segs)
    return [np.array(p) for p in polys]
