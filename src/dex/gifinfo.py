"""Read what dex needs to know about a GIF without decoding it.

Only the timing matters here: how long the animation runs, so the feed can hold
a reel for as long as its animation actually plays instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Browsers rewrite a frame delay of 0 or 1 hundredths to 10 (about 10fps);
#: anything from 2 up is honoured. Clamping more than that would misreport the
#: duration of any animation faster than 10fps.
CLAMPED_BELOW_CS = 2
CLAMPED_TO_CS = 10


def _effective(delay: int) -> int:
    return CLAMPED_TO_CS if delay < CLAMPED_BELOW_CS else delay


@dataclass(slots=True)
class GifInfo:
    frames: int
    #: Seconds for one pass through the animation.
    duration: float
    width: int
    height: int
    #: Per-frame delay in hundredths of a second, in order.
    delays: list[int] = field(default_factory=list)

    def to_json(self) -> dict[str, float | int]:
        return {
            "frames": self.frames,
            "duration": round(self.duration, 2),
            "width": self.width,
            "height": self.height,
        }


def read(path: Path) -> GifInfo | None:
    """Frame count and total duration, or None if this is not a readable GIF."""
    try:
        return read_bytes_info(path.read_bytes())
    except OSError:
        return None


def read_bytes_info(data: bytes) -> GifInfo | None:
    """The same, for bytes already in hand."""
    if len(data) < 13 or data[:3] != b"GIF":
        return None

    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    packed = data[10]
    cursor = 13
    if packed & 0x80:  # global colour table present
        cursor += 3 * (2 ** ((packed & 0x07) + 1))

    frames = 0
    total_cs = 0
    pending_delay = 0
    delays: list[int] = []

    while cursor < len(data):
        block = data[cursor]
        if block == 0x3B:  # trailer
            break
        if block == 0x21:  # extension
            label = data[cursor + 1] if cursor + 1 < len(data) else 0
            cursor += 2
            if label == 0xF9 and cursor < len(data):  # graphic control
                size = data[cursor]
                if size >= 4:
                    pending_delay = int.from_bytes(data[cursor + 2 : cursor + 4], "little")
            cursor = _skip_sub_blocks(data, cursor)
            continue
        if block == 0x2C:  # image descriptor: one frame
            frames += 1
            delay = _effective(pending_delay)
            delays.append(delay)
            total_cs += delay
            pending_delay = 0
            local = data[cursor + 9] if cursor + 9 < len(data) else 0
            cursor += 10
            if local & 0x80:
                cursor += 3 * (2 ** ((local & 0x07) + 1))
            cursor += 1  # LZW minimum code size
            cursor = _skip_sub_blocks(data, cursor)
            continue
        # Anything unexpected means the file is not shaped as expected; stop
        # rather than walking off into the data.
        break

    if frames == 0:
        return None
    return GifInfo(
        frames=frames, duration=total_cs / 100, width=width, height=height, delays=delays
    )


def _skip_sub_blocks(data: bytes, cursor: int) -> int:
    """Walk past a chain of length-prefixed sub-blocks to the terminator."""
    while cursor < len(data):
        size = data[cursor]
        cursor += 1
        if size == 0:
            return cursor
        cursor += size
    return cursor


def retime(data: bytes, delay_cs: int) -> bytes:
    """Rewrite every frame delay, leaving the image data untouched.

    Changing playback speed is a change to the Graphic Control Extension blocks
    only, so it needs no decode: re-encoding a 500-frame animation through an
    image tool takes over a minute, this takes milliseconds.
    """
    if len(data) < 13 or data[:3] != b"GIF":
        return data

    out = bytearray(data)
    delay = max(0, min(0xFFFF, int(delay_cs)))
    packed = out[10]
    cursor = 13
    if packed & 0x80:
        cursor += 3 * (2 ** ((packed & 0x07) + 1))

    while cursor < len(out):
        block = out[cursor]
        if block == 0x3B:
            break
        if block == 0x21:
            label = out[cursor + 1] if cursor + 1 < len(out) else 0
            cursor += 2
            if label == 0xF9 and cursor + 4 < len(out) and out[cursor] >= 4:
                out[cursor + 2 : cursor + 4] = delay.to_bytes(2, "little")
            cursor = _skip_sub_blocks(out, cursor)
            continue
        if block == 0x2C:
            local = out[cursor + 9] if cursor + 9 < len(out) else 0
            cursor += 10
            if local & 0x80:
                cursor += 3 * (2 ** ((local & 0x07) + 1))
            cursor += 1
            cursor = _skip_sub_blocks(out, cursor)
            continue
        break

    return bytes(out)
