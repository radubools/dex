"""Directed-graph pieces for reading figures on top of cairo_plot: arrows between disc nodes and labels that sit above the edges."""
import math

from . import cairo_plot as cp


def arrow(c, x0, y0, x1, y1, r0, r1, offset=0.0, colour=cp.INK, width=2.4, head=16):
    """An arrow from the node at (x0, y0) to the node at (x1, y1), stopping at their rims r0, r1, shifted sideways by `offset` px (use ± for a reciprocal pair)."""
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    ax, ay = x0 + ux * r0 + nx * offset, y0 + uy * r0 + ny * offset
    bx, by = x1 - ux * (r1 + 3) + nx * offset, y1 - uy * (r1 + 3) + ny * offset
    cp.line(c, ax, ay, bx - ux * head * 0.8, by - uy * head * 0.8, colour=colour, width=width)
    c.set_source_rgb(*colour)
    c.move_to(bx, by)
    c.line_to(bx - ux * head + nx * head * 0.42, by - uy * head + ny * head * 0.42)
    c.line_to(bx - ux * head - nx * head * 0.42, by - uy * head - ny * head * 0.42)
    c.close_path()
    c.fill()


def label(c, x, y, s, size=21, colour=cp.INK):
    """Text centred on (x, baseline y) over a white backing, so edges passing behind it do not cut through it."""
    w = 0.56 * size * len(s)
    c.set_source_rgb(1, 1, 1)
    c.rectangle(x - w / 2 - 6, y - size, w + 12, size + 9)
    c.fill()
    cp.text(c, x, y, [s], size=size, colour=colour, align="centre")
