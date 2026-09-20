"""What counts as a generated artifact, and what is scratch lying beside it.

Split out of `watcher.py` so the runner can ask the same question without
importing it: the watcher reaches the queue, and the queue reaches the runner,
so a runner that imported the watcher would close that loop.
"""

from __future__ import annotations

from pathlib import Path

#: Manim and pytest churn through scratch files; those are not artifacts.
IGNORED_PARTS = {
    "media", "__pycache__", ".pytest_cache", "partial_movie_files", ".git",
    # Per-cue narration audio, joined into one track before it is delivered.
    ".cues",
}
IGNORED_SUFFIXES = {".pyc", ".tmp", ".part", ".swp"}

KINDS = {
    ".py": "code", ".md": "markdown", ".json": "manifest",
    ".gif": "animation", ".png": "image", ".mp4": "video", ".svg": "image",
    # Narration and its captions, produced alongside an animation.
    ".m4a": "audio", ".vtt": "captions",
    # Music projects: a score, what it sounds like, and the page that plays it.
    ".mid": "midi", ".midi": "midi",
    ".wav": "audio", ".mp3": "audio", ".ogg": "audio", ".flac": "audio",
    ".html": "page", ".css": "code", ".js": "code", ".ts": "code",
    ".csv": "data", ".tsv": "data",
}


def is_artifact(path: Path) -> bool:
    if any(part in IGNORED_PARTS for part in path.parts):
        return False
    if path.suffix in IGNORED_SUFFIXES or path.name.startswith("."):
        return False
    return path.suffix in KINDS
