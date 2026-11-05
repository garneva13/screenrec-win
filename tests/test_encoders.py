"""The 4:4:4 table in `encoders.py`, re-asked of the ffmpeg that is actually bundled.

Everything else about `encoders` is string-building and is tested in
`tests/test_recorder.py`. This file exists for the one thing in that module that is a
**measurement of a particular binary** rather than a decision: which encoders really
advertise `yuv444p`, which is what `YUV444_ENCODERS` records and what the 4:4:4 profile
is offered or withheld on.

A comment cannot be checked, and one cell of it was wrong for a week - `av1_qsv` was
written down as `nv12 qsv`, which is `h264_qsv`'s answer; its own is `nv12 p010le qsv`.
Nothing in the program behaved differently, which is exactly why nothing caught it.

These tests run the bundled ffmpeg (``-h encoder=NAME``, no encoding, no GPU) and skip
when there is none. They pin this build's answer on purpose: a different build that
answers differently is a measurement that has to be taken again, not a test to loosen.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from screenrec import encoders, paths  # noqa: E402

CREATE_NO_WINDOW = 0x08000000

_advertised: dict[str, frozenset[str]] = {}
_problem = ""


def _formats(name: str) -> frozenset[str]:
    """The pixel formats this build says `name` takes, as a set. Cached per process."""
    global _problem
    if name in _advertised:
        return _advertised[name]
    try:
        ffmpeg = paths.ffmpeg_exe()
    except paths.BinaryNotFound as exc:  # pragma: no cover - depends on the checkout
        _problem = str(exc)
        return frozenset()
    try:
        result = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-h", f"encoder={name}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        _problem = f"ffmpeg could not be run: {exc}"
        return frozenset()
    found: frozenset[str] = frozenset()
    for line in (result.stdout or "").splitlines():
        if "Supported pixel formats:" in line:
            found = frozenset(line.split(":", 1)[1].split())
            break
    _advertised[name] = found
    return found


class AdvertisedFormatTest(unittest.TestCase):
    """`YUV444_ENCODERS` against the binary it was measured on."""

    def _formats_or_skip(self, name: str) -> frozenset[str]:
        found = _formats(name)
        if not found:
            self.skipTest(_problem or f"this build does not have {name}")
        return found

    def test_every_encoder_that_is_offered_444_advertises_yuv444p(self) -> None:
        for name in sorted(encoders.YUV444_ENCODERS):
            with self.subTest(name):
                self.assertIn("yuv444p", self._formats_or_skip(name))

    def test_no_other_encoder_advertises_yuv444p(self) -> None:
        """The half that matters on a machine nobody here owns. `frame_path` builds
        `...,format=yuv444p`, so an encoder outside the set being offered the 4:4:4
        profile would be handed a format it never listed."""
        for name in sorted(encoders.FAMILIES):
            if name in encoders.YUV444_ENCODERS:
                continue
            with self.subTest(name):
                self.assertNotIn("yuv444p", self._formats_or_skip(name))

    def test_the_three_qsv_encoders_do_not_share_one_answer(self) -> None:
        """The cell that was wrong: `av1_qsv` was filed under `h264_qsv`'s list."""
        h264 = self._formats_or_skip("h264_qsv")
        av1 = self._formats_or_skip("av1_qsv")
        self.assertNotEqual(h264, av1)
        self.assertIn("p010le", av1)
        self.assertNotIn("p010le", h264)

    def test_only_hevc_qsv_takes_the_format_ddagrab_produces(self) -> None:
        """What `frame_path`'s `vpp_qsv=format=nv12` rests on: `ddagrab`'s frames are
        bgra, and of the three QSV encoders only `hevc_qsv` lists bgra."""
        self.assertIn("bgra", self._formats_or_skip("hevc_qsv"))
        self.assertNotIn("bgra", self._formats_or_skip("h264_qsv"))
        self.assertNotIn("bgra", self._formats_or_skip("av1_qsv"))

    def test_media_foundation_lists_the_format_it_will_not_encode(self) -> None:
        """`MF_PIXEL_FORMAT`'s whole argument: `yuv420p` is advertised and silently
        writes no packets, which is why the probe feeds Media Foundation nv12."""
        for name in ("h264_mf", "hevc_mf"):
            with self.subTest(name):
                found = self._formats_or_skip(name)
                self.assertIn("yuv420p", found)
                self.assertIn(encoders.MF_PIXEL_FORMAT, found)


if __name__ == "__main__":
    unittest.main()
