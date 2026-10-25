"""Bench names as the CSS selectors schema-2 highlight lists and e2e `ui` checks need (Model Workshop).

A single-object `highlight.ui` goes through the game's lesson.js uiSelector,
which knows names ("bench", "slider:lr"). Inside a highlight list the string
reaches querySelectorAll as is, so a name there throws ("'slider:lr' is not a
valid selector") and the end-to-end run stops with "0 beats, 1 errors".
"""


def ui_css(name: str) -> str:
    """The CSS for a bench name: bench, formula/plaque, loss, meter:<id>, chart:<id>, slider:<p>, tool:<t>; anything else unchanged."""
    kind, _, rest = name.partition(":")
    if name == "bench":
        return "#bench"
    if name in ("formula", "plaque"):
        return ".plaque"
    if name == "loss" or kind == "meter":
        return ".meter"
    if kind == "chart":
        return ".chart"
    if kind == "slider" and rest:
        return f'.slider[data-param="{rest}"]'
    if kind == "tool" and rest:
        return f'.tool[data-tool="{rest}"]'
    return name
