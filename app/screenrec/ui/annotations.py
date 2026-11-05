"""Drawing on the screen: the part that decides what the pieces mean together.

The canvas knows how to draw, the toolbar knows how to be a toolbar and the text box
knows how to take Cyrillic. None of them knows when the user is drawing and when they
are working, which hotkey does what, or that the panel must get out of the way of the
pointer. That is here.

**The two modes, and why they are the whole feature.**

* *Work with the application* — the drawings stay on screen, and the mouse and keyboard
  reach whatever is underneath. The canvas is click-through.
* *Draw* — the mouse belongs to the current tool, and a click must not reach the
  application below it.

A hold-to-work key gives the third thing people actually want: drop into the application
for a moment without losing the tool. The toggle is a global hotkey because the keyboard
belongs to Maya, or to a browser, at the moment it is pressed.

**Getting out must always be possible.** Collapsing the panel keeps the mode button;
the global hotkey works whatever has focus; and if the toolbar is somehow gone, Esc on
the canvas leaves draw mode. A drawing layer that can trap the pointer is worse than no
drawing layer.

**What is recorded and what is not** is decided in one place - `surfaces.FloatingSurface`
hides itself from capture, and the canvas, which is a plain QWidget, does not. The
measurement behind that is in docs/ANNOTATE_CONTRACT.md.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n, winapi
from ..annotate import model as model_mod
from ..annotate.canvas import Canvas
from ..annotate.textbox import TextBox
from ..annotate.toolbar import Toolbar
from ..i18n import _

#: Single-key shortcuts, live only while drawing so they cannot disturb another program.
TOOL_KEYS = {
    QtCore.Qt.Key.Key_P: model_mod.TOOL_PEN,
    QtCore.Qt.Key.Key_M: model_mod.TOOL_MARKER,
    QtCore.Qt.Key.Key_L: model_mod.TOOL_LINE,
    QtCore.Qt.Key.Key_A: model_mod.TOOL_ARROW,
    QtCore.Qt.Key.Key_R: model_mod.TOOL_RECT,
    QtCore.Qt.Key.Key_O: model_mod.TOOL_ELLIPSE,
    QtCore.Qt.Key.Key_T: model_mod.TOOL_TEXT,
    QtCore.Qt.Key.Key_E: model_mod.TOOL_ERASER,
}


def _drawings(count: int) -> str:
    """"drawing" / "drawings", or the right one of the three Russian forms.

    Russian counts in three forms chosen by the last digit, with 11 to 14 taking the
    many form whatever their last digit is; `i18n.plural` is the rule and these are the
    words it picks between.
    """
    return i18n.plural(
        count,
        i18n.pick("plural.drawing.one", "drawing"),
        i18n.pick("plural.drawing.few", "drawings"),
        i18n.pick("plural.drawing.many", "drawings"),
    )


class AnnotationController(QtCore.QObject):
    """Owns the canvas, the toolbar and the text box, and the mode they are in."""

    mode_changed = QtCore.Signal(str)
    #: The canvas has come to the front and the owner's own chrome must follow it up.
    restack_needed = QtCore.Signal()

    def __init__(self, settings, say=None, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._say = say or (lambda text, warn=False: None)

        self.document = model_mod.Document()
        self.canvas = Canvas(self.document)
        self.toolbar = Toolbar(self.document)
        self.textbox = TextBox(self.document.settings_for(model_mod.TOOL_TEXT))

        self._tool = model_mod.TOOL_PEN
        self._temporary_passthrough = False
        self._warned_about_capture = False

        self._wire()
        # Windows raises a window when it is activated, and the canvas has to be
        # activatable in draw mode so it can read Esc and the single-key shortcuts.
        # The consequence, measured: the FIRST stroke put the canvas above the toolbar
        # and every button on it went dead - the user could draw over the buttons but
        # not press them. So every press on the canvas is followed by putting the
        # chrome back on top.
        self.canvas.installEventFilter(self)
        self.canvas.set_tool(self._tool)
        self.canvas.set_mode(Canvas.MODE_PASSTHROUGH)
        self.canvas.show()

        if len(getattr(settings, "annotate_toolbar_pos", []) or []) == 2:
            position = settings.annotate_toolbar_pos
            self.toolbar.place(position[0], position[1])
        else:
            self.toolbar.place_default(winapi.list_monitors())
        self.toolbar.set_collapsed(bool(getattr(settings, "annotate_collapsed", False)))
        self.toolbar.set_tool(self._tool)
        self.toolbar.set_mode(Canvas.MODE_PASSTHROUGH)
        self._sync_history()

    # ------------------------------------------------------------------ wire

    def _wire(self) -> None:
        self.canvas.stroke_finished.connect(self._stroke_finished)
        self.canvas.text_requested.connect(self._open_text)
        self.canvas.escaped.connect(self._escape)

        self.toolbar.tool_changed.connect(self.set_tool)
        self.toolbar.mode_toggled.connect(self.toggle_mode)
        self.toolbar.undo_requested.connect(self.undo)
        self.toolbar.redo_requested.connect(self.redo)
        self.toolbar.clear_requested.connect(self.clear)
        self.toolbar.visibility_toggled.connect(self.toggle_visibility)
        self.toolbar.settings_changed.connect(self._settings_changed)
        self.toolbar.collapsed_changed.connect(self._collapsed_changed)
        self.toolbar.moved.connect(self._toolbar_moved)

        self.textbox.committed.connect(self._text_committed)
        self.textbox.cancelled.connect(self._text_cancelled)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """Watch the canvas for anything that would bury the toolbar."""
        if watched is self.canvas and event.type() in (
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QEvent.Type.WindowActivate,
            QtCore.QEvent.Type.FocusIn,
        ):
            # Deferred: Windows finishes reordering after this event returns, so
            # raising now would be undone a moment later.
            QtCore.QTimer.singleShot(0, self.restack)
        return False

    def restack(self) -> None:
        """Put the toolbar, the text box and the owner's chrome above the canvas.

        `raise_` does not activate, so none of this steals focus from the canvas and
        the keyboard keeps working while drawing.
        """
        if self.toolbar.isVisible():
            self.toolbar.raise_()
        for popover in self.toolbar.findChildren(QtWidgets.QWidget):
            if popover.isWindow() and popover.isVisible():
                popover.raise_()
        if self.textbox.isVisible():
            self.textbox.raise_()
        self.restack_needed.emit()

    # ------------------------------------------------------------------ mode

    def mode(self) -> str:
        return self.canvas.mode()

    def drawing(self) -> bool:
        return self.canvas.mode() == Canvas.MODE_DRAW

    def toggle_mode(self) -> None:
        self.set_mode(
            Canvas.MODE_PASSTHROUGH if self.drawing() else Canvas.MODE_DRAW
        )

    def set_mode(self, mode: str) -> None:
        if mode == self.canvas.mode():
            return
        if mode == Canvas.MODE_DRAW:
            self.show()
            self._keep_toolbar_clear_of_pointer()
        else:
            self.textbox.cancel()
        self.canvas.set_mode(mode)
        self.toolbar.set_mode(mode)
        self.mode_changed.emit(mode)
        self._say(
            _("Drawing. The hotkey switches back to working with the application.")
            if mode == Canvas.MODE_DRAW
            else _("Working with the application; the drawings stay on screen.")
        )

    def hold_passthrough(self, held: bool) -> None:
        """Dip into the application while a key is held, and come back to the tool."""
        if held and self.drawing():
            self._temporary_passthrough = True
            self.canvas.set_mode(Canvas.MODE_PASSTHROUGH)
            self.toolbar.set_mode(Canvas.MODE_PASSTHROUGH)
        elif not held and self._temporary_passthrough:
            self._temporary_passthrough = False
            self.canvas.set_mode(Canvas.MODE_DRAW)
            self.toolbar.set_mode(Canvas.MODE_DRAW)

    def _escape(self) -> None:
        if self.textbox.isVisible():
            self.textbox.cancel()
            return
        self.set_mode(Canvas.MODE_PASSTHROUGH)

    # ------------------------------------------------------------------ tools

    def set_tool(self, tool: str) -> None:
        if tool not in model_mod.ALL_TOOLS:
            self._say(_("There is no {tool} tool.").format(tool=repr(tool)), True)
            return
        self._tool = tool
        self.canvas.set_tool(tool)
        self.toolbar.set_tool(tool)
        if tool == model_mod.TOOL_TEXT:
            self.textbox.set_settings(self.document.settings_for(tool))

    def handle_key(self, key: int) -> bool:
        """A single-key tool shortcut, honoured only while drawing and not typing."""
        if not self.drawing() or self.textbox.isVisible():
            return False
        tool = TOOL_KEYS.get(key)
        if tool is None:
            return False
        self.set_tool(tool)
        return True

    def _settings_changed(self, tool: str) -> None:
        if tool == model_mod.TOOL_TEXT:
            self.textbox.set_settings(self.document.settings_for(tool))
        self.canvas.update()

    # --------------------------------------------------------------- history

    def _sync_history(self) -> None:
        self.toolbar.set_history(self.document.can_undo, self.document.can_redo)
        self.toolbar.set_hidden_drawings(self.document.hidden)

    def _stroke_finished(self, _item: object) -> None:
        self._sync_history()

    def undo(self) -> None:
        if self.document.undo():
            self.canvas.repaint_dirty()
        self._sync_history()

    def redo(self) -> None:
        if self.document.redo():
            self.canvas.repaint_dirty()
        self._sync_history()

    def clear(self) -> None:
        removed = self.document.clear()
        self.canvas.repaint_dirty()
        self._sync_history()
        if removed:
            self._say(
                _("Cleared {count} {word} - undo brings them back.").format(
                    count=removed, word=_drawings(removed)
                )
            )

    def toggle_visibility(self) -> None:
        self.document.set_hidden(not self.document.hidden)
        self.canvas.repaint_dirty()
        self._sync_history()
        self._say(
            _("Drawings hidden; they are not deleted.")
            if self.document.hidden
            else _("Drawings shown again.")
        )

    # ------------------------------------------------------------------ text

    def _open_text(self, x: int, y: int) -> None:
        self.textbox.set_settings(self.document.settings_for(model_mod.TOOL_TEXT))
        self.textbox.open_at(x, y)
        # The panel must not sit on top of the box being typed into.
        geometry = self.textbox.geometry()
        self.toolbar.avoid(
            (geometry.x(), geometry.y(), geometry.width(), geometry.height())
        )
        self._text_anchor = (x, y)

    def _text_committed(self, text: str) -> None:
        text = text.strip("\n")
        if not text:
            return
        x, y = getattr(self, "_text_anchor", (0, 0))
        settings = self.document.settings_for(model_mod.TOOL_TEXT)
        self.document.add(
            model_mod.Item(
                kind=model_mod.TOOL_TEXT,
                points=[model_mod.Point(float(x), float(y))],
                text=text,
                colour=settings.colour,
                width=settings.width,
                font_size=settings.font_size,
            )
        )
        self.canvas.repaint_dirty()
        self._sync_history()

    def _text_cancelled(self) -> None:
        pass  # nothing was added; the canvas never changed

    # -------------------------------------------------------------- placement

    def _keep_toolbar_clear_of_pointer(self) -> None:
        """Never let the panel be sitting under the pointer when drawing starts."""
        try:
            x, y = winapi.cursor_pos()
        except Exception as exc:
            self._say(
                _("The pointer position is unreadable: {error}").format(error=exc),
                True,
            )
            return
        self.toolbar.avoid((x - 48, y - 48, 96, 96))

    def _toolbar_moved(self, x: int, y: int) -> None:
        if hasattr(self.settings, "annotate_toolbar_pos"):
            self.settings.annotate_toolbar_pos = [x, y]

    def _collapsed_changed(self, collapsed: bool) -> None:
        if hasattr(self.settings, "annotate_collapsed"):
            self.settings.annotate_collapsed = collapsed

    # ------------------------------------------------------------ appearance

    def show(self) -> None:
        self.canvas.refresh_geometry()
        self.canvas.show()
        self.toolbar.show()
        self._check_capture_exclusion()

    def hide(self) -> None:
        self.textbox.cancel()
        self.set_mode(Canvas.MODE_PASSTHROUGH)
        self.toolbar.hide()

    def _check_capture_exclusion(self) -> None:
        """Say so once if the panel is going to end up in the recording.

        `WDA_EXCLUDEFROMCAPTURE` needs Windows 10 2004 or newer. Where it does not take,
        the toolbar is captured like any other window, and the user deserves to know
        before they watch it back rather than after.
        """
        if self._warned_about_capture:
            return
        if not self.toolbar.isVisible():
            return
        self._warned_about_capture = True
        if not self.toolbar.excluded_from_capture:
            self._say(
                _(
                    "This Windows version cannot hide a window from screen capture, "
                    "so the drawing panel will appear in the recording. Collapse it, "
                    "or move it to another screen."
                ),
                True,
            )

    def new_recording(self) -> None:
        """A fresh recording starts with a clean sheet, keeping the tools as they were."""
        self.document.reset()
        self.canvas.update()
        self._sync_history()

    def shutdown(self) -> None:
        """Give input back, whatever state things were left in."""
        try:
            self.textbox.cancel()
        except Exception:
            # Shutting down; a text box that will not close must not block the rest.
            pass
        try:
            self.canvas.set_mode(Canvas.MODE_PASSTHROUGH)
        except Exception as exc:
            self._say(
                _("The drawing layer would not release the mouse: {error}").format(
                    error=exc
                ),
                True,
            )
        for widget in (self.textbox, self.toolbar, self.canvas):
            try:
                widget.close()
                widget.deleteLater()
            except Exception as exc:
                # The process is going away and the window manager will take these with
                # it; the one thing that would matter - input still being grabbed - was
                # released above, and that failure is reported.
                self._say(
                    _("A drawing window would not close: {error}").format(error=exc),
                    True,
                )
