"""Cut extracted document text into paragraph chunks, CJK-aware.

The functions here are mechanism: each takes text and returns a list of
strings. They decide nothing about what a segment should contain, how it should
be numbered, or whether a package is correct — that is the caller's job.
"""

import re

# Sentence-final punctuation in Chinese prose.
SENTENCE_END = "。！？…"
# Closing marks that may trail a sentence end; they stay with the sentence they close.
TRAILING = "”’」』）》"
# A paragraph longer than this is worth splitting before translating it.
DEFAULT_LIMIT = 600


def indented_lines(text: str) -> list[tuple[int, str]]:
    """Return (indent, stripped text) per line, with blank lines as (-1, '')."""
    out: list[tuple[int, str]] = []
    for raw in text.replace("\f", "").split("\n"):
        stripped = raw.strip()
        out.append((-1, "") if not stripped else (len(raw) - len(raw.lstrip(" ")), stripped))
    return out


def join_lines(parts: list[str]) -> str:
    """Rejoin one paragraph's wrapped lines, joining CJK directly and spacing only Latin-to-Latin."""
    out = ""
    for part in parts:
        if not part:
            continue
        latin_left = bool(out) and out[-1].isascii() and out[-1].isalnum()
        latin_right = part[0].isascii() and part[0].isalnum()
        out += (" " + part) if (latin_left and latin_right) else part
    return out


def blocks_by_indent(text: str) -> list[str]:
    """Split a laid-out column into joined paragraphs on first-line indent (or a blank line).

    CJK reading pages justify continuation lines to the left margin and indent
    the first line of each paragraph, so the indent — not a blank line — is what
    separates one paragraph from the next.
    """
    lines = indented_lines(text)
    body = [i for i, _ in lines if i >= 0]
    if not body:
        return []
    margin = min(body)

    blocks: list[list[str]] = []
    for indent, txt in lines:
        if indent < 0:
            if blocks and blocks[-1]:
                blocks.append([])
            continue
        if indent > margin or not blocks:
            blocks.append([txt])
        else:
            blocks[-1].append(txt)
    return [join_lines(b) for b in blocks if b]


def blocks_by_blank_line(text: str) -> list[str]:
    """Split text into joined paragraphs on blank lines, for sources with no indent signal."""
    return [
        join_lines([t for _, t in indented_lines(part) if t])
        for part in re.split(r"\n\s*\n", text.replace("\f", "\n\n"))
        if part.strip()
    ]


def split_at_sentences(text: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Split a paragraph longer than `limit` at sentence ends only, never mid-sentence."""
    if len(text) <= limit:
        return [text]
    sentences = re.findall(
        rf"[^{SENTENCE_END}]*[{SENTENCE_END}][{TRAILING}]*|[^{SENTENCE_END}]+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        chunks.append(cur)
    return chunks
