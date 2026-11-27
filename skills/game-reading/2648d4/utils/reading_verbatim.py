"""Prose of a reading passage that does not occur in the source text: what a verbatim check must look at."""
import re
import unicodedata


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def unquoted_runs(markdown: str, source: str, *, min_chars: int = 25, phrase_chars: int = 12) -> list[str]:
    """Prose runs of a passage (between formulas, figures, citations and […] cuts) not found in `source`; a run that crosses a page break is checked phrase by phrase."""
    src = re.sub(r"-\n", "", unicodedata.normalize("NFKC", source))
    s = _letters(src)
    md = re.sub(r"\$\$.*?\$\$", "|", markdown, flags=re.S)
    md = re.sub(r"!\[.*?\]\(.*?\)", "|", md)
    md = re.sub(r"\$[^$]*\$", "|", md)
    md = re.sub(r"\[([^\]]*)\]\(#ref:[^)]*\)", r"\1", md)
    md = md.replace("[…]", "|")
    missing = []
    for run in re.split(r"[|\n]", md):
        if len(_letters(run)) < min_chars or _letters(run) in s:
            continue
        for part in re.split(r"(?<=[.,;:])\s+", run):
            if len(_letters(part)) >= phrase_chars and _letters(part) not in s:
                missing.append(part.strip())
    return missing
