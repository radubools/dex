"""Checks on how a story writes its maths: values in formulas versus values in plain text."""
from __future__ import annotations

import re
from typing import Any

_FORMULA = re.compile(r"\$\$.*?\$\$", re.S)


def val_outside_math(story: Any, path: str = "story") -> list[str]:
    """Paths of the strings in a story (any nesting) where \\val{…} sits outside $$…$$; the game shows those raw."""
    out: list[str] = []
    if isinstance(story, str):
        if "\\val" in _FORMULA.sub("", story):
            out.append(path)
    elif isinstance(story, dict):
        for k, v in story.items():
            out += val_outside_math(v, f"{path}.{k}")
    elif isinstance(story, list):
        for i, v in enumerate(story):
            out += val_outside_math(v, f"{path}[{i}]")
    return out
