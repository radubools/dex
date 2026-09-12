"""Playback variants of a generated animation: speed, and step checkpoints.

A GIF plays at the frame delays baked into it, and an `<img>` offers no seek,
so both speed and stepping have to be produced server-side as derived files.
They are cached, because re-encoding a 500-frame animation is not free.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import gifinfo

log = logging.getLogger("dex.animation")

#: Speeds the UI offers, and the bounds anything else is clamped to.
SPEED_CHOICES = [0.25, 0.5, 1.0, 1.5, 2.0, 4.0]
MIN_SPEED, MAX_SPEED = 0.1, 8.0
#: A GIF delay is hundredths of a second, and browsers floor very small values.
MIN_DELAY_CS = 2
#: When a scene has no sections, fall back to this many equal steps.
FALLBACK_STEPS = 6
#: Below this, splitting adds nothing.
MIN_FRAMES_TO_SPLIT = 24


@dataclass(slots=True)
class Checkpoint:
    index: int
    name: str
    start_frame: int
    end_frame: int
    duration: float

    #: Where this section begins, in seconds. A video is seeked and looped by
    #: time; frames are a GIF's unit, not a player's.
    start: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "startFrame": self.start_frame,
            "endFrame": self.end_frame,
            "duration": round(self.duration, 2),
            "start": round(self.start, 3),
            "end": round(self.start + self.duration, 3),
        }


def sections_file(gif: Path) -> Path:
    """Where `render_manim` records the scene's own section boundaries."""
    return gif.with_suffix(".sections.json")


def checkpoints(gif: Path) -> list[Checkpoint]:
    """Steps within an animation.

    Prefers the scene's own sections, which name each step. Falls back to equal
    slices so animations rendered before sections existed can still be stepped
    through, unnamed.
    """
    info = gifinfo.read(gif)
    if info is None or info.frames < MIN_FRAMES_TO_SPLIT:
        return []

    recorded = _recorded_sections(gif, info.frames)
    if recorded:
        return recorded

    step = max(1, info.frames // FALLBACK_STEPS)
    marks: list[Checkpoint] = []
    for index, start in enumerate(range(0, info.frames, step)):
        end = min(start + step, info.frames) - 1
        if end <= start:
            break
        marks.append(
            Checkpoint(
                index=index,
                name=f"Part {index + 1}",
                start_frame=start,
                end_frame=end,
                duration=sum(info.delays[start : end + 1]) / 100,
            )
        )
    return marks


def _recorded_sections(gif: Path, total_frames: int) -> list[Checkpoint]:
    path = sections_file(gif)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list) or not payload:
        return []

    marks: list[Checkpoint] = []
    cursor = 0
    for index, entry in enumerate(payload):
        try:
            count = int(entry.get("nb_frames", 0))
            duration = float(entry.get("duration", 0))
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        start = cursor
        end = min(cursor + count, total_frames) - 1
        cursor += count
        if end < start:
            break
        marks.append(
            Checkpoint(
                index=index,
                name=str(entry.get("name") or f"Part {index + 1}"),
                start_frame=start,
                end_frame=end,
                duration=duration,
            )
        )
    return marks


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def _video_sections(video: Path) -> list[Checkpoint]:
    """The scene's sections as times, straight from manim's own index.

    A video needs no slicing to be stepped through: the player seeks. All that
    is wanted is where each section starts and ends.
    """
    path = sections_file(video)
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    marks: list[Checkpoint] = []
    at = 0.0
    for index, entry in enumerate(recorded if isinstance(recorded, list) else []):
        try:
            duration = float(entry.get("duration", 0.0))
        except (TypeError, ValueError):
            continue
        if duration <= 0:
            continue
        marks.append(
            Checkpoint(
                index=index,
                name=str(entry.get("name") or f"Step {index + 1}"),
                start_frame=0,
                end_frame=0,
                duration=duration,
                start=at,
            )
        )
        at += duration
    return marks


def describe(path: Path) -> dict[str, Any]:
    if is_video(path):
        marks = _video_sections(path)
        return {
            "kind": "video",
            "animation": {"duration": round(sum(m.duration for m in marks), 2)} if marks else None,
            "checkpoints": [m.to_json() for m in marks],
            "named": bool(marks),
            # A video player has its own rate control; no server-side re-encode.
            "speeds": SPEED_CHOICES,
        }

    info = gifinfo.read(path)
    marks = checkpoints(path)
    return {
        "kind": "gif",
        "animation": info.to_json() if info else None,
        "checkpoints": [m.to_json() for m in marks],
        "named": bool(marks) and sections_file(path).exists(),
        "speeds": SPEED_CHOICES,
    }


def variant(gif: Path, cache_dir: Path, *, speed: float = 1.0, segment: int | None = None) -> Path:
    """A re-encoded copy at `speed`, optionally just one checkpoint.

    Returns the source itself when nothing needs changing, so the common case
    costs nothing.
    """
    speed = max(MIN_SPEED, min(MAX_SPEED, float(speed or 1.0)))
    if abs(speed - 1.0) < 0.01 and segment is None:
        return gif

    marks = checkpoints(gif) if segment is not None else []
    mark = next((m for m in marks if m.index == segment), None)
    if segment is not None and mark is None:
        return gif

    info = gifinfo.read(gif)
    if info is None:
        return gif

    base_delay = sorted(info.delays)[len(info.delays) // 2] if info.delays else 10
    delay = max(MIN_DELAY_CS, round(base_delay / speed))

    key = hashlib.sha256(
        f"{gif}|{gif.stat().st_mtime_ns}|{speed}|{segment}|{delay}".encode()
    ).hexdigest()[:16]
    cached = cache_dir / f"{gif.stem}-{key}.gif"
    if cached.exists():
        return cached

    cache_dir.mkdir(parents=True, exist_ok=True)
    scratch = cached.with_suffix(".partial.gif")

    if mark is None:
        payload = gifinfo.retime(gif.read_bytes(), delay)
    else:
        sliced = _slice(gif, mark, cache_dir)
        if sliced is None:
            return gif
        payload = gifinfo.retime(sliced, delay) if delay != base_delay else sliced

    try:
        scratch.write_bytes(payload)
    except OSError:
        log.exception("could not write the variant for %s", gif.name)
        scratch.unlink(missing_ok=True)
        return gif

    # Rename only once complete, so a killed process cannot leave a truncated
    # file that later looks like a valid cache hit.
    scratch.replace(cached)
    return cached


def _slice(gif: Path, mark: Checkpoint, cache_dir: Path) -> bytes | None:
    """Cut one checkpoint out of an animation.

    Uses ffmpeg rather than an image tool: GIF frames are deltas of each other,
    so a slice has to be decoded and re-encoded, and ffmpeg does that an order
    of magnitude faster on the sizes these animations reach.
    """
    if shutil.which("ffmpeg") is None:
        log.warning("ffmpeg is not installed; cannot slice %s", gif.name)
        return None

    target = cache_dir / f"{gif.stem}-slice-{mark.index}.tmp.gif"
    select = f"select='between(n\\,{mark.start_frame}\\,{mark.end_frame})'"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(gif),
            "-vf",
            # A generated palette keeps the colours of the source; the default
            # one visibly banded manim's gradients.
            f"{select},setpts=N/15/TB,split[a][b];[a]palettegen=stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer",
            str(target),
        ],
        capture_output=True, text=True, timeout=300,
    )
    try:
        if result.returncode != 0 or not target.exists():
            log.warning("could not slice %s: %s", gif.name, result.stderr[-300:])
            return None
        return target.read_bytes()
    finally:
        target.unlink(missing_ok=True)
