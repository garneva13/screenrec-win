"""Tests for ``ui/thumbnails.py``.

No GPU, no ffmpeg, no recording and no visible window. The loader takes its grab function
as an argument precisely so that its interesting parts - the worker thread, the cache, the
failure path and `stop()` - can be exercised with a fake that returns bytes from memory.

What the fakes stand in for, so the next reader knows what is *not* covered here: whether
ddagrab actually produces a frame on this machine. That was checked by hand against a real
screen and a real window; see the handover notes. Everything below is the module's own
logic.

The filter-chain tests use `winapi.Monitor` values copied from the measured machine in
``docs/CONTRACT.md``: DXGI 0 is 2560x1440 unrotated, DXGI 1 is 1440x2560 rotated 90.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import winapi  # noqa: E402
from screenrec.ui import thumbnails  # noqa: E402

MONITOR_0 = winapi.Monitor(
    index=0,
    device_name=r"\\.\DISPLAY2",
    x=0,
    y=0,
    width=2560,
    height=1440,
    rotation=1,
    adapter="NVIDIA GeForce RTX 3070 Ti",
)

MONITOR_1_ROTATED = winapi.Monitor(
    index=1,
    device_name=r"\\.\DISPLAY1",
    x=2560,
    y=0,
    width=1440,
    height=2560,
    rotation=2,
    adapter="NVIDIA GeForce RTX 3070 Ti",
)

SMALL_MONITOR = winapi.Monitor(
    index=0,
    device_name=r"\\.\DISPLAY9",
    x=0,
    y=0,
    width=160,
    height=120,
    rotation=1,
    adapter="test",
)


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if isinstance(existing, QtWidgets.QApplication):
        return existing
    return QtWidgets.QApplication(sys.argv[:1])


def _png_bytes(width: int = 48, height: int = 32) -> bytes:
    """A real PNG, made by Qt, so the decode path under test is the real one."""
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("#8B5CF6"))
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt could not write a PNG for the test fixture.")
    buffer.close()
    return bytes(buffer.data())


def _pump(predicate, timeout: float = 5.0) -> bool:
    """Spin the event loop until `predicate` is true. Queued signals need an event loop."""
    app = _app()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
    return bool(predicate())


class Recorded:
    """Collects `ready` emissions and counts how often the fake grab was called."""

    def __init__(self) -> None:
        self.results: list[tuple[str, object]] = []
        self.calls: list[thumbnails.ThumbRequest] = []

    def on_ready(self, key: str, thumb: object) -> None:
        self.results.append((key, thumb))

    def keys(self) -> list[str]:
        return [key for key, _ in self.results]


class FilterChainTests(unittest.TestCase):
    """The geometry must come from the recorder, not from this module."""

    def test_unrotated_monitor_downloads_and_scales(self) -> None:
        from screenrec import recorder

        source = recorder.VideoSource(monitor=MONITOR_0)
        chain = thumbnails._filter_chain(source, 240)
        self.assertEqual(chain, "hwdownload,format=bgra,format=bgra,scale=240:-2")

    def test_rotated_monitor_transposes_before_scaling(self) -> None:
        from screenrec import recorder

        source = recorder.VideoSource(monitor=MONITOR_1_ROTATED)
        chain = thumbnails._filter_chain(source, 240)
        self.assertEqual(
            chain, "hwdownload,format=bgra,transpose=1,format=bgra,scale=240:-2"
        )
        # The transpose has to come first, or the width given to scale is the wrong axis.
        self.assertLess(chain.index("transpose=1"), chain.index("scale="))

    def test_small_source_is_not_enlarged(self) -> None:
        from screenrec import recorder

        source = recorder.VideoSource(monitor=SMALL_MONITOR)
        chain = thumbnails._filter_chain(source, 240)
        self.assertNotIn("scale=", chain)

    def test_ddagrab_args_come_from_the_recorder(self) -> None:
        from screenrec import recorder

        source = recorder.VideoSource(monitor=MONITOR_1_ROTATED)
        args = source.ddagrab_args(thumbnails.GRAB_FRAMERATE, draw_mouse=False)
        self.assertEqual(args, "ddagrab=output_idx=1:framerate=30:draw_mouse=0")


class SourceTests(unittest.TestCase):
    def test_monitor_and_window_together_is_refused(self) -> None:
        info = winapi.WindowInfo(
            hwnd=1, title="Both", process="x.exe", x=0, y=0, width=100, height=100
        )
        source, problem = thumbnails._source_for(
            thumbnails.ThumbRequest(key="k", monitor=MONITOR_0, window=info)
        )
        self.assertIsNone(source)
        self.assertIn("one or the other", problem)

    def test_neither_is_refused(self) -> None:
        source, problem = thumbnails._source_for(thumbnails.ThumbRequest(key="k"))
        self.assertIsNone(source)
        self.assertIn("neither a monitor nor a window", problem)

    def test_dead_window_is_refused_in_words(self) -> None:
        # hwnd 0 is never a window, so IsWindow says no without touching anything real.
        info = winapi.WindowInfo(
            hwnd=0, title="Gone", process="x.exe", x=0, y=0, width=800, height=600
        )
        source, problem = thumbnails._source_for(
            thumbnails.ThumbRequest(key="k", window=info)
        )
        self.assertIsNone(source)
        self.assertIn("has closed", problem)
        self.assertIn("Gone", problem)
        self.assertEqual(thumbnails.last_problem(), problem)


class MessageTests(unittest.TestCase):
    """ffmpeg's complaint is condensed to one readable line for the card."""

    def test_the_filter_tag_and_the_repeats_are_dropped(self) -> None:
        # Copied verbatim from a real failed grab of DXGI output 9 on this machine.
        dump = (
            "[Parsed_ddagrab_0 @ 000002630f85fac0] Failed to enumerate DXGI output 9\n"
            "[Parsed_ddagrab_0 @ 000002630f85fac0] Failed to configure output pad\n"
            "[in#0 @ 000002630f7d2bc0] Error opening input: Generic error\n"
            "Error opening input files: Generic error in an external library"
        )
        self.assertEqual(
            thumbnails._first_line(dump), "Failed to enumerate DXGI output 9"
        )

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(thumbnails._first_line(""), "")
        self.assertEqual(thumbnails._first_line("\n  \n"), "")

    def test_an_untagged_line_survives_whole(self) -> None:
        self.assertEqual(thumbnails._first_line("No such file [x]"), "No such file [x]")


class DecodeTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()

    def test_good_png_becomes_a_thumb(self) -> None:
        thumb, problem = thumbnails._thumb_from_png(_png_bytes(64, 40))
        self.assertEqual(problem, "")
        self.assertIsNotNone(thumb)
        assert thumb is not None
        self.assertEqual((thumb.width, thumb.height), (64, 40))
        self.assertFalse(thumb.pixmap.isNull())

    def test_rubbish_is_reported_not_swallowed(self) -> None:
        thumb, problem = thumbnails._thumb_from_png(b"not a png at all")
        self.assertIsNone(thumb)
        self.assertIn("could not be decoded", problem)


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.recorded = Recorded()
        self.loaders: list[thumbnails.ThumbnailLoader] = []

    def tearDown(self) -> None:
        for loader in self.loaders:
            loader.stop()

    def _loader(self, grab) -> thumbnails.ThumbnailLoader:
        loader = thumbnails.ThumbnailLoader(grab=grab)
        loader.ready.connect(self.recorded.on_ready)
        self.loaders.append(loader)
        return loader

    def test_a_grab_comes_back_as_a_thumb_on_the_gui_thread(self) -> None:
        data = _png_bytes(80, 50)

        def grab(request):
            self.recorded.calls.append(request)
            return data, ""

        loader = self._loader(grab)
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1), "no ready signal")

        key, thumb = self.recorded.results[0]
        self.assertEqual(key, "mon:0")
        self.assertIsInstance(thumb, thumbnails.Thumb)
        assert isinstance(thumb, thumbnails.Thumb)
        self.assertEqual((thumb.width, thumb.height), (80, 50))
        self.assertEqual(len(self.recorded.calls), 1)
        self.assertEqual(self.recorded.calls[0].width, thumbnails.DEFAULT_WIDTH)
        self.assertEqual(loader.problems, {})

    def test_the_cache_answers_without_grabbing_again(self) -> None:
        data = _png_bytes()

        def grab(request):
            self.recorded.calls.append(request)
            return data, ""

        loader = self._loader(grab)
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))

        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(
            _pump(lambda: len(self.recorded.results) == 2), "the cache did not answer"
        )

        # The point of the test: one grab, two answers, both through `ready`.
        self.assertEqual(len(self.recorded.calls), 1)
        self.assertEqual(self.recorded.keys(), ["mon:0", "mon:0"])
        first, second = self.recorded.results[0][1], self.recorded.results[1][1]
        self.assertIs(first, second)
        self.assertIs(loader.cached("mon:0"), first)

    def test_a_cache_hit_is_not_emitted_inside_request(self) -> None:
        """One code path for the caller: the answer always arrives from the event loop."""
        data = _png_bytes()
        loader = self._loader(lambda request: (data, ""))
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))

        self.recorded.results.clear()
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertEqual(self.recorded.results, [], "the cached thumb arrived re-entrantly")
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))

    def test_a_failing_grab_emits_none_and_says_why(self) -> None:
        def grab(request):
            self.recorded.calls.append(request)
            return b"", "ddagrab refused output 7."

        loader = self._loader(grab)
        loader.request("mon:7", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))

        self.assertEqual(self.recorded.results[0], ("mon:7", None))
        self.assertEqual(loader.problems["mon:7"], "ddagrab refused output 7.")
        self.assertEqual(loader.last_problem, "ddagrab refused output 7.")
        self.assertIsNone(loader.cached("mon:7"))

    def test_a_failure_is_not_cached_so_a_retry_grabs_again(self) -> None:
        attempts: list[str] = []
        data = _png_bytes()

        def grab(request):
            attempts.append(request.key)
            if len(attempts) == 1:
                return b"", "The window was closing."
            return data, ""

        loader = self._loader(grab)
        loader.request("win:1", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))
        self.assertIsNone(self.recorded.results[0][1])

        loader.request("win:1", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 2))
        self.assertEqual(len(attempts), 2)
        self.assertIsInstance(self.recorded.results[1][1], thumbnails.Thumb)
        self.assertNotIn("win:1", loader.problems)

    def test_undecodable_bytes_report_rather_than_crash(self) -> None:
        loader = self._loader(lambda request: (b"certainly not a png", ""))
        loader.request("bad", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))
        self.assertEqual(self.recorded.results[0], ("bad", None))
        self.assertIn("could not be decoded", loader.problems["bad"])

    def test_a_raising_grab_does_not_kill_the_worker(self) -> None:
        data = _png_bytes()

        def grab(request):
            if request.key == "boom":
                raise RuntimeError("the fake grab exploded")
            return data, ""

        loader = self._loader(grab)
        loader.request("boom", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))
        self.assertIsNone(self.recorded.results[0][1])
        self.assertIn("RuntimeError", loader.problems["boom"])

        loader.request("fine", monitor=MONITOR_0)
        self.assertTrue(
            _pump(lambda: len(self.recorded.results) == 2), "the worker thread died"
        )
        self.assertIsInstance(self.recorded.results[1][1], thumbnails.Thumb)

    def test_several_keys_all_come_back(self) -> None:
        data = _png_bytes()
        loader = self._loader(lambda request: (data, ""))
        for index in range(5):
            loader.request(f"mon:{index}", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 5))
        self.assertEqual(
            sorted(self.recorded.keys()), ["mon:0", "mon:1", "mon:2", "mon:3", "mon:4"]
        )

    def test_a_repeated_request_while_in_flight_is_not_grabbed_twice(self) -> None:
        release = threading.Event()
        data = _png_bytes()

        def grab(request):
            self.recorded.calls.append(request)
            release.wait(5.0)
            return data, ""

        loader = self._loader(grab)
        loader.request("mon:0", monitor=MONITOR_0)
        loader.request("mon:0", monitor=MONITOR_0)
        release.set()
        self.assertTrue(_pump(lambda: len(self.recorded.results) >= 1))
        # Give a second answer a chance to turn up before claiming there is not one.
        _pump(lambda: len(self.recorded.results) >= 2, timeout=0.5)
        self.assertEqual(len(self.recorded.calls), 1)
        self.assertEqual(len(self.recorded.results), 1)

    def test_stop_is_safe_when_nothing_was_ever_requested(self) -> None:
        loader = self._loader(lambda request: (b"", "unused"))
        loader.stop()
        loader.stop()  # and twice

    def test_stop_joins_the_worker_and_is_safe_twice(self) -> None:
        data = _png_bytes()
        loader = self._loader(lambda request: (data, ""))
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))

        loader.stop()
        self.assertIsNone(loader._thread, "stop() did not join the worker")
        loader.stop()

    def test_a_request_after_stop_is_refused_in_words(self) -> None:
        loader = self._loader(lambda request: (_png_bytes(), ""))
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))
        loader.stop()

        loader.request("mon:1", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 2))
        self.assertEqual(self.recorded.results[1], ("mon:1", None))
        self.assertIn("stopped", loader.problems["mon:1"])

    def test_the_cache_still_answers_after_stop(self) -> None:
        data = _png_bytes()
        loader = self._loader(lambda request: (data, ""))
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))
        loader.stop()

        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 2))
        self.assertIsInstance(self.recorded.results[1][1], thumbnails.Thumb)

    def test_clear_forgets_everything(self) -> None:
        data = _png_bytes()
        loader = self._loader(lambda request: (data, ""))
        loader.request("mon:0", monitor=MONITOR_0)
        self.assertTrue(_pump(lambda: len(self.recorded.results) == 1))
        loader.clear()
        self.assertIsNone(loader.cached("mon:0"))
        self.assertEqual(loader.problems, {})


if __name__ == "__main__":
    unittest.main()
