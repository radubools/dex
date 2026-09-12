import subprocess
from pathlib import Path

import pytest

from dex import gifinfo


@pytest.fixture(scope="module")
def gif(tmp_path_factory) -> Path:
    """A real three-frame GIF with a known 0.5s-per-frame delay."""
    out = tmp_path_factory.mktemp("gif") / "probe.gif"
    made = subprocess.run(
        ["magick", "-delay", "50", "-size", "40x20",
         "xc:red", "xc:green", "xc:blue", "-loop", "0", str(out)],
        capture_output=True,
    )
    if made.returncode != 0 or not out.exists():
        pytest.skip("ImageMagick is not available to build a fixture")
    return out


def test_reads_frames_and_duration(gif: Path):
    info = gifinfo.read(gif)
    assert info is not None
    assert info.frames == 3
    assert info.duration == pytest.approx(1.5, abs=0.05)
    assert (info.width, info.height) == (40, 20)


def test_json_shape(gif: Path):
    payload = gifinfo.read(gif).to_json()
    assert set(payload) == {"frames", "duration", "width", "height"}


def test_non_gifs_and_missing_files_return_none(tmp_path: Path):
    assert gifinfo.read(tmp_path / "absent.gif") is None
    plain = tmp_path / "not.gif"
    plain.write_bytes(b"this is not a gif")
    assert gifinfo.read(plain) is None


def test_browser_delay_clamping_matches_reality(tmp_path):
    """0 and 1 centiseconds render at ~10fps; 2 and above are honoured."""
    from dex.gifinfo import _effective

    assert _effective(0) == 10
    assert _effective(1) == 10
    assert _effective(2) == 2
    assert _effective(5) == 5
    assert _effective(50) == 50


def test_retime_changes_duration_without_touching_frames(gif):
    from dex import gifinfo

    original = gifinfo.read(gif)
    faster = gifinfo.read_bytes_info(gifinfo.retime(gif.read_bytes(), 25))
    assert faster.frames == original.frames
    assert faster.duration == pytest.approx(original.frames * 0.25, abs=0.01)

    # And a delay the browser would clamp is reported the way it will render.
    clamped = gifinfo.read_bytes_info(gifinfo.retime(gif.read_bytes(), 1))
    assert clamped.duration == pytest.approx(original.frames * 0.10, abs=0.01)


def test_retime_leaves_a_non_gif_alone():
    from dex import gifinfo

    assert gifinfo.retime(b"not a gif", 5) == b"not a gif"
