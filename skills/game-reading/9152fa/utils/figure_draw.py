"""Cairo primitives for schematic reading figures: arrows, dots, rings, path-safe text, panel backgrounds."""
from __future__ import annotations

import math

import cairo


def safe_text(cv, x: float, y: float, s: str, **kw) -> None:
    """Draw cv.text(x, y, s, **kw) and clear cairo's path, so later arcs never connect to it."""
    cv.text(x, y, s, **kw)
    cv.c.new_path()


def arrow(c: cairo.Context, x0: float, y0: float, x1: float, y1: float,
          color, width: float = 1.8, head: float = 7.0, dash=None) -> None:
    """Straight arrow from (x0, y0) to (x1, y1) in canvas points, filled head, optional dash."""
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return
    ux, uy = dx / dist, dy / dist
    sx, sy = x1 - ux * (head * 0.7), y1 - uy * (head * 0.7)
    c.save()
    c.new_path()
    c.set_source_rgb(*color)
    c.set_line_width(width)
    c.set_dash(dash or [])
    c.move_to(x0, y0)
    c.line_to(sx, sy)
    c.stroke()
    c.set_dash([])
    px, py = -uy, ux
    w = head * 0.44
    c.move_to(x1, y1)
    c.line_to(x1 - ux * head + px * w, y1 - uy * head + py * w)
    c.line_to(x1 - ux * head - px * w, y1 - uy * head - py * w)
    c.close_path()
    c.fill()
    c.restore()
    c.new_path()


def dot(c: cairo.Context, x: float, y: float, color, r: float = 3.0) -> None:
    """Filled point of radius r, unoutlined; leaves no current path."""
    c.new_path()
    c.arc(x, y, r, 0, 2 * math.pi)
    c.set_source_rgb(*color)
    c.fill()
    c.new_path()


def ring(c: cairo.Context, cx: float, cy: float, r: float, color,
         width: float = 1.2, dash=None, fill=None, alpha: float = 1.0) -> None:
    """Circle outline (optionally dashed), under an optional translucent fill; leaves no path."""
    c.save()
    c.new_path()
    c.arc(cx, cy, r, 0, 2 * math.pi)
    if fill is not None:
        c.set_source_rgba(*fill, alpha)
        c.fill_preserve()
    c.set_source_rgb(*color)
    c.set_line_width(width)
    c.set_dash(dash or [])
    c.stroke()
    c.restore()
    c.new_path()


def panel_grid(p, xticks, yticks, bg=(250 / 255, 251 / 255, 252 / 255),
               grid=(234 / 255, 238 / 255, 242 / 255)) -> None:
    """Paint a chart.Panel's box with `bg` and light `grid` lines at the ticks, clipped to the box."""
    c = p.c
    c.save()
    c.new_path()
    c.rectangle(p.l, p.t, p.r - p.l, p.b - p.t)
    c.set_source_rgb(*bg)
    c.fill_preserve()
    c.clip()
    c.set_source_rgb(*grid)
    c.set_line_width(0.7)
    for x in xticks:
        px = p.px(x, p.ylim[0])[0]
        c.move_to(px, p.t)
        c.line_to(px, p.b)
        c.stroke()
    for y in yticks:
        py = p.px(p.xlim[0], y)[1]
        c.move_to(p.l, py)
        c.line_to(p.r, py)
        c.stroke()
    c.restore()
    c.new_path()
