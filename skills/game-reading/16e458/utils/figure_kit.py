"""Figure kit for reading figures: font glyph audit and canvas primitives for utils.chart.

`missing_glyphs` answers which characters a font cannot draw (data; the caller
decides whether to raise). `arrow`, `box` and `vbar` draw the small pieces every
reading schematic needs on a `chart.Canvas`, in canvas points.
"""
from __future__ import annotations

import math

import cairo

from . import chart

MATH_FONT = "STIX Two Text"  # Greek, superscripts and set symbols for figure labels


def missing_glyphs(text, font=None):
    """The distinct non-space characters of `text` that `font` (default MATH_FONT) has no glyph for, in order."""
    font = font or MATH_FONT
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 1, 1)
    c = cairo.Context(surf)
    c.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(12)
    sf = c.get_scaled_font()
    miss: list[str] = []
    for ch in str(text):
        if ch.isspace() or ch in miss:
            continue
        glyphs = sf.text_to_glyphs(0, 0, ch)[0]
        if not glyphs or glyphs[0].index == 0:
            miss.append(ch)
    return miss


def arrow(cv: chart.Canvas, x0: float, y0: float, x1: float, y1: float,
          color=chart.INK, width: float = 1.6, head: float = 7.0):
    """A straight arrow from (x0, y0) to (x1, y1) in canvas points, with a filled head."""
    c = cv.c
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    c.new_path()
    c.set_source_rgb(*color)
    c.set_line_width(width)
    c.move_to(x0, y0)
    c.line_to(x1 - ux * head * 0.8, y1 - uy * head * 0.8)
    c.stroke()
    c.new_path()
    c.move_to(x1, y1)
    c.line_to(x1 - ux * head + nx * head * 0.42, y1 - uy * head + ny * head * 0.42)
    c.line_to(x1 - ux * head - nx * head * 0.42, y1 - uy * head - ny * head * 0.42)
    c.close_path()
    c.fill()


def box(cv: chart.Canvas, x: float, y: float, w: float, h: float,
         color=chart.INK, fill=None, width: float = 1.2, r: float = 4.0):
    """A rounded rectangle in canvas points; `fill` (a colour) tints it translucently."""
    c = cv.c
    c.save()
    c.set_line_width(width)
    c.new_path()
    c.move_to(x + r, y)
    c.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    c.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    c.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    c.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    c.close_path()
    if fill is not None:
        c.set_source_rgba(*fill, 0.14)
        c.fill_preserve()
    c.set_source_rgb(*color)
    c.stroke()
    c.restore()


def vbar(cv: chart.Canvas, x: float, y_base: float, y_top: float, color, width: float = 11.0):
    """A translucent vertical bar with a solid cap dot, from y_base to y_top (canvas points)."""
    c = cv.c
    c.new_path()
    c.set_source_rgba(*color, 0.85)
    c.rectangle(x - width / 2, min(y_base, y_top), width, abs(y_top - y_base))
    c.fill()
    c.new_path()
    c.set_source_rgb(*color)
    c.arc(x, y_top, width * 0.45, 0, 2 * math.pi)
    c.fill()


__all__ = ["MATH_FONT", "missing_glyphs", "arrow", "box", "vbar"]
