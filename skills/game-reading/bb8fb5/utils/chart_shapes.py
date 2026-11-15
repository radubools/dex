"""Shapes for chart.Panel figures: filled polygons, edged dots, and a canvas capped at the reading figure width."""
import math

import cairo

from .chart import INK, Canvas


def canvas(w, h, max_px=1400):
    """A chart.Canvas of w x h points at up to 2 pixels per point, never wider than max_px pixels."""
    return Canvas(w, h, scale=min(2.0, max_px / w))


def fill_poly(p, pts, color, alpha=0.35, edge=None, width=1.6):
    """A polygon through data points `pts` on chart.Panel `p`, filled at `alpha` and outlined in `edge` if given."""
    c = p.c
    p.clip()
    for i, (x, y) in enumerate(pts):
        (c.move_to if i == 0 else c.line_to)(*p.px(x, y))
    c.close_path()
    c.set_source_rgba(*color, alpha)
    if edge is None:
        c.fill()
    else:
        c.fill_preserve()
        c.set_source_rgb(*edge)
        c.set_line_width(width)
        c.set_line_join(cairo.LINE_JOIN_ROUND)
        c.stroke()
    p.unclip()


def dot(p, x, y, color, r=4.5, edge=INK):
    """A filled dot of radius r points with an `edge` outline at data point (x, y), with no line joining it to earlier drawing."""
    c = p.c
    c.new_sub_path()
    c.arc(*p.px(x, y), r, 0, 2 * math.pi)
    c.set_source_rgb(*color)
    c.fill_preserve()
    c.set_source_rgb(*edge)
    c.set_line_width(1.0)
    c.stroke()
