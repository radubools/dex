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


def ui_highlight_fixes(beats: list) -> list:
    """Copies of story beats whose highlights the game can resolve: one-item highlight lists unwrapped to the object, `ui` names inside longer lists made CSS (ui_css)."""
    out = []
    for beat in beats:
        b = dict(beat)
        h = b.get("highlight")
        if isinstance(h, list) and len(h) == 1:
            b["highlight"] = dict(h[0])
        elif isinstance(h, list):
            b["highlight"] = [{**x, "ui": ui_css(x["ui"])} if isinstance(x, dict) and isinstance(x.get("ui"), str) else x for x in h]
        out.append(b)
    return out
