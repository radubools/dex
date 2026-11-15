"""Compare a reading's numbered display equations with a reference text's (the study notes), ignoring spacing and sizing."""
import re

from .reading import numbered_equations


def normalize(tex: str) -> str:
    """LaTeX without spacing commands, \\big/\\Big sizing, whitespace, commas and full stops, for comparing formulas."""
    tex = re.sub(r"\\(Big|big|qquad|quad|,|;|!|\s)|[\s,.]", "", tex)
    tex = re.sub(r"\\text\{([^}]*)\}", lambda m: m.group(1).replace(" ", ""), tex)
    return tex.replace("\\ ", "")


def equation_diffs(markdown: str, reference_markdown: str) -> list[str]:
    """Numbers of the display equations in `markdown` whose LaTeX differs from (or is missing in) `reference_markdown`, normalized."""
    ref = numbered_equations(reference_markdown)
    return [n for n, tex in numbered_equations(markdown).items()
            if n not in ref or normalize(tex) != normalize(ref[n])]
