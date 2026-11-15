"""Prose of a retold reading passage that does occur in the source text: what an own-words check must look at."""
import re

__all__ = ["words", "prose", "shared_runs"]


def words(text: str) -> list[str]:
    """Lowercased a–z words of `text`, with end-of-line hyphenation joined."""
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.findall(r"[a-z]+", text.lower())


def prose(markdown: str) -> str:
    """A passage's markdown with display and inline maths and images taken out."""
    md = re.sub(r"\$\$.*?\$\$", " ", markdown, flags=re.S)
    md = re.sub(r"\$[^$]*\$", " ", md)
    return re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)


def shared_runs(markdown: str, source: str, *, n: int = 7) -> list[str]:
    """Every run of `n` consecutive prose words of `markdown` also found in `source` (also matched with spaces removed, for PDF text that splits words)."""
    src = words(source)
    grams = {" ".join(src[i:i + n]) for i in range(len(src) - n + 1)}
    letters = "".join(src)
    w = words(prose(markdown))
    runs = (w[i:i + n] for i in range(len(w) - n + 1))
    return [" ".join(r) for r in runs if " ".join(r) in grams or "".join(r) in letters]
