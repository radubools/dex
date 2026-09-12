#!/usr/bin/env python3
"""Render one manim Scene straight to a GIF at a path you choose.

Manim buries its output under `media/videos/<module>/<quality>/<Scene>.gif`,
which makes it awkward for an agent to place a file deterministically. This
wrapper renders into a scratch media dir, then moves the single produced GIF
to the requested destination and prints that path.

    python -m dex.tools.render_manim animation.py TwoSumBrute -o brute.gif
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

QUALITY = {"low": "-ql", "medium": "-qm", "high": "-qh"}


def render(
    scene_file: Path,
    scene: str,
    out: Path,
    quality: str = "medium",
    fps: int = 15,
    narration: Path | None = None,
) -> Path:
    if shutil.which("ffmpeg") is None:
        # manim shells out to ffmpeg; without it the failure is a confusing
        # traceback from deep inside the renderer.
        raise SystemExit(
            "ffmpeg is not on PATH, and manim needs it to write the GIF.\n"
            "Install it with:  brew install ffmpeg"
        )
    if importlib.util.find_spec("manim") is None:
        raise SystemExit(
            "manim is not installed in this environment. Install it with:\n"
            "  uv pip install -e '.[manim]'\n"
            "(macOS also needs: brew install py3cairo pango pkg-config ffmpeg)"
        )

    with tempfile.TemporaryDirectory(prefix="dex-manim-") as tmp:
        # Run manim as a module of *this* interpreter rather than as whatever
        # `manim` happens to be on PATH — a pyenv shim or another virtualenv can
        # easily be a different version with a different Scene API.
        # The destination's extension decides the format: mp4 when the
        # animation has narration to carry, gif when it is silent.
        fmt = "mp4" if out.suffix.lower() == ".mp4" else "gif"
        cmd = [
            sys.executable, "-m", "manim", "render", QUALITY.get(quality, "-qm"),
            f"--format={fmt}",
            f"--fps={fps}",
            "--disable_caching",
            # Records the scene's own `next_section` boundaries, which dex turns
            # into the checkpoints you can step through in the viewer.
            "--save_sections",
            "--media_dir", tmp,
            str(scene_file), scene,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
            raise SystemExit(f"manim failed for scene {scene} (exit {proc.returncode})")

        # The per-section files live under `sections/`; the whole-scene render
        # is the one outside it, and that is what we want.
        made = [
            p for p in Path(tmp).rglob(f"*.{fmt}")
            if p.parent.name != "sections"
        ]
        made.sort(key=lambda p: p.stat().st_mtime)
        if not made:
            sys.stderr.write(proc.stdout[-4000:])
            raise SystemExit(
                f"manim reported success but produced no {fmt.upper()} for {scene}"
            )

        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made[-1]), out)
        _save_sections(Path(tmp), scene, out)

    if narration is not None:
        _mux(out, narration)
    return out


def _mux(video: Path, narration: Path) -> None:
    """Put the narration inside the video, so the two cannot drift apart.

    A separate audio file has to be kept in sync by whoever plays it; muxed in,
    the pairing survives seeking, looping a section, and being downloaded.
    """
    if video.suffix.lower() != ".mp4":
        raise SystemExit("narration can only be muxed into an .mp4")
    if not narration.exists():
        raise SystemExit(f"no narration at {narration}")
    merged = video.with_name(f"{video.stem}.muxed.mp4")
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video), "-i", str(narration),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac",
            # The track runs as long as the picture; a cue overrunning the last
            # frame would otherwise stretch the file past the animation.
            "-shortest", str(merged),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"ffmpeg could not mux {narration.name} into {video.name}")
    merged.replace(video)


def _save_sections(media: Path, scene: str, out: Path) -> None:
    """Copy the section index next to the GIF, if the scene declared any.

    Manim writes one index per scene describing each `next_section` block. dex
    reads it to offer named checkpoints; without it the viewer falls back to
    equal slices.
    """
    for index in media.rglob(f"{scene}.json"):
        if index.parent.name != "sections":
            continue
        try:
            shutil.copyfile(index, out.with_suffix(".sections.json"))
        except OSError:
            pass
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_file", type=Path)
    parser.add_argument("scene")
    parser.add_argument(
        "-o", "--out", type=Path, required=True,
        help="destination .gif or .mp4 path; the extension chooses the format",
    )
    parser.add_argument(
        "--narration", type=Path, default=None,
        help="audio to mux into an .mp4 render",
    )
    parser.add_argument("-q", "--quality", choices=list(QUALITY), default="medium")
    parser.add_argument("--fps", type=int, default=15)
    args = parser.parse_args()

    path = render(
        args.scene_file.resolve(), args.scene, args.out.resolve(),
        args.quality, args.fps,
        args.narration.resolve() if args.narration else None,
    )
    print(path)


if __name__ == "__main__":
    main()
