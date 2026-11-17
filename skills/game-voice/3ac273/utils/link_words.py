"""Speakable words for story cross-links ([[story/x|label]]) in caption text."""
import re

_LABELLED = re.compile(r"\[\[story/[^|\]]+\|([^\]]+)\]\]")
_BARE = re.compile(r"\[\[story/([^\]|]+)\]\]")


def link_words(text: str, prefix: str = "llm-") -> str:
    """Replace [[story/x|label]] with its label and a bare [[story/<prefix>x-y]] with "the x y story"."""
    text = _LABELLED.sub(r"\1", text)

    def bare(m):
        name = m.group(1)
        if prefix and name.startswith(prefix):
            name = name[len(prefix):]
        return "the " + name.replace("-", " ") + " story"

    return _BARE.sub(bare, text)
