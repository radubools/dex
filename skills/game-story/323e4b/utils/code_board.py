"""Where a code-station program's lines lie on the floor board, and close story cameras over a range of them."""
from __future__ import annotations

# The engine's BoardLayout (engine/src/code/board-layout.js) as Model Workshop's
# code station uses it: near edge at z = -0.8, line 1 the far line.
FRONT = -0.8
LINE = 0.36
PAD = 0.28
MIN_LINES = 3


def board_line_z(source: str, n: int, *, front: float = FRONT) -> float:
    """The world z of the middle of line n (1-based) of `source` on the code station's board."""
    rows = max(MIN_LINES, len(source.rstrip().split("\n")))
    back = front - (rows * LINE + 2 * PAD)
    return round(back + PAD + (n - 0.5) * LINE, 2)


def board_lines_camera(source: str, first: int, last: int, *, distance: float = 10,
                       duration: float = 1, front: float = FRONT) -> dict:
    """A fixed story camera {target, distance, duration} centred on lines first..last of `source`'s board."""
    z = (board_line_z(source, first, front=front) + board_line_z(source, last, front=front)) / 2
    return {"target": [0, round(z, 2)], "distance": distance, "duration": duration}
