"""Data points placed exactly on the llm-math plot board, at a plotted value (x, y), as data.scatter rows."""

# Model Workshop's plot.js BOARD: the window's x0..x1 spans stage x -4.5..4.5,
# y0..y1 spans height 0.35..4.35, standing at z = 0.
BOARD_X = (-4.5, 4.5)
BOARD_H = (0.35, 4.35)
BOARD_Z = 0


def board_xh(x, y, window_x, window_y):
    """The stage (x, height) where plot.js draws the value (x, y) for a board window (window_x, window_y)."""
    (x0, x1), (y0, y1) = window_x, window_y
    sx = BOARD_X[0] + (x - x0) / (x1 - x0) * (BOARD_X[1] - BOARD_X[0])
    sh = BOARD_H[0] + (y - y0) / (y1 - y0) * (BOARD_H[1] - BOARD_H[0])
    return sx, sh


def board_point(x, y, window_x, window_y, cls=0, digits=4):
    """A data.scatter row [x, z, class, height] that sits on the plot board at (x, y); lift it with data.height."""
    sx, sh = board_xh(x, y, window_x, window_y)
    return [round(sx, digits), BOARD_Z, cls, round(sh, digits)]
