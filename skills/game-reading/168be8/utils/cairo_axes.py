"""Framed linear and log-scale axes in any font (STIX Two Text has ℓ, ℝ, ν) and polylines, on top of cairo_plot."""
import math

from . import cairo_plot as cp

MATH_FONT = "STIX Two Text"


def polyline(c, pts, colour=cp.INK, width=2.0, dash=None):
    """A connected line through the pixel points pts, optionally dashed (dash = [on, off, ...])."""
    c.new_path()
    c.set_source_rgb(*colour)
    c.set_line_width(width)
    c.set_dash(dash or [])
    for i, (px, py) in enumerate(pts):
        (c.move_to if i == 0 else c.line_to)(px, py)
    c.stroke()
    c.set_dash([])


def axes(c, box, xr, yr, xticks, yticks, xlabel, ylabel, size=20, font=MATH_FONT, grid=cp.LIGHT):
    """cairo_plot.axes with numbers and titles in `font`, minus signs typeset; returns to(x, y) -> pixel."""
    to = cp.axes(c, box, xr, yr, [], [], [], [], size=size, grid=grid)
    L, T, R, B = box
    for t in yticks:
        py = to(xr[0], t)[1]
        cp.line(c, L, py, R, py, colour=grid, width=1.2)
        cp.text(c, L - 12, py + 7, [f"{t:g}".replace("-", "−")], size=size, align="right", font=font)
    for t in xticks:
        cp.text(c, to(t, yr[0])[0], B + 30, [f"{t:g}".replace("-", "−")], size=size, align="centre", font=font)
    c.new_path()
    c.set_source_rgb(*cp.INK)
    c.set_line_width(2)
    c.rectangle(L, T, R - L, B - T)
    c.stroke()
    cp.text(c, (L + R) / 2, B + 70, xlabel, size=size + 4, align="centre", font=font)
    c.save()
    c.translate(L - 75, (T + B) / 2)
    c.rotate(-math.pi / 2)
    cp.text(c, 0, 0, ylabel, size=size + 4, align="centre", font=font)
    c.restore()
    return to


def log_axes(c, box, n, lo_exp, hi_exp, xlabel, ylabel, *, step=2, xstep=10, size=20, font=MATH_FONT):
    """Axes for index 0..n+1 against 10^lo_exp..10^hi_exp (ticks every `step` decades); returns to(k, v) -> pixel, v clipped at the floor."""
    to_lin = axes(c, box, (0, n + 1), (lo_exp, hi_exp), [], [], xlabel, ylabel, size=size, font=font)
    L, T, R, B = box
    for e in range(lo_exp, hi_exp + 1, step):
        py = to_lin(0, e)[1]
        cp.line(c, L, py, R, py, colour=cp.LIGHT, width=1.2)
        cp.text(c, L - 14, py + 8, ["10", (str(e).replace("-", "−"), "sup")], size=size, align="right", font=font)
    for k in range(0, n + 1, xstep):
        cp.text(c, to_lin(k, lo_exp)[0], B + 30, [str(k)], size=size, align="centre", font=font)
    c.new_path()
    c.set_source_rgb(*cp.INK)
    c.set_line_width(2)
    c.rectangle(L, T, R - L, B - T)
    c.stroke()
    return lambda k, v: to_lin(k, math.log10(max(v, 10.0 ** lo_exp)))
