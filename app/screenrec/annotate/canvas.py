"""The transparent layer the drawings live on - the one window that IS recorded.

Every other surface in this application is chrome and is marked
``WDA_EXCLUDEFROMCAPTURE`` so it stays on the screen and out of the file. This one is the
exception, and the whole annotation feature rests on it: the Desktop Duplication API
duplicates the *composed* desktop, so an ordinary translucent always-on-top window is in
the captured frame at the moment it is painted. Both halves of that were measured against
the real capture path on 2026-11-04 (see ``docs/ANNOTATE_CONTRACT.md``). Nothing here may
call ``surfaces.exclude_from_capture``, and this is deliberately a plain ``QWidget``
rather than a ``FloatingSurface``, whose ``showEvent`` would exclude it.

Three things this module is built around.

**The window is never recreated.** Switching between pass-through and draw is a change of
window *flags*, and ``QWidget.setWindowFlags`` goes through ``setParent`` internally,
which on some paths destroys and rebuilds the native window. A rebuilt window appears at
the top of the z-order with a new handle: in the middle of a recording that is a visible
flicker and a lost display affinity. Measured here with PySide6 on the real windows
plugin, on a shown window: the HWND is **unchanged** across
``setWindowFlag(WindowTransparentForInput, True)`` and back again - but the widget is left
**hidden** and has to be shown again. So the flag is changed on the live window, the
geometry is reapplied, the window is re-shown if it was visible, and the handle is
compared before and after; a change would be reported through `input_problem` rather than
passing unnoticed.

**Only the dirty rectangle is repainted.** ``Document.dirty_rect()`` is in physical
desktop pixels; it is converted to widget coordinates and handed to ``update(rect)``.
``update()`` with no argument is never called from the drawing path - a full-desktop
repaint on every mouse move, beside a 2560x1440 60 fps capture, is the cost this whole
object model exists to avoid. The painter then culls items against the exposed rectangle
as well, so a stroke in one corner does not pay for the drawings in the other.

The document's mark is deliberately coarse: ``Document.extend`` marks ``item.bounds()``,
which is the whole stroke so far, so following it literally makes a freehand stroke
repaint more and more of the desktop the longer it gets. Measured here offscreen over a
2560x1440 monitor, pen, one press and 240 moves ten pixels apart: 30x25 at the first
move, 420x220 at the fortieth, 2420x1220 - 80% of the desktop - at the last. So the
freehand path does **not** hand that mark to ``update``: ``_update_segment`` invalidates
the new segment alone, and ``repaint_dirty`` keeps the document's own extent for
everything whose change really is spread out (undo, redo, clear, hide, erase, and the
dragged shapes, which are redrawn whole from two corners anyway).

**The window must be hit-testable where nothing has been drawn.** ``WS_EX_LAYERED`` is
set in both modes - the translucent background needs it - and Windows hit-tests a layered
window against its per-pixel alpha, so every pixel this widget leaves at alpha 0 passes
the click to the application underneath. In pass-through mode that is exactly what is
wanted. In draw mode it would mean a stroke can only be started on top of ink that is
already there, and a press on blank screen would activate the application being
presented, which the contract forbids. So in draw mode ``paintEvent`` first fills the
exposed rectangle with ``HIT_BACKING`` - black at alpha 1/255, invisible on screen and
nothing a capture can show - and the whole window is repainted once on every mode change,
because the backing store only holds what has been painted into it. Pass-through is
unaffected: ``WS_EX_TRANSPARENT`` overrides the alpha either way.

**Z-order: the canvas is raised before the toolbar, never after.** Draw mode has to take
the keyboard, so entering it raises and activates this window - which, since the canvas
spans the whole desktop, puts it over the toolbar and would make the mode button
unreachable. Two things keep that from happening. A raise is done only on a real
transition, never on the reassert that ``set_mode`` offers for the same mode (which would
also pull focus off the text box being typed into). And every raise is announced with the
`raised` signal, so the owner can put its own chrome back on top afterwards.

**Coordinates are physical desktop pixels, converted once at this boundary.** Qt reports
logical units, which differ from physical pixels on a scaled display. Each monitor
becomes a `_Patch` - where it starts in widget units and how many physical pixels one
logical unit is - and every point in or out goes through the patch it falls on. Monitors
can start at a **negative** x or y, so nothing here assumes an origin at (0, 0).

*What could not be tested here:* this machine runs both screens at 100%, where logical
units and physical pixels are the same number and every expression below collapses to a
translation. The scaled and mixed-DPI branches - `_Patch.ratio` other than 1.0, and
`_window_rect`'s use of the logical screen union - are written out explicitly and
reasoned from Qt's own high-DPI mapping, but they are **unverified**. When a QScreen
cannot be paired with a DXGI output (which is the normal case under the offscreen
platform plugin, and on a machine whose Qt reports friendly monitor names), the whole
desktop falls back to one uniform patch and `mapping_problem` says so; that fallback is
exact at uniform scaling and wrong on a mixed-DPI desktop.
"""
from __future__ import annotations

import ctypes
import math
import sys
from ctypes import wintypes
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n, winapi
from ..i18n import _
from . import model

__all__ = ["Canvas"]

# --------------------------------------------------------------- native bits
#
# `overlay.make_click_through` is the proven recipe and these are the same bits, written
# out here rather than imported: the canvas needs to *clear* WS_EX_TRANSPARENT again when
# it goes back into draw mode, which that one-way helper does not do.

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

user32 = ctypes.WinDLL("user32", use_last_error=True)

# ------------------------------------------------------------------- drawing

#: How far the arrow head reaches back from the tip, as a multiple of the stroke width,
#: with a floor so a hairline arrow still has a head one can see.
ARROW_HEAD = 3.5
ARROW_HEAD_MIN = 10.0
#: Half the opening angle of the head.
ARROW_SPREAD_DEGREES = 26.0

#: Extra widget pixels round an update rectangle, for the antialiased edge of a stroke.
UPDATE_PAD = 2

#: Extra desktop pixels round a freehand segment's own rectangle, beyond the stroke
#: width: the round cap reaches half a width past the point and the antialiased edge a
#: little further still.
SEGMENT_PAD = 4.0

#: What draw mode paints where nothing has been drawn, so that Windows' hit test for a
#: layered window finds this widget there instead of passing the click through. One unit
#: of alpha: 1/255 of black over the desktop is below any viewer's and any encoder's
#: ability to show it, and 0 would be click-through again.
HIT_BACKING = QtGui.QColor(0, 0, 0, 1)

#: Below this the item is invisible anyway; above 1.0 Qt clamps. Kept off zero so a
#: mis-set opacity cannot make a stroke silently disappear with no way to find it.
MIN_OPACITY = 0.05


def _get_window_long(hwnd: int) -> int:
    """GetWindowLongPtrW, falling back to the 32-bit name on a 32-bit interpreter."""
    func = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    func.argtypes = [wintypes.HWND, ctypes.c_int]
    func.restype = (
        ctypes.c_longlong if hasattr(user32, "GetWindowLongPtrW") else ctypes.c_long
    )
    return int(func(wintypes.HWND(hwnd), GWL_EXSTYLE))


def _set_window_long(hwnd: int, value: int) -> int:
    func = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    func.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    func.restype = (
        ctypes.c_longlong if hasattr(user32, "SetWindowLongPtrW") else ctypes.c_long
    )
    ctypes.set_last_error(0)
    return int(func(wintypes.HWND(hwnd), GWL_EXSTYLE, value))


# ----------------------------------------------------------------- geometry


@dataclass(frozen=True)
class _Patch:
    """One monitor's slice of the canvas: where it sits, and at what scale.

    `left` and `top` are widget-local **logical** units; `desktop` is the monitor in
    physical desktop pixels; `ratio` is physical pixels per logical unit. At 100% scaling
    `ratio` is 1.0 and the mapping is a pure translation by the desktop union's origin -
    which is why a negative origin needs no special case anywhere, only arithmetic that
    does not assume zero.
    """

    left: float
    top: float
    ratio: float
    desktop: QtCore.QRect

    def to_widget(self, x: float, y: float) -> QtCore.QPointF:
        """Physical desktop pixel -> widget-local logical point."""
        return QtCore.QPointF(
            self.left + (x - self.desktop.x()) / self.ratio,
            self.top + (y - self.desktop.y()) / self.ratio,
        )

    def to_desktop(self, local: QtCore.QPointF) -> tuple[float, float]:
        """Widget-local logical point -> physical desktop pixel. Inverse of `to_widget`."""
        return (
            self.desktop.x() + (local.x() - self.left) * self.ratio,
            self.desktop.y() + (local.y() - self.top) * self.ratio,
        )

    def widget_rect(self) -> QtCore.QRectF:
        """This monitor's area in widget-local logical units."""
        return QtCore.QRectF(
            self.left,
            self.top,
            self.desktop.width() / self.ratio,
            self.desktop.height() / self.ratio,
        )

    def transform(self) -> QtGui.QTransform:
        """Desktop pixels -> widget units, as a painter transform.

        With this on the painter every item can be drawn in the coordinates the model
        keeps it in: a pen width in desktop pixels and a font size in desktop pixels both
        come out the right size on screen, because the same scale applies to them.
        """
        transform = QtGui.QTransform()
        transform.translate(self.left, self.top)
        transform.scale(1.0 / self.ratio, 1.0 / self.ratio)
        transform.translate(-self.desktop.x(), -self.desktop.y())
        return transform

    def desktop_area(self, widget_rect: QtCore.QRect) -> QtCore.QRectF:
        """The desktop rectangle a widget rectangle covers, for culling items."""
        top_left = self.to_desktop(
            QtCore.QPointF(widget_rect.left(), widget_rect.top())
        )
        bottom_right = self.to_desktop(
            QtCore.QPointF(widget_rect.right() + 1, widget_rect.bottom() + 1)
        )
        return QtCore.QRectF(
            QtCore.QPointF(*top_left), QtCore.QPointF(*bottom_right)
        ).normalized()


def _union_of(monitors: list[winapi.Monitor]) -> QtCore.QRect:
    """The bounding box of every monitor, in physical desktop pixels."""
    if not monitors:
        return QtCore.QRect()
    left = min(m.x for m in monitors)
    top = min(m.y for m in monitors)
    right = max(m.x + m.width for m in monitors)
    bottom = max(m.y + m.height for m in monitors)
    return QtCore.QRect(left, top, right - left, bottom - top)


class Canvas(QtWidgets.QWidget):
    """The transparent layer the drawings live on. THIS one is recorded."""

    MODE_PASSTHROUGH = "passthrough"
    MODE_DRAW = "draw"

    stroke_finished = QtCore.Signal(object)  # the model.Item that was committed
    text_requested = QtCore.Signal(int, int)  # desktop x, y - the controller opens an editor
    escaped = QtCore.Signal()
    #: This window has just put itself at the top of the z-order, which it has to do when
    #: it takes the keyboard for draw mode. The canvas covers the whole desktop, so
    #: anything that must stay reachable - the toolbar above all, whose mode button is the
    #: way out of draw mode - has to be raised again after this. The owner connects it;
    #: the canvas cannot raise windows it does not know about.
    raised = QtCore.Signal()

    #: Mirrors `surfaces.FloatingSurface.hide_from_capture`, and is False on purpose:
    #: this is the one surface that must appear in the recording. Nothing in this module
    #: calls `surfaces.exclude_from_capture`, and nothing else may call it on this widget.
    hide_from_capture = False

    def __init__(
        self,
        document: model.Document,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._document = document
        self._mode = self.MODE_PASSTHROUGH
        self._tool = model.TOOL_PEN

        #: Empty, or why the window has no geometry (no monitor was reported).
        self.geometry_problem = ""
        #: Empty, or why a point conversion had to fall back to the uniform mapping.
        self.mapping_problem = ""
        #: Empty, or why the window styles that make a mode work could not be set.
        self.input_problem = ""
        #: Empty, or the first exception a paint raised. A paint must never raise out.
        self.paint_problem = ""
        #: The last rectangle handed to `update`, in widget units. For tests and the log.
        self.last_update_rect = QtCore.QRect()
        #: The last dirty rectangle taken from the document, in physical desktop pixels.
        self.last_dirty_desktop: tuple[float, float, float, float] | None = None
        #: The native handle the window had when its styles were last applied. Compared
        #: after every mode switch, because a recreated window is the failure to catch.
        self.native_id = 0

        self.setWindowTitle(_("ScreenRec - annotations"))
        self._base_flags = (
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(self._base_flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # Moves are wanted only while a button is held, which is the default. Tracking
        # every move across a 2560x1440 desktop would cost a Python call per pixel.
        self.setMouseTracking(False)

        self._monitors: list[winapi.Monitor] = []
        self._desktop = QtCore.QRect()
        self._patches: list[_Patch] = []
        self._fallback = _Patch(0.0, 0.0, 1.0, QtCore.QRect())

        #: Where the stroke in progress began, in desktop pixels - Shift constrains
        #: against this, not against the previous sample.
        self._start: model.Point | None = None
        #: The last *unconstrained* point, so releasing Shift mid-drag can undo the snap.
        self._raw: model.Point | None = None
        self._drawing = False
        self._erasing = False
        self._reported_paint_failure = False

        self.refresh_geometry()
        self._apply_mode()
        i18n.notifier.changed.connect(self.retranslate)

    # ------------------------------------------------------------------ text

    def retranslate(self) -> None:
        """Re-read every string this window owns.

        That is the title alone: the canvas carries no label of its own, only the
        user's drawings. The title is still read - it is what the taskbar and Alt+Tab
        show for the drawing layer while it is up.
        """
        self.setWindowTitle(_("ScreenRec - annotations"))

    # ------------------------------------------------------------------ mode

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch between pass-through and draw **without recreating the window**."""
        if mode not in (self.MODE_PASSTHROUGH, self.MODE_DRAW):
            self.input_problem = (
                f"Unknown canvas mode {mode!r}; the canvas stayed in {self._mode!r}."
            )
            return
        if mode == self._mode:
            # Still reassert the styles: another window's SetWindowLongPtrW, or a Qt
            # show(), can clear them underneath us, and a silent loss of click-through
            # means the user's clicks stop reaching the application they are presenting.
            # Styles only, though: `transition=False` keeps this off the z-order and off
            # the focus, so a reassert cannot bury the toolbar under the canvas or take
            # the keyboard away from the text box being typed into.
            self._apply_mode(transition=False)
            return
        self._cancel_stroke()
        self._mode = mode
        self._apply_mode(transition=True)

    def _apply_mode(self, transition: bool = True) -> None:
        """Put the live window into `self._mode`. The one place flags are touched.

        `transition` says whether the mode actually changed. Everything with an effect
        beyond this window - raising it, activating it, taking the focus, repainting all
        of it - happens only then; a reassert of the same mode re-sets the styles and
        nothing else.
        """
        problems: list[str] = []
        passthrough = self._mode == self.MODE_PASSTHROUGH
        did_raise = False

        wanted = self._base_flags
        if passthrough:
            wanted |= QtCore.Qt.WindowType.WindowTransparentForInput

        # Before the flags, not after: measured here, `QWidgetPrivate::adjustFlags` puts
        # `WindowTransparentForInput` straight back on while `WA_TransparentForMouseEvents`
        # is still set, so clearing the attribute afterwards leaves draw mode holding a
        # flag that says it wants no input at all.
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, passthrough
        )

        before = self._native_handle()
        if self.windowFlags() != wanted:
            was_visible = self.isVisible()
            self.setWindowFlags(wanted)
            # Measured: setWindowFlags leaves a visible widget hidden, and it can drop a
            # geometry set before it (the region selector hit the same thing). Both are
            # repaired here; the native handle survives, which is the part that matters.
            self._apply_geometry()
            if was_visible:
                self.show()
                self.raise_()
                did_raise = True
        after = self._native_handle()
        if before and after and before != after:
            problems.append(
                "The annotation canvas was recreated by a mode switch "
                f"(handle {before} -> {after}); it has lost its place in the z-order."
            )
        if after:
            self.native_id = after

        self.setFocusPolicy(
            QtCore.Qt.FocusPolicy.NoFocus
            if passthrough
            else QtCore.Qt.FocusPolicy.StrongFocus
        )
        self.setCursor(
            QtCore.Qt.CursorShape.ArrowCursor if passthrough else self._cursor_for_tool()
        )

        native = self._apply_native_styles(passthrough)
        if native:
            problems.append(native)

        if transition and not passthrough and self.isVisible():
            # Draw mode owns the keyboard: Esc has to reach `keyPressEvent`, and a press
            # must not fall through to the application underneath. This is the one place
            # the application deliberately takes focus - and only on the way in, because
            # repeating it would steal the caret back from the text box every time the
            # styles are reasserted.
            self.raise_()
            self.activateWindow()
            self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
            did_raise = True

        if transition:
            # Draw mode paints a hit-testable backing over the whole window and
            # pass-through takes it away again; the backing store only holds what has
            # been painted into it, so all of it is invalidated once here. This is a
            # mode change, not the drawing path - nothing like it happens per mouse move.
            self.last_update_rect = QtCore.QRect(self.rect())
            self.update(self.rect())

        self.input_problem = " ".join(problems)

        if did_raise:
            # Last, so that a listener putting its own chrome back on top is acting on a
            # window that is already where it is going to stay.
            self.raised.emit()

    def _native_handle(self) -> int:
        """The HWND, or 0 where there is none (offscreen, or no window yet)."""
        if QtGui.QGuiApplication.platformName() != "windows":
            return 0
        return int(self.winId())

    def _apply_native_styles(self, passthrough: bool) -> str:
        """Set the extended styles the mode needs. Returns "" or what went wrong.

        Qt's `WindowTransparentForInput` already asks for WS_EX_TRANSPARENT, and measured
        here it does set it - but it is applied when the platform window is configured and
        a later show() or style change can lose it, so the bits are set and read back
        directly as well. WS_EX_LAYERED is never cleared: the translucent background needs
        it in both modes.
        """
        if QtGui.QGuiApplication.platformName() != "windows":
            # offscreen and minimal have no HWND, and no desktop to click through.
            return ""
        hwnd = int(self.winId())
        if not hwnd:
            return "The annotation canvas has no native window yet; its mode was not applied."

        current = _get_window_long(hwnd)
        wanted = current | WS_EX_LAYERED
        if passthrough:
            wanted |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        else:
            # Draw mode must take the press, and must be allowed to activate so that Esc
            # and the modifier keys arrive.
            wanted &= ~WS_EX_TRANSPARENT
            wanted &= ~WS_EX_NOACTIVATE

        if wanted != current:
            _set_window_long(hwnd, wanted)
            error = ctypes.get_last_error()
            if _get_window_long(hwnd) != wanted and error:
                return (
                    "The annotation canvas could not be put into "
                    f"{'pass-through' if passthrough else 'draw'} mode "
                    f"(SetWindowLongPtrW error {error})."
                )

        # Prove it rather than trust it: the return value is the *previous* style.
        now = _get_window_long(hwnd)
        transparent = bool(now & WS_EX_TRANSPARENT)
        if transparent != passthrough:
            return (
                "WS_EX_TRANSPARENT is "
                + ("set" if transparent else "clear")
                + " on the annotation canvas, which is wrong for "
                + ("pass-through" if passthrough else "draw")
                + " mode: "
                + (
                    "clicks would be eaten instead of reaching the application below."
                    if transparent is False
                    else "presses would fall through instead of drawing."
                )
            )
        return ""

    # -------------------------------------------------------------- the tool

    def set_tool(self, tool: str) -> None:
        if tool not in model.ALL_TOOLS:
            self.input_problem = (
                f"Unknown tool {tool!r}; the canvas stayed on {self._tool!r}."
            )
            return
        if tool == self._tool:
            return
        self._cancel_stroke()
        self._tool = tool
        if self._mode == self.MODE_DRAW:
            self.setCursor(self._cursor_for_tool())

    def tool(self) -> str:
        return self._tool

    def _cursor_for_tool(self) -> QtCore.Qt.CursorShape:
        if self._tool == model.TOOL_TEXT:
            return QtCore.Qt.CursorShape.IBeamCursor
        if self._tool == model.TOOL_ERASER:
            return QtCore.Qt.CursorShape.PointingHandCursor
        return QtCore.Qt.CursorShape.CrossCursor

    # ---------------------------------------------------------- the geometry

    def desktop_rect(self) -> QtCore.QRect:
        """The union of every monitor, in physical desktop pixels."""
        return QtCore.QRect(self._desktop)

    def monitors(self) -> list[winapi.Monitor]:
        return list(self._monitors)

    def refresh_geometry(self) -> None:
        """Re-read the monitors and resize to their union. Safe to call at any time.

        A monitor unplugged mid-recording shows up here as a shorter list, or - if the
        last one went, or DXGI is momentarily unavailable - as an empty one. An empty list
        is **not** taken as "resize to nothing": a 0x0 always-on-top window would silently
        swallow the whole feature, so the previous geometry is kept and
        `geometry_problem` says what happened.
        """
        monitors = winapi.list_monitors()
        if not monitors:
            self.geometry_problem = (
                "No attached monitor was reported by DXGI, so the annotation canvas kept "
                "its previous geometry; drawings may be in the wrong place until a "
                "monitor comes back."
            )
            self._monitors = []
            self._rebuild_patches()
            return

        self.geometry_problem = ""
        self._monitors = monitors
        self._desktop = _union_of(monitors)
        self._apply_geometry()
        self._rebuild_patches()
        # The whole window is new ground after a geometry change, so the whole of it is
        # the dirty rectangle - stated as a rectangle rather than a bare update().
        self.last_update_rect = QtCore.QRect(self.rect())
        self.update(self.rect())

    def _apply_geometry(self) -> None:
        rect = self._window_rect()
        if rect.isEmpty():
            # The only way here is an empty monitor list, which `refresh_geometry` has
            # already put into `geometry_problem`; resizing to 0x0 would hide the
            # drawings on top of that.
            return
        self.setGeometry(rect)

    def _window_rect(self) -> QtCore.QRect:
        """Where to put the window, in the logical units `setGeometry` wants.

        At 100% scaling - every screen at devicePixelRatio 1.0, which is this machine and
        the offscreen plugin - logical units are physical pixels, so the physical union is
        used directly. If any screen is scaled, Qt's logical desktop is smaller than the
        physical one and handing it the physical union would make an oversized window;
        then the union of the QScreen geometries is used. **That branch is untested here.**
        """
        screens = QtGui.QGuiApplication.screens()
        scaled = any(abs(s.devicePixelRatio() - 1.0) > 1e-6 for s in screens)
        if scaled and screens:
            logical = QtCore.QRect()
            for screen in screens:
                logical = logical.united(screen.geometry())
            return logical
        return QtCore.QRect(self._desktop)

    def _rebuild_patches(self) -> None:
        """Pair every QScreen with its DXGI output, or fall back to one uniform patch."""
        ratio = self.devicePixelRatioF() or 1.0
        self._fallback = _Patch(0.0, 0.0, ratio, QtCore.QRect(self._desktop))

        screens = QtGui.QGuiApplication.screens()
        patches: list[_Patch] = []
        unpaired: list[str] = []
        taken: set[int] = set()
        for screen in screens:
            monitor = self._match_screen(screen, taken)
            if monitor is None:
                unpaired.append(screen.name())
                continue
            taken.add(monitor.index)
            origin = self.mapFromGlobal(screen.geometry().topLeft())
            patches.append(
                _Patch(
                    float(origin.x()),
                    float(origin.y()),
                    screen.devicePixelRatio() or 1.0,
                    QtCore.QRect(monitor.x, monitor.y, monitor.width, monitor.height),
                )
            )

        if unpaired or not patches:
            # Partial pairing is worse than none: half the desktop would be mapped one way
            # and half another, and the seam would move drawings. Use one uniform mapping
            # for all of it and say that it is only exact at uniform scaling.
            self._patches = [self._fallback]
            self.mapping_problem = (
                "These screens could not be paired with a DXGI output, so the whole "
                "canvas uses one uniform coordinate mapping, which is exact only while "
                "every screen is at the same scale: "
                + (", ".join(name or "<unnamed>" for name in unpaired) or "<none>")
                + "."
            )
        else:
            self._patches = patches
            self.mapping_problem = ""

    def _match_screen(
        self, screen: QtGui.QScreen, taken: set[int]
    ) -> winapi.Monitor | None:
        """The DXGI output a QScreen is, or None if it cannot be told apart.

        By geometry, not by name: measured in `overlay`, Qt reports a monitor's friendly
        name ('Display 32 QHD') while DXGI reports '\\\\.\\DISPLAY2', so a name comparison
        never matches here. The name is still tried first, for a Qt build that does report
        the device name.
        """
        free = [m for m in self._monitors if m.index not in taken]
        name = screen.name()
        for monitor in free:
            if monitor.device_name == name:
                return monitor
        box = screen.geometry()
        for monitor in free:
            if (box.x(), box.y(), box.width(), box.height()) == (
                monitor.x,
                monitor.y,
                monitor.width,
                monitor.height,
            ):
                return monitor
        # Scaled: the logical origin is not the physical one, but logical size times the
        # device pixel ratio is the physical size. Accept it only when it is unambiguous.
        ratio = screen.devicePixelRatio() or 1.0
        width = int(round(box.width() * ratio))
        height = int(round(box.height() * ratio))
        candidates = [m for m in free if m.width == width and m.height == height]
        return candidates[0] if len(candidates) == 1 else None

    # ------------------------------------------------------- the conversions

    def to_widget(self, x: float, y: float) -> QtCore.QPointF:
        """Physical desktop pixel -> widget-local logical point."""
        return self._patch_for_desktop(x, y).to_widget(x, y)

    def to_desktop(self, local: QtCore.QPointF) -> model.Point:
        """Widget-local logical point -> physical desktop pixel, as a `model.Point`."""
        patch = self._patch_for_widget(local)
        x, y = patch.to_desktop(local)
        return model.Point(x, y)

    def widget_rect_for(
        self, rect: tuple[float, float, float, float]
    ) -> QtCore.QRect:
        """A desktop rectangle as a widget rectangle, padded and clipped to the window."""
        x, y, width, height = rect
        box: QtCore.QRectF | None = None
        for patch in self._patches:
            mapped = QtCore.QRectF(
                patch.to_widget(x, y), patch.to_widget(x + width, y + height)
            ).normalized()
            # united() with a null rectangle returns the other one, which would quietly
            # drop a zero-size dirty rectangle's position; hence the explicit None.
            box = mapped if box is None else box.united(mapped)
        if box is None:
            return QtCore.QRect()
        padded = box.adjusted(-UPDATE_PAD, -UPDATE_PAD, UPDATE_PAD, UPDATE_PAD)
        return padded.toAlignedRect().intersected(self.rect())

    def _patch_for_desktop(self, x: float, y: float) -> _Patch:
        for patch in self._patches:
            if patch.desktop.contains(int(x), int(y)):
                return patch
        # Between two monitors, or outside them all: the uniform mapping is the only
        # answer that exists, and it is the right one everywhere at a single scale.
        return self._patches[0] if len(self._patches) == 1 else self._fallback

    def _patch_for_widget(self, local: QtCore.QPointF) -> _Patch:
        for patch in self._patches:
            if patch.widget_rect().contains(local):
                return patch
        return self._patches[0] if len(self._patches) == 1 else self._fallback

    # ------------------------------------------------------------ repainting

    def repaint_dirty(self) -> QtCore.QRect:
        """Repaint what the document says has changed, and nothing else.

        Called after every edit, including the ones the controller makes directly -
        undo, redo, clear, hiding the drawings - because the document is the only thing
        that knows their extent. Returns the widget rectangle that was invalidated, which
        is empty when nothing had changed.
        """
        rect = self._document.dirty_rect()
        self._document.clear_dirty()
        self.last_dirty_desktop = rect
        if rect is None:
            self.last_update_rect = QtCore.QRect()
            return QtCore.QRect()
        widget_rect = self.widget_rect_for(rect)
        self.last_update_rect = QtCore.QRect(widget_rect)
        if widget_rect.isEmpty():
            return widget_rect
        # update(rect), never update(): a full-desktop repaint per mouse move is the
        # thing this design exists to avoid.
        self.update(widget_rect)
        return widget_rect

    # ---------------------------------------------------------------- events

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._mode != self.MODE_DRAW:
            event.ignore()
            return
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            # A right press abandons the stroke in progress; it does not draw anything.
            self._cancel_stroke()
            event.accept()
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return

        point = self.to_desktop(event.position())
        event.accept()

        if self._tool == model.TOOL_TEXT:
            # The text tool draws nothing. The controller opens the editor and adds the
            # finished item to the document, which this canvas then paints.
            self.text_requested.emit(int(round(point.x)), int(round(point.y)))
            return

        if self._tool == model.TOOL_ERASER:
            self._erasing = True
            self._document.erase_at(point.x, point.y)
            self.repaint_dirty()
            return

        self._start = point
        self._raw = point
        self._drawing = True
        self._document.begin(self._tool, self._document.settings_for(self._tool), point)
        self.repaint_dirty()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._mode != self.MODE_DRAW:
            event.ignore()
            return
        point = self.to_desktop(event.position())
        event.accept()

        if self._erasing:
            self._document.erase_at(point.x, point.y)
            self.repaint_dirty()
            return
        if not self._drawing:
            return
        self._raw = point
        item = self._document.pending
        previous = item.points[-1] if item is not None and item.points else None
        self._document.extend(self._constrained(point, event.modifiers()))
        if item is not None and item.kind not in model.DRAGGED_TOOLS:
            # Freehand: only the new segment is new ink. The document's mark is the
            # whole stroke's bounding box, which grows without bound - see the module
            # docstring for the measurement.
            self._update_segment(item, previous)
        else:
            # A dragged shape is redrawn from its two corners every time, so its old
            # outline has to be painted out as well: the document's extent is right.
            self.repaint_dirty()

    def _update_segment(
        self, item: model.Item, previous: model.Point | None
    ) -> QtCore.QRect:
        """Invalidate the piece of a freehand stroke added by the last sample.

        The document's dirty mark is dropped here rather than used: it is the union of
        two whole-item bounding boxes, and following it means repainting the entire
        stroke on every mouse move. Nothing else is waiting on that mark during a stroke
        - the press cleared it, and the controller only reads it between edits - so
        clearing it keeps the next `repaint_dirty` honest instead of letting a stale
        stroke-sized rectangle accumulate.
        """
        latest = item.points[-1] if item.points else previous
        if latest is None:
            self.last_dirty_desktop = None
            self.last_update_rect = QtCore.QRect()
            self._document.clear_dirty()
            return QtCore.QRect()
        if previous is None:
            previous = latest
        pad = float(item.width) + SEGMENT_PAD
        rect = (
            min(previous.x, latest.x) - pad,
            min(previous.y, latest.y) - pad,
            abs(latest.x - previous.x) + 2.0 * pad,
            abs(latest.y - previous.y) + 2.0 * pad,
        )
        self._document.clear_dirty()
        self.last_dirty_desktop = rect
        widget_rect = self.widget_rect_for(rect)
        self.last_update_rect = QtCore.QRect(widget_rect)
        if not widget_rect.isEmpty():
            self.update(widget_rect)
        return widget_rect

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._mode != self.MODE_DRAW:
            event.ignore()
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        event.accept()

        if self._erasing:
            self._erasing = False
            return
        if not self._drawing:
            return

        point = self.to_desktop(event.position())
        self._raw = point
        pending = self._document.pending
        previous = pending.points[-1] if pending is not None and pending.points else None
        self._document.extend(self._constrained(point, event.modifiers()))
        item = self._document.finish()
        self._drawing = False
        self._start = None
        self._raw = None
        if item is not None and item is pending and item.kind not in model.DRAGGED_TOOLS:
            # Committing a freehand stroke changes nothing on screen beyond the last
            # segment - the same object was already being painted as the pending one -
            # so this is not the moment to repaint the whole of a long stroke either.
            self._update_segment(item, previous)
        else:
            self.repaint_dirty()
        if item is not None:
            self.stroke_finished.emit(item)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if self._mode != self.MODE_DRAW:
            super().keyPressEvent(event)
            return
        if event.key() == QtCore.Qt.Key.Key_Escape:
            # Whatever Esc means next is the controller's decision - leave draw mode,
            # close the toolbar, stop annotating. The canvas only drops what it was in
            # the middle of, so a half-drawn shape is not left hanging on the screen.
            self._cancel_stroke()
            event.accept()
            self.escaped.emit()
            return
        if event.key() == QtCore.Qt.Key.Key_Shift and self._drawing:
            self._reapply_constraint(QtCore.Qt.KeyboardModifier.ShiftModifier)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QtGui.QKeyEvent) -> None:
        if (
            self._mode == self.MODE_DRAW
            and event.key() == QtCore.Qt.Key.Key_Shift
            and self._drawing
        ):
            # Letting go of Shift mid-drag must release the snap, not leave the shape
            # stuck square until the mouse happens to move again.
            self._reapply_constraint(QtCore.Qt.KeyboardModifier.NoModifier)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            if self._mode == self.MODE_DRAW:
                self._paint_hit_backing(painter, event.rect())
            self._paint(painter, event.rect())
        except Exception as exc:  # a paint must never raise: see the module docstring
            self.paint_problem = f"The annotation canvas failed to paint: {exc!r}"
            if not self._reported_paint_failure:
                # Once per canvas: this can run 60 times a second, and a flood of
                # identical tracebacks would bury whatever else the log has to say.
                self._reported_paint_failure = True
                print(self.paint_problem, file=sys.stderr)
        finally:
            # An exception escaping a live QPainter takes the interpreter down with it.
            painter.end()

    def _paint_hit_backing(
        self, painter: QtGui.QPainter, area: QtCore.QRect
    ) -> None:
        """Make draw mode's empty pixels belong to this window, not to the one below.

        A layered window - which this is, in both modes, because the translucent
        background needs WS_EX_LAYERED - is hit-tested against its per-pixel alpha, and
        the backing store is cleared to alpha 0 before each paint. Without this fill a
        press on anything the user has not already drawn on goes to the application
        underneath: it would activate the program being presented, move its caret, follow
        its links, and draw nothing.

        `CompositionMode_Source` writes the alpha rather than compositing over it, so the
        result is exactly 1 and not 0; it is put back to `SourceOver` immediately, which
        is what every item expects and what the marker's translucency depends on.
        """
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(area, HIT_BACKING)
        painter.setCompositionMode(
            QtGui.QPainter.CompositionMode.CompositionMode_SourceOver
        )

    # ------------------------------------------------------------ the stroke

    def _constrained(
        self, point: model.Point, modifiers: QtCore.Qt.KeyboardModifier
    ) -> model.Point:
        """Apply Shift, using the model's own maths - never a second copy of it."""
        if not bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier):
            return point
        start = self._start
        if start is None or self._tool not in model.DRAGGED_TOOLS:
            return point
        if self._tool in (model.TOOL_RECT, model.TOOL_ELLIPSE):
            return model.constrain_shape(start, point)
        return model.constrain_angle(start, point)

    def _reapply_constraint(self, modifiers: QtCore.Qt.KeyboardModifier) -> None:
        """Redo the last sample under a changed Shift state.

        Only the dragged shapes can be constrained, and only they are redone. For a pen
        or a marker `_constrained` gives the point back unchanged, so this would extend
        the stroke with a sample the model then discards for not having moved - and
        still mark the whole stroke dirty, once per autorepeat of the held Shift key.
        """
        if self._raw is None or self._tool not in model.DRAGGED_TOOLS:
            return
        self._document.extend(self._constrained(self._raw, modifiers))
        self.repaint_dirty()

    def _cancel_stroke(self) -> None:
        self._erasing = False
        if not self._drawing:
            return
        self._drawing = False
        self._start = None
        self._raw = None
        self._document.cancel()
        self.repaint_dirty()

    # ----------------------------------------------------------- the picture

    def _paint(self, painter: QtGui.QPainter, area: QtCore.QRect) -> None:
        document = self._document
        if document.hidden:
            return
        items = document.items
        pending = document.pending
        if pending is not None:
            items.append(pending)
        if not items:
            return

        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

        for patch in self._patches:
            clip = patch.widget_rect().toAlignedRect().intersected(area)
            if clip.isEmpty():
                continue
            visible = patch.desktop_area(clip)
            painter.save()
            try:
                # The clip is set in widget units, before the transform: QPainter keeps a
                # clip in device coordinates, so the transform below does not move it.
                painter.setClipRect(clip)
                painter.setWorldTransform(patch.transform())
                for item in items:
                    left, top, width, height = item.bounds(pad=4)
                    if not visible.intersects(QtCore.QRectF(left, top, width, height)):
                        continue
                    self._paint_item(painter, item)
            finally:
                painter.restore()

    def _paint_item(self, painter: QtGui.QPainter, item: model.Item) -> None:
        if not item.points:
            return
        colour = QtGui.QColor(item.colour)
        if not colour.isValid():
            colour = QtGui.QColor("#FFFFFF")
            self.paint_problem = (
                f"{item.colour!r} is not a colour Qt understands; the item was drawn "
                "white so it is at least visible."
            )

        # The marker's whole purpose is that what is under it stays readable, so it is
        # drawn at its own opacity over the desktop. setOpacity rather than an alpha in
        # the colour: one drawPolyline is one composited primitive, so a stroke that
        # crosses itself does not darken at the crossing.
        painter.setCompositionMode(
            QtGui.QPainter.CompositionMode.CompositionMode_SourceOver
        )
        painter.setOpacity(min(1.0, max(MIN_OPACITY, float(item.opacity))))

        if item.kind == model.TOOL_TEXT:
            self._paint_text(painter, item, colour)
            return

        pen = QtGui.QPen(colour)
        pen.setWidthF(max(0.5, float(item.width)))
        # Round caps and joins: a marker with flat caps shows every sample as a notch.
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        if item.kind == model.TOOL_ARROW:
            self._paint_arrow(painter, item, colour)
        elif item.kind in (model.TOOL_RECT, model.TOOL_ELLIPSE):
            if len(item.points) < 2:
                return
            box = QtCore.QRectF(
                QtCore.QPointF(item.points[0].x, item.points[0].y),
                QtCore.QPointF(item.points[-1].x, item.points[-1].y),
            ).normalized()
            if item.kind == model.TOOL_RECT:
                painter.drawRect(box)
            else:
                painter.drawEllipse(box)
        elif item.kind == model.TOOL_LINE:
            if len(item.points) < 2:
                return
            painter.drawLine(
                QtCore.QPointF(item.points[0].x, item.points[0].y),
                QtCore.QPointF(item.points[-1].x, item.points[-1].y),
            )
        else:
            # pen, marker, and anything added to the model later: a freehand polyline.
            if len(item.points) < 2:
                painter.drawPoint(QtCore.QPointF(item.points[0].x, item.points[0].y))
                return
            painter.drawPolyline(
                QtGui.QPolygonF([QtCore.QPointF(p.x, p.y) for p in item.points])
            )

    def _paint_arrow(
        self, painter: QtGui.QPainter, item: model.Item, colour: QtGui.QColor
    ) -> None:
        if len(item.points) < 2:
            return
        start = item.points[0]
        end = item.points[-1]
        dx, dy = end.x - start.x, end.y - start.y
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        angle = math.atan2(dy, dx)
        head = max(ARROW_HEAD_MIN, item.width * ARROW_HEAD)
        # Stop the shaft short of the tip so the line does not show through the head.
        shaft = max(0.0, length - head * 0.6)
        painter.drawLine(
            QtCore.QPointF(start.x, start.y),
            QtCore.QPointF(
                start.x + math.cos(angle) * shaft, start.y + math.sin(angle) * shaft
            ),
        )
        spread = math.radians(ARROW_SPREAD_DEGREES)
        painter.setBrush(colour)
        painter.drawPolygon(
            QtGui.QPolygonF(
                [
                    QtCore.QPointF(end.x, end.y),
                    QtCore.QPointF(
                        end.x - head * math.cos(angle - spread),
                        end.y - head * math.sin(angle - spread),
                    ),
                    QtCore.QPointF(
                        end.x - head * math.cos(angle + spread),
                        end.y - head * math.sin(angle + spread),
                    ),
                ]
            )
        )
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

    def _paint_text(
        self, painter: QtGui.QPainter, item: model.Item, colour: QtGui.QColor
    ) -> None:
        if not item.text:
            return
        font = QtGui.QFont(self.font())
        # Pixels, not points: the size is in desktop pixels and the patch transform
        # scales it. A point size would also be scaled by the screen's logical DPI and
        # the text would come out a different size from the one that was chosen.
        font.setPixelSize(max(1, int(round(item.font_size))))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(colour))
        metrics = QtGui.QFontMetricsF(font)
        anchor = item.points[0]
        baseline = anchor.y + metrics.ascent()
        # Qt's drawText takes the text as it comes; no key codes are inspected anywhere
        # in this module, so Cyrillic, combining marks and anything else an input method
        # produces are drawn exactly as the document holds them.
        for line in item.text.split("\n"):
            painter.drawText(QtCore.QPointF(anchor.x, baseline), line)
            baseline += metrics.lineSpacing()
