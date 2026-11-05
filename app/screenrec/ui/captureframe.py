"""The thin outline that shows what is being recorded.

**The whole point of this module is keeping the outline out of the file.** The Desktop
Duplication API duplicates the composed desktop, so anything Windows paints inside the
captured rectangle is in the file - a red box in every single frame of the user's
recording. There are two ways round that, and this module uses both:

* **Outside.** For a region or a window, the frame window is placed *outside* the
  rectangle, at ``(x - PAD, y - PAD)`` with the rectangle's size plus ``2 * PAD``, and the
  outline is stroked just inside the window's own edge. Not one pixel of it is ever inside
  the capture. This needs nothing of Windows and is used wherever it fits.
* **Inside, hidden from the capture.** A whole-monitor recording has no outside, and it is
  the case where the user most wants the border, because nothing else on the screen says
  that this monitor is the one being written. So the window covers the monitor, the
  outline is stroked along its very edge, and the window is given
  ``WDA_EXCLUDEFROMCAPTURE`` (`ui.surfaces.exclude_from_capture`), measured on this
  machine to contribute **zero** pixels to a ddagrab frame.

The second way is taken only when that call is confirmed to have taken effect - it needs
Windows 10 version 2004. Where it has not, `show_for` returns ``False`` and paints
nothing, exactly as the whole-monitor case always did, and the caller says so. Drawing
inside the capture without the affinity is the failure this module is designed against.

Two smaller decisions, for the next reader:

* **The rectangle is used as given, not rounded.** `recorder.surface_crop` intersects the
  rectangle with the monitor and rounds the size *down* to even, so what ffmpeg records is
  always a subset of what is passed in here. Framing the un-rounded rectangle therefore
  stays outside the capture whatever the rotation; rounding it here would move the outline
  inwards on a rotated screen, where the dropped pixel comes off the opposite edge.
* **A rotated monitor needs nothing special.** The rectangle arrives in desktop
  coordinates and the window is placed in desktop coordinates. The transposing belongs to
  the filter graph, which has already measured it.

The tag is the `panel` colour with `record` text and a `record` border, not white on red:
white on this red measures 3.7:1, which is below the 4.5:1 the palette is held to.
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n, winapi
from ..i18n import _

# The native click-through bits, which `overlay` already gets right and reports on. The
# function is shared rather than copied - duplicated ctypes window-style code is how one
# of the two copies ends up missing a flag. The *class* is not imported, as asked: this
# widget is not a ClickHighlight and does not want its desktop-wide geometry.
from ..overlay import make_click_through
from . import surfaces, theme

__all__ = ["CaptureFrame", "THICKNESS", "GAP", "PAD"]

#: The outline's stroke, in pixels.
THICKNESS = 2
#: The clear pixel between the outline and the captured rectangle, so that anti-aliasing
#: or an off-by-one in a scaled coordinate still cannot reach into the capture.
GAP = 1
#: How far outside the captured rectangle the window starts, on every side.
PAD = THICKNESS + GAP

#: The tag sits in its own strip above (or, with no room above, below) the outline.
TAG_HEIGHT = 18
TAG_GAP = 2
TAG_PAD_X = 7
#: 11 px: the contract's 10-11 px. Set in pixels rather than points so it does not change
#: size with the screen's logical DPI - this label must stay small and out of the way.
TAG_FONT_PX = 11


class CaptureFrame(QtWidgets.QWidget):
    """A thin outline round the rectangle being recorded.

    Click-through, always on top, never focusable. `show_for` returns whether a frame is
    being shown; when it is not, `problem` says why in words the caller can show.

    A window that is being recorded can move. The recording does not follow it, so neither
    does this frame: it stays where the recording is, which is the honest thing to show.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("ScreenRec - capture frame"))
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        #: Empty while a frame is shown; otherwise why it is not, for the caller to say.
        self.problem = ""
        #: The rectangle actually being framed, in physical desktop pixels.
        self.capture_rect = QtCore.QRect()
        #: Where the window was asked to go, in physical desktop pixels. Kept because
        #: `geometry()` is in logical units, which differ on a scaled screen.
        self.window_rect = QtCore.QRect()

        #: Whether Windows has agreed to keep this window out of the capture. Only then
        #: may the outline be drawn inside the recorded rectangle.
        self.excluded_from_capture = False

        self._label = "Recording"
        self._frame_rect = QtCore.QRect()  # the outline's box, widget-local
        self._tag_above = True
        #: True while the outline is drawn inside the capture rather than around it.
        self._inside = False
        #: Where the tag's top-left corner goes, widget-local. Computed when the window
        #: is placed, because it differs in all three cases (above, below, inside).
        self._tag_origin = QtCore.QPoint(0, 0)

        i18n.notifier.changed.connect(self.retranslate)

    # ------------------------------------------------------------------ text

    def retranslate(self) -> None:
        """Re-read the window title, which is the only string this widget holds.

        The tag beside the outline is `_label`, and that is the caller's word, handed
        in by `show_for`; this widget does not own it and must not rewrite it.
        """
        self.setWindowTitle(_("ScreenRec - capture frame"))

    # ------------------------------------------------------------------ API

    def show_for(
        self,
        monitor: winapi.Monitor,
        rect: tuple[int, int, int, int] | None,
        label: str = "Recording",
    ) -> bool:
        """Frame `rect` on `monitor`. Returns False (and sets `problem`) if it cannot.

        `rect` is ``(x, y, width, height)`` in physical desktop pixels, exactly as the
        region selector and the recorder use them. ``None`` means the whole monitor: the
        outline then goes *inside* it, which is only safe because the window is excluded
        from the capture - if Windows refuses that, False comes back as it always did.
        """
        self.problem = ""
        self._label = label

        if monitor is None:
            self.problem = _(
                "No monitor was given, so there is nothing to draw a frame on."
            )
            self.hide_frame()
            return False

        screen_rect = QtCore.QRect(monitor.x, monitor.y, monitor.width, monitor.height)

        if rect is None:
            wanted = QtCore.QRect(screen_rect)
        else:
            try:
                x, y, width, height = (int(value) for value in rect)
            except (TypeError, ValueError) as exc:
                self.problem = _(
                    "The rectangle {rect} is not four numbers: {reason}"
                ).format(rect=repr(rect), reason=exc)
                self.hide_frame()
                return False
            wanted = QtCore.QRect(x, y, width, height).normalized()

        captured = wanted.intersected(screen_rect)
        if captured.isEmpty():
            self.problem = _(
                "The rectangle {rect} does not overlap monitor {index} ({screen}), "
                "so there is nothing to frame."
            ).format(
                rect=f"{wanted.x()},{wanted.y()} "
                     f"{wanted.width()}x{wanted.height()}",
                index=monitor.index,
                screen=f"{monitor.x},{monitor.y} "
                       f"{monitor.width}x{monitor.height}",
            )
            self.hide_frame()
            return False

        inside = captured == screen_rect
        # Asked for on every path, not only the inside one: with two monitors being
        # recorded at once, a frame placed outside one rectangle can land inside the
        # other, and the affinity is what saves it there too.
        excluded = self._ensure_excluded()
        if inside and not excluded:
            # The old behaviour, kept for the builds that cannot hide a window from the
            # capture: a whole-monitor recording has no room outside it, and an outline
            # inside it would be in every frame of the file.
            self.problem = _(
                "Monitor {index} is being recorded whole, and this build of Windows "
                "will not keep the frame out of the capture (it needs Windows 10 "
                "version 2004), so no frame is drawn - one would be in every frame "
                "of the recording."
            ).format(index=monitor.index)
            self.hide_frame()
            return False

        self.capture_rect = captured
        self._inside = inside
        if inside:
            self._place_inside(monitor, captured)
        else:
            self._place(monitor, captured)
        self.show()
        self.raise_()

        native = self._apply_click_through()
        if native:
            # Not fatal - the frame is still correct and still outside the capture - but a
            # frame that eats a click where the user is demonstrating must be findable.
            # Appended, not assigned: `_place` may already have reported a misplacement,
            # and the second problem must not erase the first.
            self.problem = f"{self.problem} {native}".strip() if self.problem else native

        self.update()
        return True

    def hide_frame(self) -> None:
        """Hide the frame and forget its rectangle. Safe when nothing is shown."""
        self.capture_rect = QtCore.QRect()
        self.window_rect = QtCore.QRect()
        self._frame_rect = QtCore.QRect()
        self._inside = False
        self.hide()

    # ------------------------------------------------------------- geometry

    def _place(self, monitor: winapi.Monitor, captured: QtCore.QRect) -> None:
        """Size and position the window round `captured`, with room for the tag.

        The pairing with a QScreen is done *here*, once, and the result carried into
        `_logical_rect`. Doing it twice is what used to hide the failure: an unpaired
        monitor fell back to ratio 1.0, which then took `_logical_rect`'s 100% fast path,
        so the branch that reports the fallback was never reached and physical pixels were
        handed to `setGeometry` as logical ones without a word.
        """
        screen = self._screen_for(monitor)
        if screen is None:
            ratio = 1.0
            if self._desktop_is_scaled():
                # Only wrong when something on this desktop is scaled; where every screen
                # is at 100% the physical rectangle *is* the logical one and the fallback
                # is exact, so there is nothing to report.
                self.problem = self._unpaired_message(monitor)
        else:
            ratio = float(screen.devicePixelRatio() or 1.0)

        pad_px = int(math.ceil(PAD * ratio))
        tag_block_px = int(math.ceil((TAG_HEIGHT + TAG_GAP) * ratio))

        outer = captured.adjusted(-pad_px, -pad_px, pad_px, pad_px)

        # The tag goes above the rectangle unless that would push it off the top of this
        # monitor, in which case it goes below. Either way it is outside the capture.
        self._tag_above = (outer.top() - tag_block_px) >= monitor.y
        if self._tag_above:
            window = outer.adjusted(0, -tag_block_px, 0, 0)
        else:
            window = outer.adjusted(0, 0, 0, tag_block_px)

        self.window_rect = window
        logical = self._logical_rect(window, monitor, ratio, screen)
        self.setGeometry(logical)

        tag_block = TAG_HEIGHT + TAG_GAP
        self._frame_rect = QtCore.QRect(
            0,
            tag_block if self._tag_above else 0,
            logical.width(),
            max(0, logical.height() - tag_block),
        )
        self._tag_origin = QtCore.QPoint(
            self._frame_rect.left(),
            0 if self._tag_above else self._frame_rect.bottom() + 1 + TAG_GAP,
        )

    def _place_inside(self, monitor: winapi.Monitor, captured: QtCore.QRect) -> None:
        """Cover the monitor and stroke the outline along its very edge.

        Only ever reached with `excluded_from_capture` true, so these pixels are on the
        screen and not in the file. The tag hangs just under the top edge rather than
        above it, for the same reason the outline moves in: there is no above.
        """
        screen = self._screen_for(monitor)
        if screen is None:
            ratio = 1.0
            if self._desktop_is_scaled():
                self.problem = self._unpaired_message(monitor)
        else:
            ratio = float(screen.devicePixelRatio() or 1.0)

        self.window_rect = QtCore.QRect(captured)
        logical = self._logical_rect(self.window_rect, monitor, ratio, screen)
        self.setGeometry(logical)

        self._tag_above = False
        self._frame_rect = QtCore.QRect(0, 0, logical.width(), logical.height())
        self._tag_origin = QtCore.QPoint(
            THICKNESS + TAG_GAP, THICKNESS + TAG_GAP
        )

    def _ratio_for(self, monitor: winapi.Monitor) -> float:
        """The device pixel ratio of the screen this monitor is, or 1.0.

        `_place` does not use this - it needs the screen itself and must know when the
        pairing failed - but it keeps the two questions askable separately.
        """
        screen = self._screen_for(monitor)
        if screen is None:
            return 1.0
        return float(screen.devicePixelRatio() or 1.0)

    def _desktop_is_scaled(self) -> bool:
        """True when any screen is above 100%, i.e. logical units are not physical ones."""
        return any(
            abs(float(screen.devicePixelRatio() or 1.0) - 1.0) > 1e-6
            for screen in QtGui.QGuiApplication.screens()
        )

    @staticmethod
    def _unpaired_message(monitor: winapi.Monitor) -> str:
        """What the caller is told when the monitor has no QScreen to place against."""
        return (
            f"Monitor {monitor.index} could not be paired with a Qt screen, so the "
            "capture frame is placed in physical pixels and will be misplaced on a "
            "scaled desktop."
        )

    def _screen_for(self, monitor: winapi.Monitor) -> QtGui.QScreen | None:
        """Pair a DXGI output with its QScreen - by name if that works, else by geometry.

        The same three steps as ``overlay._match_screen``, in the same order, because the
        same thing is being decided. Measured there: ``QScreen.name()`` is the monitor's
        friendly name here ('Display 32 QHD') while `winapi` reports ``\\\\.\\DISPLAY2``, so
        the name comparison never matches on this machine - it is kept as a first try for
        a Qt build that does report the device name. Geometry is the reliable pairing, and
        it is exact at 100% scaling, where Qt's logical desktop *is* the physical one.
        """
        screens = QtGui.QGuiApplication.screens()
        for screen in screens:
            if screen.name() == monitor.device_name:
                return screen
        for screen in screens:
            geometry = screen.geometry()
            if (
                geometry.x() == monitor.x
                and geometry.y() == monitor.y
                and geometry.width() == monitor.width
                and geometry.height() == monitor.height
            ):
                return screen
        # Scaled: logical size times the ratio is the physical size.
        candidates: list[tuple[QtGui.QScreen, QtCore.QRect, float]] = []
        for screen in screens:
            geometry = screen.geometry()
            ratio = float(screen.devicePixelRatio() or 1.0)
            if (
                int(round(geometry.width() * ratio)) == monitor.width
                and int(round(geometry.height() * ratio)) == monitor.height
            ):
                candidates.append((screen, geometry, ratio))
        if len(candidates) == 1:
            return candidates[0][0]
        # Two screens of the same size at the same scaling - an ordinary pair of identical
        # monitors - are told apart by where they are. Logical origin times the ratio is
        # the physical origin only while the desktop scales uniformly, which is exactly
        # the case this rescues; under mixed DPI it simply fails to match and the caller
        # is told, which is better than guessing between two screens.
        by_origin = [
            screen
            for screen, geometry, ratio in candidates
            if int(round(geometry.x() * ratio)) == monitor.x
            and int(round(geometry.y() * ratio)) == monitor.y
        ]
        return by_origin[0] if len(by_origin) == 1 else None

    def _logical_rect(
        self,
        rect: QtCore.QRect,
        monitor: winapi.Monitor,
        ratio: float,
        screen: QtGui.QScreen | None = None,
    ) -> QtCore.QRect:
        """Physical desktop pixels -> the logical units `setGeometry` wants.

        At 100% scaling the two are the same number, which is the case on this machine and
        under the offscreen platform, so the physical rectangle is used unchanged and the
        geometry can be compared with `winapi.list_monitors()` straight off.

        **The scaled branch is untested** - both screens here run at 100%. It follows Qt's
        own mapping: the screen's logical origin plus the offset into that screen divided
        by its ratio. The size is rounded *up*, because an oversized window only pushes the
        outline further outside the capture, while an undersized one would pull it in.

        `screen` is the pairing `_place` already made; without one it is made here, so a
        direct call still works. The fallback is reported from the *caller*, because at
        100% an unpaired monitor costs nothing - the physical rectangle is the logical one
        - and only a scaled desktop makes it a wrong answer.
        """
        if abs(ratio - 1.0) <= 1e-6:
            return QtCore.QRect(rect)
        if screen is None:
            screen = self._screen_for(monitor)
        if screen is None:
            self.problem = self._unpaired_message(monitor)
            return QtCore.QRect(rect)
        origin = screen.geometry()
        left = origin.x() + int(math.floor((rect.x() - monitor.x) / ratio))
        top = origin.y() + int(math.floor((rect.y() - monitor.y) / ratio))
        width = int(math.ceil(rect.width() / ratio))
        height = int(math.ceil(rect.height() / ratio))
        return QtCore.QRect(left, top, max(1, width), max(1, height))

    # --------------------------------------------------------------- native

    def _ensure_excluded(self) -> bool:
        """Ask Windows to keep this window out of the capture. Returns whether it did.

        Asked again on every `show_for` rather than once in the constructor: Qt can
        recreate the native window - a change of screen does it - and the affinity is a
        property of the HWND, so a cached yes from an older handle would be a lie.
        """
        if QtGui.QGuiApplication.platformName() != "windows":
            # offscreen and minimal have no HWND to set it on, and nothing captures them.
            self.excluded_from_capture = False
            return False
        self.excluded_from_capture = surfaces.exclude_from_capture(self)
        return self.excluded_from_capture

    def _apply_click_through(self) -> str:
        """Force the extended window styles on. Returns "" or what went wrong."""
        platform = QtGui.QGuiApplication.platformName()
        if platform != "windows":
            # offscreen and minimal have no HWND, and no desktop to click through either.
            return ""
        return make_click_through(int(self.winId()))

    # -------------------------------------------------------------- drawing

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        if self._frame_rect.isEmpty():
            return
        colours = theme.COLOURS
        record = QtGui.QColor(colours["record"])

        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        pen = QtGui.QPen(record)
        pen.setWidth(THICKNESS)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        # Stroked on the centre line half a thickness in, so the whole stroke lands on the
        # window's outermost THICKNESS pixels - that is, PAD..PAD-THICKNESS outside the
        # captured rectangle, never inside it. Sharp corners: the outline's corners are the
        # captured rectangle's corners, and a radius would blur what is being shown.
        half = THICKNESS / 2.0
        outline = QtCore.QRectF(self._frame_rect).adjusted(half, half, -half, -half)
        painter.drawRect(outline)

        self._paint_tag(painter)

    def _paint_tag(self, painter: QtGui.QPainter) -> None:
        """The small 'Recording' tag, just outside the top-left corner of the capture."""
        if not self._label:
            return
        colours = theme.COLOURS
        font = QtGui.QFont(self.font())
        font.setPixelSize(TAG_FONT_PX)
        metrics = QtGui.QFontMetrics(font)

        text = metrics.elidedText(
            self._label, QtCore.Qt.TextElideMode.ElideRight, max(20, self.width() - 16)
        )
        width = min(metrics.horizontalAdvance(text) + 2 * TAG_PAD_X, self.width())
        left, top = self._tag_origin.x(), self._tag_origin.y()
        if top + TAG_HEIGHT > self.height():
            # No strip to draw in: the outline alone still says what is being recorded.
            return
        tag = QtCore.QRectF(left, top, width, TAG_HEIGHT)

        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor(colours["record"]), 1))
        painter.setBrush(QtGui.QColor(colours["panel"]))
        radius = float(theme.RADIUS["small"])
        painter.drawRoundedRect(tag.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        painter.setFont(font)
        painter.setPen(QtGui.QColor(colours["record"]))
        painter.drawText(
            tag,
            int(QtCore.Qt.AlignmentFlag.AlignCenter),
            text,
        )
