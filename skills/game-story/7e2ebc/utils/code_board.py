"""Story cameras over a range of a code-station program's lines, framed by their `code:line-<n>` ids."""
from __future__ import annotations

# The game places the board (upright at the back of the floor, scaled to the
# program; ~/games/model-workshop/design/06_algorithms.md, "The floor"), so a
# shot of the code frames line ids rather than aiming at a fixed target.


def board_lines_camera(source: str, first: int, last: int, *, duration: float = 1) -> dict:
    """A story camera {frame, duration} framing `code:line-<first>`..`code:line-<last>` of `source`."""
    rows = len(source.rstrip().split("\n"))
    if not 1 <= first <= last <= rows:
        raise ValueError(f"lines {first}..{last} are outside the program's 1..{rows}")
    return {"frame": [f"code:line-{n}" for n in range(first, last + 1)], "duration": duration}
