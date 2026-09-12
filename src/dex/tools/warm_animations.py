#!/usr/bin/env python3
"""Pre-build the checkpoint slices for every animation in a project.

Slices are produced on demand and cached, so the first press of a step control
pays for the cut. Running this once after a batch of tasks means it never does.

    python -m dex.tools.warm_animations                 # every project
    python -m dex.tools.warm_animations --speed 2       # also warm a speed
    python -m dex.tools.warm_animations --dry-run
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ..animation import checkpoints, variant
from ..config import CONFIG


def human(size: float) -> str:
    return f"{size / 1e6:.0f} MB" if size >= 1e6 else f"{size / 1e3:.0f} KB"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets", type=Path, default=CONFIG.assets_dir,
                        help="project directory to walk (default: the configured one)")
    parser.add_argument("--cache", type=Path, default=CONFIG.animation_cache)
    parser.add_argument("--speed", type=float, action="append", default=None,
                        help="also warm this playback speed; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="report the work, build nothing")
    args = parser.parse_args()

    speeds = args.speed or [1.0]
    animations = sorted(args.assets.glob("*/*.gif"))
    if not animations:
        print(f"no animations under {args.assets}")
        return

    planned = 0
    for gif in animations:
        planned += len(checkpoints(gif)) * len(speeds)
    print(f"{len(animations)} animations, {planned} slices to build at {speeds}\n")
    if args.dry_run:
        return

    built = skipped = failed = 0
    written = 0
    started = time.time()

    for gif in animations:
        marks = checkpoints(gif)
        if not marks:
            print(f"  {gif.parent.name}/{gif.name}: no checkpoints")
            continue
        named = "named" if gif.with_suffix(".sections.json").exists() else "auto"
        print(f"  {gif.parent.name}/{gif.name} ({len(marks)} {named} steps)", flush=True)

        for speed in speeds:
            for mark in marks:
                try:
                    out = variant(gif, args.cache, speed=speed, segment=mark.index)
                except Exception as exc:  # one bad animation must not stop the sweep
                    print(f"      step {mark.index}: FAILED ({type(exc).__name__}: {exc})")
                    failed += 1
                    continue
                if out == gif:
                    skipped += 1
                    continue
                built += 1
                written += out.stat().st_size

    print(
        f"\n{built} built, {skipped} skipped, {failed} failed — "
        f"{human(written)} in {time.time() - started:.0f}s\n"
        f"cache: {args.cache}"
    )


if __name__ == "__main__":
    main()
