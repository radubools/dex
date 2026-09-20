"""Answer the questions a finished bitext has to pass, as lists of problems.

Every function here takes data a caller has already loaded and returns a list of
strings describing what is wrong, empty when nothing is. None of them reads a
file, prints, raises on a bad document, or decides that a package has failed —
the caller counts what comes back and decides what it means. That is what lets
one package treat an untranslated segment as fatal while another, building the
same document section by section, treats it as work still to do.
"""

from collections import Counter


def numbering_problems(segments: list[dict]) -> list[str]:
    """Report whether `n` runs 1..N with no gaps, no repeats and in reading order."""
    numbers = [segment["n"] for segment in segments]
    expected = list(range(1, len(segments) + 1))
    if numbers == expected:
        return []
    counts = Counter(numbers)
    missing = [n for n in expected if n not in counts]
    repeats = sorted(n for n, times in counts.items() if times > 1)
    return [f"numbering is not 1..{len(segments)} in order"
            + (f"; missing {missing}" if missing else "")
            + (f"; repeated {repeats}" if repeats else "")]


def source_drift_problems(segments: list[dict], sources: dict[int, str]) -> list[str]:
    """Report any `source` that no longer matches the extractor's, keyed by segment number."""
    problems: list[str] = []
    seen = {segment["n"] for segment in segments}
    for segment in segments:
        n = segment["n"]
        if n not in sources:
            problems.append(f"#{n}: not in the skeleton")
        elif segment["source"] != sources[n]:
            problems.append(f"#{n}: `source` differs from the skeleton — it has been edited")
    for n in sorted(set(sources) - seen):
        problems.append(f"#{n}: in the skeleton but missing from the bitext")
    return problems


def proofread_problems(segments: list[dict]) -> list[str]:
    """Report any `suggestion` lacking a `note`, or identical to the draft it replaces."""
    problems: list[str] = []
    for segment in segments:
        if "suggestion" not in segment:
            continue
        if not segment.get("note"):
            problems.append(f"#{segment['n']}: `suggestion` without a `note`")
        if segment["suggestion"] == segment.get("target", ""):
            problems.append(f"#{segment['n']}: `suggestion` is identical to the draft")
    return problems


def untranslated(segments: list[dict]) -> list[int]:
    """Return the numbers of the segments with neither a `target` nor a `note` explaining why."""
    return [segment["n"] for segment in segments
            if not segment.get("target") and not segment.get("note")]
