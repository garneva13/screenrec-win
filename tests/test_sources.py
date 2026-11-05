"""Checks for `screenrec.ui.sources`.

Everything here runs under the offscreen platform plugin with fake monitors, fake windows
and a stub thumbnail loader, so no DXGI call, no ffmpeg grab and no visible window is
involved.

Two things are worth saying about how the checks are written:

* **A raising `paintEvent` does not raise out of `grab()`.** PySide6 reports an exception
  escaping a virtual method through `sys.excepthook` and lets the paint finish, so a test
  that merely calls `grab()` inside `assertNoRaise` would pass over a broken card. Every
  grab below therefore runs inside `collecting_exceptions`, which swaps `sys.excepthook`
  and fails on anything it catches.
* **The thumbnail loader is injected through `sys.modules`.** `sources` imports
  `ui.thumbnails` late and by name, which is exactly the seam a stub needs; the missing
  module case is forced by putting `None` under that name, which makes the import raise
  `ImportError` deterministically whether or not the real module exists yet.
"""
from __future__ import annotations

import contextlib
import os
import sys
import unittest
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from screenrec import config, i18n, winapi  # noqa: E402
from screenrec.ui import sources, theme  # noqa: E402

THUMBNAILS = "screenrec.ui.thumbnails"


# ------------------------------------------------------------------ fixtures


MONITORS = [
    winapi.Monitor(
        index=0, device_name=r"\\.\DISPLAY2", x=0, y=0, width=2560, height=1440,
        rotation=0, adapter="NVIDIA RTX 3070 Ti",
    ),
    winapi.Monitor(
        index=1, device_name=r"\\.\DISPLAY1", x=2560, y=0, width=1440, height=2560,
        rotation=2, adapter="NVIDIA RTX 3070 Ti",
    ),
]

WINDOWS = [
    winapi.WindowInfo(
        hwnd=1001, title="Untitled - Notepad", process="notepad.exe",
        x=100, y=100, width=900, height=640,
    ),
    winapi.WindowInfo(
        hwnd=1002, title="ScreenRec — docs", process="Code.exe",
        x=300, y=160, width=1400, height=900,
    ),
    winapi.WindowInfo(
        hwnd=1003, title="Calculator", process="Calculator.exe",
        x=0, y=0, width=340, height=520,
    ),
]

DEAD_HWND = 1003


class StubThumb:
    """The shape `ui.thumbnails.Thumb` promises, and nothing more."""

    def __init__(self, pixmap: QtGui.QPixmap) -> None:
        self.pixmap = pixmap
        self.width = pixmap.width()
        self.height = pixmap.height()



def monitor_cards(picker) -> list:
    """The per-screen cards, without the "All screens" one that now leads the page.

    That card stands for every monitor at once and has no monitor of its own, so every
    test that is about a particular screen wants the rest of the list.
    """
    return [c for c in picker.screens.cards() if c.key != "all:screens"]


def all_screens_card(picker):
    return next((c for c in picker.screens.cards() if c.key == "all:screens"), None)


class StubLoader(QtCore.QObject):
    """Records requests; hands pictures back only when the test says to."""

    ready = QtCore.Signal(str, object)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.requests: list[tuple[str, object, object, int]] = []
        self.stopped = False

    def request(self, key, monitor=None, window=None, width=240) -> None:
        self.requests.append((key, monitor, window, width))

    def stop(self) -> None:
        self.stopped = True


class StubModule:
    """A stand-in for the `ui.thumbnails` module object."""

    Thumb = StubThumb
    ThumbnailLoader = StubLoader


@contextlib.contextmanager
def collecting_exceptions(case: unittest.TestCase, what: str):
    """Fail the test on any exception PySide6 swallows into `sys.excepthook`."""
    caught: list[tuple] = []
    original = sys.excepthook

    def hook(kind, value, traceback):
        caught.append((kind, value))
        original(kind, value, traceback)

    sys.excepthook = hook
    try:
        yield caught
    finally:
        sys.excepthook = original
    if caught:
        case.fail(f"{what}: {caught[0][0].__name__}: {caught[0][1]}")


# --------------------------------------------------------------------- tests


class SourcesTestCase(unittest.TestCase):
    """One QApplication for the module; one picker per test."""

    app: QtWidgets.QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        theme.apply(cls.app)

    def setUp(self) -> None:
        # Several checks below quote the English wording. The language is process-wide,
        # so it is pinned here and put back afterwards rather than inherited from
        # whatever ran before; `LanguageTests` covers the Russian.
        i18n.set_language(i18n.LANG_EN)
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        self.alive_hwnds = {w.hwnd for w in WINDOWS} - {DEAD_HWND}
        self._patch(winapi, "list_monitors", lambda: list(MONITORS))
        self._patch(winapi, "list_windows", lambda min_size=80: list(WINDOWS))
        self._patch(
            winapi, "window_is_alive", lambda hwnd: hwnd in self.alive_hwnds
        )
        self._install_thumbnails(StubModule())
        self.settings = config.Settings()

    def _patch(self, module, name: str, value) -> None:
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def _install_thumbnails(self, module) -> None:
        import screenrec.ui as ui_package

        previous = sys.modules.get(THUMBNAILS, "<absent>")
        had_attr = hasattr(ui_package, "thumbnails")
        previous_attr = getattr(ui_package, "thumbnails", None)

        def restore() -> None:
            if previous == "<absent>":
                sys.modules.pop(THUMBNAILS, None)
            else:
                sys.modules[THUMBNAILS] = previous
            if had_attr:
                ui_package.thumbnails = previous_attr
            else:
                try:
                    del ui_package.thumbnails
                except AttributeError:
                    pass  # another test already removed it; nothing to undo

        self.addCleanup(restore)
        sys.modules[THUMBNAILS] = module
        if module is not None:
            ui_package.thumbnails = module

    def make_picker(self, settings: config.Settings | None = None) -> sources.SourcePicker:
        picker = sources.SourcePicker(settings or self.settings)
        self.addCleanup(picker.deleteLater)
        self.addCleanup(picker.hide)
        return picker

    # -- content ---------------------------------------------------------

    def test_screen_segment_lists_every_monitor(self) -> None:
        picker = self.make_picker()
        cards = monitor_cards(picker)
        self.assertEqual([c.payload for c in cards], [0, 1])
        self.assertEqual(cards[0]._name, "Screen 1")
        self.assertEqual(cards[0]._detail, "2560 × 1440 · primary")
        self.assertEqual(cards[0].icon_kind, "monitor")
        self.assertEqual(cards[1]._detail, "1440 × 2560 · rotated")
        self.assertEqual(cards[1].icon_kind, "monitor_rotated")

    def test_window_segment_lists_every_window(self) -> None:
        picker = self.make_picker()
        cards = picker.windows.cards()
        self.assertEqual([c.payload for c in cards], [1001, 1002, 1003])
        self.assertEqual(cards[0]._name, "Untitled - Notepad")
        self.assertEqual(cards[0]._detail, "notepad.exe · 900 × 640")
        self.assertTrue(all(c.icon_kind == "window" for c in cards))

    def test_region_segment_shows_the_remembered_rectangle(self) -> None:
        self.settings.source_kind = config.SOURCE_REGION
        self.settings.region = [320, 180, 1280, 720]
        picker = self.make_picker()
        self.assertEqual(picker.segments.current, config.SOURCE_REGION)
        self.assertTrue(picker.region.reuse_button.isVisibleTo(picker))
        self.assertIn("1280 × 720 at 320, 180", picker.region._remembered.text())

    def test_region_segment_hides_reuse_when_nothing_is_remembered(self) -> None:
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_REGION)
        self.assertEqual(picker.region._remembered.text(), "")

    def test_settings_mark_the_current_card(self) -> None:
        self.settings.source_kind = config.SOURCE_MONITOR
        self.settings.monitor_index = 1
        picker = self.make_picker()
        self.assertEqual(
            [c.is_selected() for c in monitor_cards(picker)], [False, True]
        )
        self.assertTrue(all(not c.is_selected() for c in picker.windows.cards()))

    def test_window_selection_follows_the_remembered_title(self) -> None:
        self.settings.source_kind = config.SOURCE_WINDOW
        self.settings.window_title = "Calculator"
        picker = self.make_picker()
        selected = [c.payload for c in picker.windows.cards() if c.is_selected()]
        self.assertEqual(selected, [1003])

    def test_width_is_460_and_height_is_capped(self) -> None:
        picker = self.make_picker()
        self.assertEqual(picker.width(), sources.WIDTH)
        for mode in (
            config.SOURCE_MONITOR, config.SOURCE_REGION, config.SOURCE_WINDOW
        ):
            picker.set_mode(mode)
            self.assertLessEqual(picker.height(), sources.MAX_HEIGHT, mode)

    # -- choosing --------------------------------------------------------

    def test_clicking_a_screen_card_emits_the_monitor_index(self) -> None:
        picker = self.make_picker()
        picker.show()
        got: list[tuple[str, object]] = []
        picker.chosen.connect(lambda kind, payload: got.append((kind, payload)))
        QTest.mouseClick(
            monitor_cards(picker)[1], QtCore.Qt.MouseButton.LeftButton
        )
        self.assertEqual(got, [(config.SOURCE_MONITOR, 1)])
        self.assertEqual(self.settings.source_kind, config.SOURCE_MONITOR)
        self.assertEqual(self.settings.monitor_index, 1)
        self.assertFalse(picker.isVisible())

    def test_clicking_a_window_card_emits_the_hwnd(self) -> None:
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_WINDOW)
        picker.show()
        got: list[tuple[str, object]] = []
        picker.chosen.connect(lambda kind, payload: got.append((kind, payload)))
        QTest.mouseClick(
            picker.windows.cards()[0], QtCore.Qt.MouseButton.LeftButton
        )
        self.assertEqual(got, [(config.SOURCE_WINDOW, 1001)])
        self.assertEqual(self.settings.source_kind, config.SOURCE_WINDOW)
        self.assertEqual(self.settings.window_title, "Untitled - Notepad")

    def test_a_dead_window_is_refused_in_words(self) -> None:
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_WINDOW)
        got: list[tuple[str, object]] = []
        picker.chosen.connect(lambda kind, payload: got.append((kind, payload)))
        dead = [c for c in picker.windows.cards() if c.payload == DEAD_HWND][0]
        dead.click()
        self.assertEqual(got, [])
        self.assertTrue(picker.message.isVisibleTo(picker))
        self.assertIn("Calculator", picker.message.text())
        self.assertIn("closed", picker.message.text())
        self.assertEqual(self.settings.source_kind, config.SOURCE_MONITOR)
        self.assertEqual(picker.segments.current, config.SOURCE_WINDOW)

    def test_reusing_the_remembered_region_emits_the_rectangle(self) -> None:
        self.settings.region = [10, 20, 640, 480]
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_REGION)
        got: list[tuple[str, object]] = []
        picker.chosen.connect(lambda kind, payload: got.append((kind, payload)))
        picker.region.reuse_button.click()
        self.assertEqual(got, [(config.SOURCE_REGION, (10, 20, 640, 480))])
        self.assertEqual(self.settings.source_kind, config.SOURCE_REGION)

    def _install_overlay(self, chooser) -> None:
        """Put a stand-in `screenrec.overlay` in place of the real region selector."""
        import types

        import screenrec

        module = types.ModuleType("screenrec.overlay")

        class RegionSelector:
            choose = staticmethod(chooser)

        module.RegionSelector = RegionSelector
        previous = sys.modules.get("screenrec.overlay", "<absent>")
        had_attr = hasattr(screenrec, "overlay")
        previous_attr = getattr(screenrec, "overlay", None)

        def restore() -> None:
            if previous == "<absent>":
                sys.modules.pop("screenrec.overlay", None)
            else:
                sys.modules["screenrec.overlay"] = previous
            if had_attr:
                screenrec.overlay = previous_attr
            else:
                try:
                    del screenrec.overlay
                except AttributeError:
                    pass  # another test already removed it; nothing to undo

        self.addCleanup(restore)
        sys.modules["screenrec.overlay"] = module
        screenrec.overlay = module

    def test_a_failing_region_selector_is_explained_on_a_visible_surface(self) -> None:
        self._install_overlay(lambda parent=None: (_ for _ in ()).throw(
            OSError("no desktop to dim")
        ))
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_REGION)
        picker.show()
        seen: list[str] = []
        picker.cancelled.connect(lambda: seen.append("cancelled"))
        picker.chosen.connect(lambda kind, payload: seen.append("chosen"))

        picker.region.select_button.click()

        self.assertTrue(picker.isVisible(), "the failure must be shown, not hidden")
        self.assertTrue(picker.message.isVisibleTo(picker))
        self.assertIn("no desktop to dim", picker.message.text())
        self.assertEqual(
            seen, [], "a failure must not look like the user pressing Esc"
        )

    def test_a_cancelled_region_drag_still_emits_cancelled(self) -> None:
        self._install_overlay(lambda parent=None: None)
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_REGION)
        picker.show()
        seen: list[str] = []
        picker.cancelled.connect(lambda: seen.append("cancelled"))
        picker.region.select_button.click()
        self.assertEqual(seen, ["cancelled"])
        self.assertFalse(picker.isVisible())

    def test_a_region_drag_emits_the_rectangle(self) -> None:
        self._install_overlay(lambda parent=None: (12, 34, 800, 600))
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_REGION)
        picker.show()
        got: list[tuple[str, object]] = []
        picker.chosen.connect(lambda kind, payload: got.append((kind, payload)))
        picker.region.select_button.click()
        self.assertEqual(got, [(config.SOURCE_REGION, (12, 34, 800, 600))])
        self.assertEqual(self.settings.region, [12, 34, 800, 600])

    def test_picking_a_screen_survives_a_failing_monitor_list(self) -> None:
        """A COM failure while verifying must not swallow the click.

        PySide6 prints a traceback for an exception escaping a slot and carries on, so
        an unguarded call here means the user presses a card and nothing happens at all.
        """
        picker = self.make_picker()
        card = monitor_cards(picker)[0]
        got: list[tuple[str, object]] = []
        picker.chosen.connect(lambda kind, payload: got.append((kind, payload)))
        self._patch(
            winapi, "list_monitors",
            lambda: (_ for _ in ()).throw(OSError("COM is unhappy")),
        )
        with collecting_exceptions(self, "picking a screen with a failing list"):
            card.click()
        self.assertEqual(got, [(config.SOURCE_MONITOR, 0)])
        self.assertEqual(self.settings.monitor_index, 0)

    # -- dismissal -------------------------------------------------------

    def test_escape_emits_cancelled_and_hides(self) -> None:
        picker = self.make_picker()
        picker.show()
        seen: list[int] = []
        picker.cancelled.connect(lambda: seen.append(1))
        QTest.keyClick(picker, QtCore.Qt.Key.Key_Escape)
        self.assertEqual(seen, [1])
        self.assertFalse(picker.isVisible())

    def test_a_press_outside_dismisses_and_a_press_inside_does_not(self) -> None:
        outside = QtWidgets.QWidget()
        self.addCleanup(outside.deleteLater)
        outside.resize(60, 40)
        outside.show()
        picker = self.make_picker()
        picker.show()
        seen: list[int] = []
        picker.cancelled.connect(lambda: seen.append(1))

        QTest.mouseClick(picker.segments, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(seen, [], "a click inside the surface must not dismiss it")

        QTest.mouseClick(outside, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(seen, [1])
        self.assertFalse(picker.isVisible())

    def test_the_anchor_does_not_dismiss_but_its_neighbour_does(self) -> None:
        host = QtWidgets.QWidget()
        self.addCleanup(host.deleteLater)
        layout = QtWidgets.QHBoxLayout(host)
        chip = QtWidgets.QPushButton("Screen 1", host)
        other = QtWidgets.QPushButton("Record", host)
        layout.addWidget(chip)
        layout.addWidget(other)
        host.resize(240, 40)
        host.show()

        picker = self.make_picker()
        picker.popup_near(chip)
        seen: list[int] = []
        picker.cancelled.connect(lambda: seen.append(1))

        QTest.mouseClick(chip, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(seen, [], "the chip that opened the picker must not dismiss it")

        QTest.mouseClick(other, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(seen, [1], "another control in the bar should dismiss it")

    def _click_through_the_window(
        self, window: QtGui.QWindow, widget: QtWidgets.QWidget, top: QtWidgets.QWidget
    ) -> None:
        """Press `widget` the way the platform does: at the window, not the widget.

        `QTest.mouseClick(widget, …)` posts the event straight to the widget, so it
        never exercises the pass the application filter sees first - the one with the
        `QWidgetWindow` as the watched object. Every dismissal check above is blind to
        that pass, and a bug living in it dismissed the picker on every real click.
        """
        centre = QtCore.QPoint(widget.width() // 2, widget.height() // 2)
        QTest.mouseClick(
            window,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            widget.mapTo(top, centre),
        )
        self.app.processEvents()

    def test_a_real_press_inside_does_not_dismiss_the_picker(self) -> None:
        picker = self.make_picker()
        picker.show()
        self.app.processEvents()
        window = picker.windowHandle()
        self.assertIsNotNone(window, "the surface must be a window to be pressed as one")
        seen: list[str] = []
        picker.cancelled.connect(lambda: seen.append("cancelled"))
        picker.chosen.connect(lambda kind, payload: seen.append(f"chosen:{kind}"))

        # The centre of the three segments is Region, so this also proves the press
        # landed: the picker changed page instead of going away.
        self._click_through_the_window(window, picker.segments, picker)
        self.assertEqual(seen, [], "switching segment must not dismiss the picker")
        self.assertTrue(picker.isVisible())
        self.assertEqual(picker.segments.current, config.SOURCE_REGION)

        self._click_through_the_window(window, picker.refresh_button, picker)
        self.assertEqual(seen, [], "the refresh button must not dismiss the picker")
        self.assertTrue(picker.isVisible())

        picker.set_mode(config.SOURCE_MONITOR)
        self.app.processEvents()
        self._click_through_the_window(window, monitor_cards(picker)[1], picker)
        self.assertEqual(
            seen,
            ["chosen:" + config.SOURCE_MONITOR],
            "a card must choose, and must not report a dismissal first",
        )

    def test_a_real_press_in_another_window_dismisses_once(self) -> None:
        outside = QtWidgets.QWidget()
        self.addCleanup(outside.deleteLater)
        outside.resize(60, 40)
        outside.show()
        picker = self.make_picker()
        picker.show()
        self.app.processEvents()
        seen: list[str] = []
        picker.cancelled.connect(lambda: seen.append("cancelled"))

        window = outside.windowHandle()
        self.assertIsNotNone(window)
        self._click_through_the_window(window, outside, outside)
        self.assertEqual(seen, ["cancelled"])
        self.assertFalse(picker.isVisible())

    def test_hiding_removes_the_application_event_filter(self) -> None:
        picker = self.make_picker()
        picker.show()
        self.assertTrue(picker._filtering)
        picker.hide()
        self.assertFalse(picker._filtering)

    # -- placement -------------------------------------------------------

    def test_popup_near_prefers_below_and_stays_on_the_screen(self) -> None:
        anchor = QtWidgets.QWidget()
        self.addCleanup(anchor.deleteLater)
        anchor.resize(200, 44)
        anchor.move(400, 100)
        anchor.show()
        picker = self.make_picker()
        picker.popup_near(anchor)
        self.assertGreaterEqual(picker.y(), 100 + 44)
        self.assertGreaterEqual(picker.x(), sources.EDGE)
        self.assertLessEqual(
            picker.x() + picker.width(), MONITORS[0].width - sources.EDGE
        )

    def test_popup_near_flips_above_when_there_is_no_room_below(self) -> None:
        anchor = QtWidgets.QWidget()
        self.addCleanup(anchor.deleteLater)
        anchor.resize(200, 44)
        anchor.move(400, 1440 - 80)
        anchor.show()
        picker = self.make_picker()
        picker.popup_near(anchor)
        self.assertLessEqual(picker.y() + picker.height(), 1440 - 80)
        self.assertGreaterEqual(picker.y(), 0)

    def test_popup_near_clamps_against_the_right_edge(self) -> None:
        anchor = QtWidgets.QWidget()
        self.addCleanup(anchor.deleteLater)
        anchor.resize(120, 44)
        anchor.move(2560 - 130, 200)
        anchor.show()
        picker = self.make_picker()
        picker.popup_near(anchor)
        self.assertLessEqual(
            picker.x() + picker.width(), MONITORS[0].width - sources.EDGE
        )
        self.assertGreaterEqual(picker.x(), sources.EDGE)

    def test_popup_near_uses_the_monitor_the_anchor_is_on(self) -> None:
        anchor = QtWidgets.QWidget()
        self.addCleanup(anchor.deleteLater)
        anchor.resize(120, 44)
        anchor.move(2560 + 600, 300)
        anchor.show()
        picker = self.make_picker()
        picker.popup_near(anchor)
        self.assertGreaterEqual(picker.x(), 2560 + sources.EDGE)
        self.assertLessEqual(
            picker.x() + picker.width(), 2560 + 1440 - sources.EDGE
        )

    # -- thumbnails ------------------------------------------------------

    def test_thumbnails_are_requested_only_for_the_visible_segment(self) -> None:
        picker = self.make_picker()
        loader = picker._loader
        self.assertIsInstance(loader, StubLoader)
        keys = [r[0] for r in loader.requests]
        self.assertEqual(keys, ["monitor:0", "monitor:1"])
        picker.set_mode(config.SOURCE_WINDOW)
        keys = [r[0] for r in loader.requests]
        self.assertEqual(
            keys,
            ["monitor:0", "monitor:1", "window:1001", "window:1002", "window:1003"],
        )
        self.assertIs(loader.requests[0][1], MONITORS[0])
        self.assertIs(loader.requests[2][2], WINDOWS[0])

    def test_a_delivered_thumbnail_lands_on_its_card(self) -> None:
        picker = self.make_picker()
        pixmap = QtGui.QPixmap(240, 158)
        pixmap.fill(QtGui.QColor("#3366cc"))
        card = monitor_cards(picker)[0]
        self.assertFalse(card.has_thumbnail())
        picker._loader.ready.emit("monitor:0", StubThumb(pixmap))
        self.assertTrue(card.has_thumbnail())
        with collecting_exceptions(self, "painting a card with a thumbnail"):
            card.grab()

    def test_a_failed_thumbnail_leaves_the_placeholder(self) -> None:
        picker = self.make_picker()
        card = monitor_cards(picker)[0]
        picker._loader.ready.emit("monitor:0", None)
        self.assertFalse(card.has_thumbnail())
        with collecting_exceptions(self, "painting a card with no thumbnail"):
            card.grab()

    def test_destroying_the_picker_stops_the_loader(self) -> None:
        """The loader is a child of the picker, so the picker's delete would take it.

        Without a stop() first, the worker thread raises `Signal source has been
        deleted` on the next answer and dies, and a real grab leaves its ffmpeg child
        and its temp PNG behind.
        """
        picker = sources.SourcePicker(self.settings)
        loader = picker._loader
        self.assertIsInstance(loader, StubLoader)
        self.assertIs(loader.parent(), picker)
        picker.deleteLater()
        # `processEvents()` does not deliver deferred deletes - Qt holds them for the
        # event loop level that posted them - so the delivery is asked for by name.
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.Type.DeferredDelete
        )
        self.assertTrue(loader.stopped, "deleting the picker must stop the loader")

    def test_closing_and_shutdown_stop_the_loader(self) -> None:
        picker = self.make_picker()
        loader = picker._loader
        picker.close()
        self.assertTrue(loader.stopped)
        self.assertIsNone(picker._loader, "a stopped loader is dropped, not reused")
        picker.shutdown()  # twice over is not an error

    def test_hiding_keeps_the_loader(self) -> None:
        """A popover is put away and reopened; that must not cost a grab per card."""
        picker = self.make_picker()
        loader = picker._loader
        picker.show()
        picker.hide()
        self.assertFalse(loader.stopped)
        self.assertIs(picker._loader, loader)

    def test_a_missing_thumbnails_module_is_reported_not_fatal(self) -> None:
        self._install_thumbnails(None)  # makes `from . import thumbnails` raise
        import screenrec.ui as ui_package

        if hasattr(ui_package, "thumbnails"):
            del ui_package.thumbnails
        picker = self.make_picker()
        self.assertIsNone(picker._loader)
        self.assertIn("Thumbnails are unavailable", picker._loader_problem)
        self.assertTrue(picker.message.isVisibleTo(picker))
        self.assertEqual(len(monitor_cards(picker)), 2)
        with collecting_exceptions(self, "painting with no thumbnail loader"):
            picker.grab()

    def test_it_drives_the_real_thumbnail_loader(self) -> None:
        """The stub above is only worth something if it has the real module's shape.

        So this one uses `ui.thumbnails` itself, with the ffmpeg grab replaced by a
        function that returns PNG bytes. It proves the constructor call, the `request`
        keywords and the `ready` payload all match what `sources` assumes.
        """
        self.addCleanup(sys.modules.pop, THUMBNAILS, None)
        sys.modules.pop(THUMBNAILS, None)
        import importlib

        import screenrec.ui as ui_package

        thumbnails = importlib.import_module(THUMBNAILS)
        ui_package.thumbnails = thumbnails

        image = QtGui.QImage(240, 158, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor("#225533"))
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        png = bytes(buffer.data())

        real = thumbnails.ThumbnailLoader
        self._patch(
            thumbnails,
            "ThumbnailLoader",
            lambda parent=None: real(parent, grab=lambda request: (png, "")),
        )
        picker = self.make_picker()
        self.addCleanup(picker._stop_loader)
        loader = picker._loader
        self.assertIsInstance(loader, real)

        card = monitor_cards(picker)[0]
        for _ in range(200):
            if card.has_thumbnail():
                break
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(card.has_thumbnail(), "the real loader never delivered a card")
        with collecting_exceptions(self, "painting a real-loader thumbnail"):
            picker.grab()

    # -- painting --------------------------------------------------------

    def test_every_segment_paints(self) -> None:
        self.settings.region = [10, 20, 640, 480]
        picker = self.make_picker()
        for mode in (
            config.SOURCE_MONITOR, config.SOURCE_REGION, config.SOURCE_WINDOW
        ):
            picker.set_mode(mode)
            with collecting_exceptions(self, f"painting the {mode} segment"):
                pixmap = picker.grab()
            self.assertFalse(pixmap.isNull(), mode)
            self.assertEqual(pixmap.size(), picker.size())

    def test_a_card_paints_in_every_state(self) -> None:
        picker = self.make_picker()
        card = monitor_cards(picker)[0]
        for selected in (False, True):
            card.set_selected(selected)
            for down in (False, True):
                card.setDown(down)
                with collecting_exceptions(
                    self, f"painting a card selected={selected} down={down}"
                ):
                    self.assertFalse(card.grab().isNull())
        card.setDown(False)

    def test_the_message_line_paints(self) -> None:
        picker = self.make_picker()
        picker.show_message("Something went wrong, and here is a sentence about it.")
        self.assertTrue(picker.message.isVisibleTo(picker))
        with collecting_exceptions(self, "painting the message line"):
            picker.grab()
        self.assertLessEqual(picker.height(), sources.MAX_HEIGHT)

    def test_an_empty_grid_paints(self) -> None:
        self._patch(winapi, "list_monitors", lambda: [])
        self._patch(winapi, "list_windows", lambda min_size=80: [])
        picker = self.make_picker()
        self.assertEqual(monitor_cards(picker), [])
        for mode in (config.SOURCE_MONITOR, config.SOURCE_WINDOW):
            picker.set_mode(mode)
            with collecting_exceptions(self, f"painting an empty {mode} grid"):
                picker.grab()

    # -- geometry constants ----------------------------------------------

    def test_the_well_keeps_the_16_to_10_5_ratio(self) -> None:
        self.assertAlmostEqual(
            sources.WELL_H / sources.WELL_W, 10.5 / 16.0, places=2
        )
        self.assertLessEqual(
            2 * sources.CARD_W + sources.GRID_GAP,
            sources.WIDTH - 2 * sources.SIDE_MARGIN,
        )


if __name__ == "__main__":
    unittest.main()


class AllScreensCardTest(SourcesTestCase):
    """The card that records every monitor at once."""

    def test_it_leads_the_screen_page_when_there_is_more_than_one_monitor(self) -> None:
        picker = self.make_picker()
        cards = picker.screens.cards()
        self.assertEqual(cards[0].key, "all:screens")
        self.assertIn("2 screens", cards[0]._name)

    def test_it_is_not_offered_with_only_one_monitor(self) -> None:
        """On a laptop it would be a lie."""
        self._patch(winapi, "list_monitors", lambda: [MONITORS[0]])
        picker = self.make_picker()
        self.assertIsNone(all_screens_card(picker))

    def test_clicking_it_chooses_every_screen(self) -> None:
        picker = self.make_picker()
        chosen: list[tuple] = []
        picker.chosen.connect(lambda kind, payload: chosen.append((kind, payload)))
        picker._pick_screen_card(all_screens_card(picker))
        self.assertEqual([(config.SOURCE_ALL_SCREENS, None)], chosen)

    def test_it_shows_as_selected_when_the_settings_say_so(self) -> None:
        picker = self.make_picker()
        picker.settings.source_kind = config.SOURCE_ALL_SCREENS
        picker._mark_selection()
        self.assertTrue(all_screens_card(picker).is_selected())
        self.assertFalse(any(c.is_selected() for c in monitor_cards(picker)))

    def test_no_thumbnail_is_asked_for_it(self) -> None:
        """It stands for every screen, so there is no one frame to grab.

        Asking anyway made the loader report "neither a monitor nor a window" every
        time the picker opened.
        """
        picker = self.make_picker()
        asked: list[str] = []
        loader = picker._loader
        if loader is not None and hasattr(loader, "request"):
            original = loader.request
            loader.request = lambda key, **kw: asked.append(key)
            picker._request_thumbnails(config.SOURCE_MONITOR)
            loader.request = original
            self.assertNotIn("all:screens", asked)


class LanguageTests(SourcesTestCase):
    """The picker in Russian, and the switch happening while it is open.

    `SourcesTestCase.setUp` pins English and puts it back, so each test here only has
    to ask for the other language.
    """

    def test_a_picker_built_in_russian_is_in_russian(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        picker = self.make_picker()
        self.assertEqual(
            [label for _key, label in picker.segments.options],
            ["Экран", "Область", "Окно"],
        )
        cards = monitor_cards(picker)
        self.assertEqual([c._name for c in cards], ["Экран 1", "Экран 2"])
        self.assertEqual(cards[0]._detail, "2560 × 1440 · основной")
        self.assertEqual(cards[1]._detail, "1440 × 2560 · повёрнутый")
        self.assertEqual(all_screens_card(picker)._name, "Все 2 экрана")

    def test_the_switch_reaches_a_picker_that_is_already_open(self) -> None:
        self.settings.region = [320, 180, 1280, 720]
        picker = self.make_picker()
        picker.show()
        self.assertEqual(monitor_cards(picker)[0]._name, "Screen 1")

        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(monitor_cards(picker)[0]._name, "Экран 1")
        self.assertEqual(
            picker.refresh_button.toolTip(), "Обновить список экранов и окон"
        )
        self.assertEqual(picker.region.select_button.text(), "Выбрать область…")
        self.assertIn("1280 × 720", picker.region._remembered.text())
        self.assertTrue(picker.region._remembered.text().startswith("Прошлая область"))
        with collecting_exceptions(self, "painting the picker in Russian"):
            picker.grab()

        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(monitor_cards(picker)[0]._name, "Screen 1")
        self.assertEqual(picker.region.select_button.text(), "Select an area…")

    def test_the_empty_grid_speaks_the_new_language(self) -> None:
        self._patch(winapi, "list_monitors", lambda: [])
        self._patch(winapi, "list_windows", lambda min_size=80: [])
        picker = self.make_picker()
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(
            picker.screens._empty.text(), "DXGI не сообщила ни об одном экране."
        )
        self.assertEqual(
            picker.windows._empty.text(),
            "Нет окон, подходящих по размеру для записи.",
        )

    def test_a_dead_window_is_refused_in_the_new_language(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        picker = self.make_picker()
        picker.set_mode(config.SOURCE_WINDOW)
        dead = [c for c in picker.windows.cards() if c.payload == DEAD_HWND][0]
        dead.click()
        self.assertTrue(picker.message.isVisibleTo(picker))
        self.assertIn("Calculator", picker.message.text())
        self.assertIn("закрыто", picker.message.text())
