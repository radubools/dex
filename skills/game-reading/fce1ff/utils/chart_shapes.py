"""Shapes for chart.Panel figures: filled polygons, edged dots, class-shape markers, and a canvas capped at the reading figure width."""
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


def shape_marker(p, x, y, shape, color, r=5.0, edge=INK, width=0.8):
    """A filled class marker at data point (x, y) on chart.Panel `p`: shape 'circle', 'square' or 'triangle', radius r points, outlined in `edge`."""
    c = p.c
    px, py = p.px(x, y)
    c.new_sub_path()
    if shape == "square":
        c.rectangle(px - r * 0.85, py - r * 0.85, 1.7 * r, 1.7 * r)
    elif shape == "triangle":
        c.move_to(px, py - r)
        c.line_to(px + r * 0.95, py + r * 0.7)
        c.line_to(px - r * 0.95, py + r * 0.7)
        c.close_path()
    elif shape == "circle":
        c.arc(px, py, r, 0, 2 * math.pi)
    else:
        raise ValueError(f"unknown marker shape {shape!r}")
    c.set_source_rgb(*color)
    c.fill_preserve()
    c.set_source_rgb(*edge)
    c.set_line_width(width)
    c.stroke()
