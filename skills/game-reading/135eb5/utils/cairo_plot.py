"""Small pycairo drawing kit for reading figures where matplotlib is not installed: canvas, text with sub/superscripts, discs, lines, framed axes."""
import math

import cairo


def rgb(hex_colour: str) -> tuple[float, float, float]:
    """'#RRGGBB' as a cairo (r, g, b) triple in 0..1."""
    return tuple(int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


INK = rgb("#1F2328")
LIGHT = rgb("#E4E4E4")
FONT = "Helvetica"


def canvas(width: int, height: int) -> tuple[cairo.ImageSurface, cairo.Context]:
    """A white RGB image surface and its context; save with surface.write_to_png(path)."""
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    c = cairo.Context(surf)
    c.set_source_rgb(1, 1, 1)
    c.paint()
    return surf, c


def text(c, x, y, parts, size=22, colour=INK, align="left", italic=False, font=FONT):
    """Draw runs of text at baseline y; each part is a string or (string, 'base'|'sub'|'sup'); align 'left', 'centre' or 'right'."""
    runs = [(p, "base") if isinstance(p, str) else p for p in parts]
    slant = cairo.FONT_SLANT_ITALIC if italic else cairo.FONT_SLANT_NORMAL

    def setup(kind):
        c.select_font_face(font, slant, cairo.FONT_WEIGHT_NORMAL)
        c.set_font_size(size * (0.68 if kind != "base" else 1.0))

    width = 0.0
    for s, kind in runs:
        setup(kind)
        width += c.text_extents(s).x_advance
    x0 = x - (width / 2 if align == "centre" else width if align == "right" else 0)
    c.set_source_rgb(*colour)
    for s, kind in runs:
        setup(kind)
        dy = size * 0.25 if kind == "sub" else -size * 0.4 if kind == "sup" else 0
        c.move_to(x0, y + dy)
        c.show_text(s)
        x0 += c.text_extents(s).x_advance
    c.new_path()


def disc(c, x, y, r, fill, edge=INK, edge_width=2):
    """A filled circle of radius r at (x, y) with an outline."""
    c.new_path()
    c.arc(x, y, r, 0, 2 * math.pi)
    c.set_source_rgb(*fill)
    c.fill_preserve()
    c.set_source_rgb(*edge)
    c.set_line_width(edge_width)
    c.stroke()


def line(c, x0, y0, x1, y1, colour=INK, width=1.5, dash=None):
    """A straight segment, optionally dashed (dash = [on, off, ...] in pixels)."""
    c.new_path()
    c.set_source_rgb(*colour)
    c.set_line_width(width)
    c.set_dash(dash or [])
    c.move_to(x0, y0)
    c.line_to(x1, y1)
    c.stroke()
    c.set_dash([])


def axes(c, box, xr, yr, xticks, yticks, xlabel, ylabel, size=20, grid=LIGHT):
    """Framed plot area box=(left, top, right, bottom) in pixels for data ranges xr, yr; returns to(x, y) -> pixel.

    xticks are data values or (pixel_x, label) pairs; xlabel and ylabel are `text` parts lists.
    """
    L, T, R, B = box

    def to(x, y):
        return L + (x - xr[0]) / (xr[1] - xr[0]) * (R - L), B - (y - yr[0]) / (yr[1] - yr[0]) * (B - T)

    for t in yticks:
        py = to(xr[0], t)[1]
        line(c, L, py, R, py, colour=grid, width=1.2)
        text(c, L - 12, py + 7, [f"{t:g}"], size=size, align="right")
    for t in xticks:
        px, lab = (to(t, yr[0])[0], f"{t:g}") if isinstance(t, (int, float)) else t
        text(c, px, B + 30, [lab], size=size, align="centre")
    c.new_path()
    c.set_source_rgb(*INK)
    c.set_line_width(2)
    c.rectangle(L, T, R - L, B - T)
    c.stroke()
    text(c, (L + R) / 2, B + 70, xlabel, size=size + 2, align="centre")
    c.save()
    c.translate(L - 70, (T + B) / 2)
    c.rotate(-math.pi / 2)
    text(c, 0, 0, ylabel, size=size + 2, align="centre")
    c.restore()
    return to


def rich(c, x, y, segs, size=20, colour=None, align="centre", font="STIX Two Text"):
    """One centred line of mixed-style text at baseline y: segs of (text, kind), kind False roman, True italic, 'sub'/'sup' scripts; useful when maths beside words needs per-run italic that the global slant flag cannot handle.

    Like `text`, sizes scripts at 0.68 and offsets them, and aligns left,
    centre or right on x. A bare string in segs is roman. Returns the total
    advance width in pixels.
    """
    runs = [(seg, "base") if isinstance(seg, str) else seg for seg in segs]

    def slant(kind):
        return cairo.FONT_SLANT_ITALIC if kind in (True, "sub", "sup") else cairo.FONT_SLANT_NORMAL

    def fsize(kind):
        return size * (0.68 if kind in ("sub", "sup") else 1.0)

    def dy(kind):
        return size * 0.25 if kind == "sub" else -size * 0.4 if kind == "sup" else 0.0

    widths = []
    total = 0.0
    for s, kind in runs:
        c.select_font_face(font, slant(kind), cairo.FONT_WEIGHT_NORMAL)
        c.set_font_size(fsize(kind))
        w = c.text_extents(s).x_advance
        widths.append(w)
        total += w
    x0 = x - (total / 2 if align == "centre" else total if align == "right" else 0)
    c.set_source_rgb(*(colour if colour is not None else INK))
    for (s, kind), w in zip(runs, widths):
        c.select_font_face(font, slant(kind), cairo.FONT_WEIGHT_NORMAL)
        c.set_font_size(fsize(kind))
        c.move_to(x0, y + dy(kind))
        c.show_text(s)
        x0 += w
    c.new_path()
    return total
