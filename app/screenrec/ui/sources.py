"""The source picker: which screen, which area, or which window.

One floating surface, three segments, and a grid of painted cards. The card is the whole
point of this module: a picture of the thing you are about to record beats a line of text
in a combo box, because a screen called ``\\\\.\\DISPLAY2`` means nothing and a thumbnail of
it means everything.

Three things here are deliberate and easy to get wrong later:

* **The cards are painted, not styled.** A rounded panel, a thumbnail well, two lines of
  text, a hover lift and a selection ring are five states of one drawing; expressing that
  as a style sheet means five selectors, a layout per card and a repaint cost per hover.
  `SourceCard` is one `QAbstractButton` that draws all of it, and it serves monitors and
  windows alike - they differ only in the icon, the two strings and the payload.
* **A card never shows a hole.** Thumbnails arrive late (a grab costs 200-400 ms) or not
  at all, so the well starts with the matching icon from `icons` in ``text_faint`` and
  keeps it if the grab fails. There is no spinner: a spinner that outlives its request is
  worse than a placeholder that does not.
* **The top strip is the handle.** A frameless window has no title bar, so pressing the
  header and dragging moves the surface - `surfaces.SurfaceDrag`, the same press /
  2 px slop / snap-to-edge / clamp the bar is moved by. A press on a control in that
  strip stays that control. The position is not remembered: `popup_near` puts the
  picker back beside its chip every time it opens, which is what makes it a popover.
* **Nothing here takes focus.** `FloatingSurface` is a non-activating `Tool` window, which
  means it never receives a focus-out and cannot close itself the way a `Popup` would. So
  dismissal is driven from an event filter on the application: Escape cancels, and a press
  anywhere outside the surface (and outside the widget that opened it) cancels. The filter
  is installed on show and removed on hide, so a hidden picker watches nothing.

**Known limit, stated rather than hidden:** a click into a *different application* does
not dismiss the picker. Qt only sees presses delivered to its own windows, and the
surface deliberately never activates, so there is no focus-out to hang the close on.
`QGuiApplication.applicationStateChanged` covers the case where this application was
active and then was not; beyond that the caller (the bar) closes the picker when its chip
is clicked again.
"""
from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui, QtWidgets

from .. import config, i18n, winapi
from ..i18n import _, pick
from . import icons, surfaces, theme

__all__ = ["SourceCard", "SourcePicker"]

# -- geometry ---------------------------------------------------------------
#
# The numbers are derived from the two the contract fixes - a 460 px surface and a
# 16:10.5 well - rather than typed in, so changing either one keeps the grid square.

WIDTH = 460
MAX_HEIGHT = 520
SIDE_MARGIN = 14
TOP_MARGIN = 12
HEADER_H = 30
BODY_GAP = 10
GRID_GAP = 10
#: Room on every side of a card for the 2 px hover lift and the selection ring.
CARD_MARGIN = 4
CARD_PAD = 6
CARD_LIFT = 2
WELL_ASPECT = 10.5 / 16.0
NAME_H = 15
DETAIL_H = 13
TEXT_GAP = 7
LINE_GAP = 2
EDGE = 8  # how close to a screen edge `popup_near` is willing to put the surface
THUMB_WIDTH = 240

#: Two columns inside the surface, allowing for a scroll bar that may or may not be there.
CARD_W = (WIDTH - 2 * SIDE_MARGIN - 10 - GRID_GAP) // 2
_CARD_INNER_W = CARD_W - 2 * CARD_MARGIN
WELL_W = _CARD_INNER_W - 2 * CARD_PAD
WELL_H = round(WELL_W * WELL_ASPECT)
CARD_H = 2 * CARD_PAD + WELL_H + TEXT_GAP + NAME_H + LINE_GAP + DETAIL_H
CARD_TOTAL_H = CARD_H + 2 * CARD_MARGIN


def _report(message: str) -> None:
    """Say something on every failure path, without pulling in a logging setup.

    stderr rather than stdout: the test package puts both on UTF-8, and a picker message
    must never land in the middle of a caller's parsed output.
    """
    print(f"sources: {message}", file=sys.stderr)


def _screens_word(count: int) -> str:
    """The right form of "screen" for `count` - three of them in Russian.

    The forms live in the catalogue like every other word; `pick` is what fetches one
    by key rather than by sentence.
    """
    return i18n.plural(
        count,
        pick("plural.source.screen.one", "screen"),
        pick("plural.source.screen.few", "screens"),
        pick("plural.source.screen.many", "screens"),
    )


def _font(size: float, bold: bool = False) -> QtGui.QFont:
    font = QtGui.QFont()
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


# --------------------------------------------------------------------- a card


class SourceCard(QtWidgets.QAbstractButton):
    """One recordable thing: a thumbnail well over a name and a detail line.

    The same class serves a monitor and a window. `payload` is what the picker emits for
    it - a DXGI output index for a monitor, an ``HWND`` for a window - and `key` is the
    string the thumbnail loader hands back, so a late thumbnail can find its card.
    """

    def __init__(
        self,
        key: str,
        payload: object,
        icon_kind: str,
        name: str,
        detail: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.payload = payload
        if icon_kind not in icons.KINDS:
            _report(f"unknown icon {icon_kind!r} for card {key!r}; using 'window'")
            icon_kind = "window"
        self.icon_kind = icon_kind
        #: Set by the picker so it can ask the loader for this card's picture.
        self.monitor: winapi.Monitor | None = None
        self.window_info: winapi.WindowInfo | None = None

        self._name = name
        self._detail = detail
        self._thumb: QtGui.QPixmap | None = None
        self._scaled: QtGui.QPixmap | None = None
        self._selected = False

        self.setFixedSize(CARD_W, CARD_TOTAL_H)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setToolTip(f"{name}\n{detail}" if detail else name)

    # -- state -------------------------------------------------------------

    def set_selected(self, value: bool) -> None:
        if value != self._selected:
            self._selected = value
            self.update()

    def is_selected(self) -> bool:
        return self._selected

    def has_thumbnail(self) -> bool:
        return self._thumb is not None and not self._thumb.isNull()

    def set_thumbnail(self, pixmap: QtGui.QPixmap | None) -> None:
        """Hand the card its picture. `None` leaves the icon placeholder in place."""
        if pixmap is None or pixmap.isNull():
            # Not an error here - the loader reports *why* it failed; this is only the
            # decision to keep showing the placeholder rather than blank the well.
            self._thumb = None
            self._scaled = None
            self.update()
            return
        self._thumb = pixmap
        self._scaled = None
        self.update()

    # -- painting ----------------------------------------------------------

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(CARD_W, CARD_TOTAL_H)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _card_rect(self) -> QtCore.QRectF:
        hovered = self.underMouse() and self.isEnabled()
        top = float(CARD_MARGIN - (CARD_LIFT if hovered else 0))
        return QtCore.QRectF(float(CARD_MARGIN), top, float(_CARD_INNER_W), float(CARD_H))

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        colours = theme.COLOURS
        hovered = self.underMouse() and self.isEnabled()
        pressed = self.isDown()
        rect = self._card_rect()
        radius = theme.RADIUS["card"]

        if self._selected:
            ring = QtGui.QColor(colours["accent"])
            ring.setAlpha(70)
            painter.setPen(QtGui.QPen(ring, 2.5))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(-2.0, -2.0, 2.0, 2.0), radius + 2, radius + 2
            )

        if pressed:
            fill = colours["line"]
        elif hovered:
            fill = colours["hover"]
        else:
            fill = colours["panel"]
        if self._selected:
            pen = QtGui.QPen(QtGui.QColor(colours["accent"]), 1.5)
        else:
            pen = QtGui.QPen(
                QtGui.QColor(colours["line_strong" if hovered else "line"]), 1.0
            )
        painter.setPen(pen)
        painter.setBrush(QtGui.QColor(fill))
        inset = pen.widthF() / 2.0
        painter.drawRoundedRect(
            rect.adjusted(inset, inset, -inset, -inset), radius, radius
        )

        well = QtCore.QRectF(
            rect.left() + CARD_PAD,
            rect.top() + CARD_PAD,
            float(WELL_W),
            float(WELL_H),
        )
        self._paint_well(painter, well)

        text_left = rect.left() + CARD_PAD + 1
        text_width = max(10.0, float(WELL_W) - 2.0)
        name_rect = QtCore.QRectF(
            text_left, well.bottom() + TEXT_GAP, text_width, float(NAME_H)
        )
        detail_rect = QtCore.QRectF(
            text_left,
            name_rect.bottom() + LINE_GAP,
            text_width,
            float(DETAIL_H),
        )
        align = int(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )

        name_font = _font(9.5, bold=self._selected)
        painter.setFont(name_font)
        lit = hovered or self._selected
        painter.setPen(QtGui.QColor(colours["text" if lit else "text_soft"]))
        painter.drawText(
            name_rect,
            align,
            QtGui.QFontMetricsF(name_font).elidedText(
                self._name, QtCore.Qt.TextElideMode.ElideMiddle, int(text_width)
            ),
        )

        detail_font = _font(8.5)
        painter.setFont(detail_font)
        painter.setPen(QtGui.QColor(colours["text_dim"]))
        painter.drawText(
            detail_rect,
            align,
            QtGui.QFontMetricsF(detail_font).elidedText(
                self._detail, QtCore.Qt.TextElideMode.ElideRight, int(text_width)
            ),
        )

    def _paint_well(self, painter: QtGui.QPainter, well: QtCore.QRectF) -> None:
        colours = theme.COLOURS
        small = theme.RADIUS["small"]
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(colours["bg"]))
        painter.drawRoundedRect(well, small, small)

        if self._thumb is not None and not self._thumb.isNull():
            target = well.size().toSize()
            if self._scaled is None or self._scaled.size() != target:
                self._scaled = self._thumb.scaled(
                    target,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            path = QtGui.QPainterPath()
            path.addRoundedRect(well, small, small)
            painter.save()
            try:
                painter.setClipPath(path)
                source = QtCore.QRectF(
                    (self._scaled.width() - well.width()) / 2.0,
                    (self._scaled.height() - well.height()) / 2.0,
                    well.width(),
                    well.height(),
                )
                painter.drawPixmap(well, self._scaled, source)
            finally:
                painter.restore()
            return

        glyph = min(38.0, well.height() * 0.42)
        icons.draw(
            painter,
            self.icon_kind,
            QtCore.QRectF(
                well.center().x() - glyph / 2.0,
                well.center().y() - glyph / 2.0,
                glyph,
                glyph,
            ),
            colours["text_faint"],
            weight=1.7,
        )


# ---------------------------------------------------------------- the grid


class _CardGrid(QtWidgets.QScrollArea):
    """Two columns of `SourceCard`, scrolling when there are more than fit."""

    card_clicked = QtCore.Signal(object)

    def __init__(self, empty_text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: list[SourceCard] = []
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The application sheet gives every QScrollArea a border and a radius, which
        # would draw a second box inside the surface. Overridden here rather than in the
        # sheet, because only this one is inside a painted panel.
        self.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.viewport().setAutoFillBackground(False)

        holder = QtWidgets.QWidget()
        holder.setObjectName("cardBody")
        self._grid = QtWidgets.QGridLayout(holder)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(GRID_GAP)
        self._grid.setVerticalSpacing(GRID_GAP)
        # Centred, not left-aligned: the two columns are sized to leave room for a scroll
        # bar that is only there when the list is long, and a left-aligned grid therefore
        # sits 10 px off-centre whenever it is not.
        self._grid.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignHCenter
        )
        self._empty = surfaces.label(empty_text, "text_dim", 9.0)
        self._empty.setWordWrap(True)
        self._empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._empty.setVisible(False)
        self._grid.addWidget(self._empty, 0, 0, 1, 2)
        self.setWidget(holder)

    def cards(self) -> list[SourceCard]:
        return list(self._cards)

    def set_empty_text(self, text: str) -> None:
        self._empty.setText(text)

    def set_cards(self, specs: list[dict[str, object]]) -> list[SourceCard]:
        """Replace every card. `specs` keys: key, payload, icon, name, detail, and
        optionally monitor / window_info for the thumbnail request."""
        for card in self._cards:
            self._grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards = []

        for index, spec in enumerate(specs):
            card = SourceCard(
                str(spec["key"]),
                spec["payload"],
                str(spec["icon"]),
                str(spec["name"]),
                str(spec.get("detail", "")),
                self.widget(),
            )
            card.monitor = spec.get("monitor")  # type: ignore[assignment]
            card.window_info = spec.get("window_info")  # type: ignore[assignment]
            card.clicked.connect(lambda _checked=False, c=card: self.card_clicked.emit(c))
            self._grid.addWidget(card, index // 2 + 1, index % 2)
            card.show()
            self._cards.append(card)

        self._empty.setVisible(not self._cards)
        self.widget().adjustSize()
        return list(self._cards)

    def natural_height(self) -> int:
        """How tall the grid would like to be, before the 520 px cap bites."""
        if not self._cards:
            return max(60, self._empty.sizeHint().height() + 36)
        rows = (len(self._cards) + 1) // 2
        return rows * CARD_TOTAL_H + (rows - 1) * GRID_GAP


class _RegionPage(QtWidgets.QWidget):
    """The Region segment: pick a new rectangle, or use the remembered one."""

    select_requested = QtCore.Signal()
    reuse_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardBody")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(8)

        self.select_button = QtWidgets.QPushButton(_("Select an area…"), self)
        self.select_button.setObjectName("primary")
        self.select_button.setMinimumHeight(34)
        self.select_button.clicked.connect(self.select_requested.emit)
        layout.addWidget(self.select_button)

        self._hint = surfaces.label(
            _("Drag a rectangle anywhere on the desktop. Esc cancels the drag."),
            "text_dim",
            9.0,
        )
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._remembered = surfaces.label("", "text_soft", 9.0)
        self._remembered.setWordWrap(True)
        layout.addWidget(self._remembered)

        self.reuse_button = QtWidgets.QPushButton(_("Use it again"), self)
        self.reuse_button.setObjectName("ghost")
        self.reuse_button.clicked.connect(self.reuse_requested.emit)
        layout.addWidget(self.reuse_button)
        layout.addStretch(1)

        #: The rectangle the line is written from, so it can be written again in
        #: another language without the picker having to hand it over twice.
        self._rect: tuple[int, int, int, int] | None = None
        self.set_region(None)
        i18n.notifier.changed.connect(self.retranslate)

    def set_region(self, rect: tuple[int, int, int, int] | None) -> None:
        self._rect = rect
        if rect is None:
            self._remembered.setText("")
            self._remembered.setVisible(False)
            self.reuse_button.setVisible(False)
            return
        x, y, width, height = rect
        self._remembered.setText(
            _("Last area  {width} × {height} at {x}, {y}").format(
                width=width, height=height, x=x, y=y
            )
        )
        self._remembered.setVisible(True)
        self.reuse_button.setVisible(True)

    def retranslate(self, code: str = "") -> None:
        """Re-read the page. Connected to `i18n.notifier.changed`."""
        self.select_button.setText(_("Select an area…"))
        self._hint.setText(
            _("Drag a rectangle anywhere on the desktop. Esc cancels the drag.")
        )
        self.reuse_button.setText(_("Use it again"))
        self.set_region(self._rect)

    def natural_height(self) -> int:
        return self.sizeHint().height()


# -------------------------------------------------------------- the picker


class SourcePicker(surfaces.FloatingSurface):
    """Screen / Region / Window, as a popover over the bar.

    `chosen` carries one of `config.SOURCE_MONITOR`, `config.SOURCE_REGION` or
    `config.SOURCE_WINDOW` and the payload the recorder needs: a DXGI output index, a
    ``(x, y, w, h)`` desktop rectangle, or an ``HWND``.
    """

    chosen = QtCore.Signal(str, object)
    cancelled = QtCore.Signal()

    def __init__(
        self,
        settings: config.Settings,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(
            radius=theme.RADIUS["surface"], fill="panel_raised", border="line",
            parent=parent,
        )
        self.settings = settings
        self._mode = settings.source_kind
        self._anchor: QtWidgets.QWidget | None = None
        self._loader: QtCore.QObject | None = None
        self._loader_problem = ""
        self._cards_by_key: dict[str, SourceCard] = {}
        self._filtering = False
        #: Dragging the top strip moves the surface; see `mousePressEvent`.
        self._drag = surfaces.SurfaceDrag(self)

        self.setFixedWidth(WIDTH)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(SIDE_MARGIN, TOP_MARGIN, SIDE_MARGIN, TOP_MARGIN)
        outer.setSpacing(BODY_GAP)

        header = QtWidgets.QWidget(self)
        header.setObjectName("formRow")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.segments = surfaces.Segmented(self._segment_options(), header)
        self.segments.changed.connect(self._on_segment)
        header_layout.addWidget(self.segments)
        header_layout.addStretch(1)
        self.refresh_button = surfaces.IconButton(
            "refresh", _("Look again for screens and windows"), "quiet", 28, header
        )
        self.refresh_button.clicked.connect(self.refresh)
        header_layout.addWidget(self.refresh_button)
        header.setFixedHeight(HEADER_H)
        outer.addWidget(header)
        #: The strip the surface is dragged by. Kept so `_in_header` can measure it
        #: rather than repeat the margins as a second set of numbers.
        self.header = header

        self._stack = QtWidgets.QStackedWidget(self)
        self.screens = _CardGrid(_("No screens were reported by DXGI."), self._stack)
        self.region = _RegionPage(self._stack)
        self.windows = _CardGrid(_("No windows big enough to record."), self._stack)
        self._pages: dict[str, QtWidgets.QWidget] = {
            config.SOURCE_MONITOR: self.screens,
            config.SOURCE_REGION: self.region,
            config.SOURCE_WINDOW: self.windows,
        }
        for key in (config.SOURCE_MONITOR, config.SOURCE_REGION, config.SOURCE_WINDOW):
            self._stack.addWidget(self._pages[key])
        outer.addWidget(self._stack)

        self.message = surfaces.label("", "error_text", 9.0)
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        outer.addWidget(self.message)

        self.screens.card_clicked.connect(self._pick_screen_card)
        self.windows.card_clicked.connect(self._pick_window)
        self.region.select_requested.connect(self._pick_new_region)
        self.region.reuse_requested.connect(self._reuse_region)

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_loader)
            app.applicationStateChanged.connect(self._on_app_state)

        i18n.notifier.changed.connect(self.retranslate)
        self.refresh()

    # -- language -----------------------------------------------------------

    def _segment_options(self) -> list[tuple[str, str]]:
        """The three segment labels. "Screen" is the monitor sense, hence `pick`."""
        return [
            (config.SOURCE_MONITOR, pick("source.screen", "Screen")),
            (config.SOURCE_REGION, _("Region")),
            (config.SOURCE_WINDOW, _("Window")),
        ]

    def retranslate(self, code: str = "") -> None:
        """Re-read every string in the picker. Connected to `i18n.notifier.changed`.

        The cards carry most of the words, and they are built from the monitor and
        window lists rather than kept as text, so `refresh` is what rewrites them. It
        also clears the message line - a message in the old language would otherwise
        sit there until the next thing went wrong.
        """
        self.segments.options = self._segment_options()
        self.segments.updateGeometry()
        self.segments.update()
        self.refresh_button.setToolTip(_("Look again for screens and windows"))
        self.screens.set_empty_text(_("No screens were reported by DXGI."))
        self.windows.set_empty_text(_("No windows big enough to record."))
        self.refresh()

    # -- content -----------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the monitors, the window list and the remembered region."""
        self.clear_message()
        self._cards_by_key.clear()
        self._load_monitors()
        self._load_windows()
        remembered = self.settings.region
        self.region.set_region(
            (remembered[0], remembered[1], remembered[2], remembered[3])
            if len(remembered) == 4
            else None
        )
        self.set_mode(self._mode)

    def _load_monitors(self) -> None:
        try:
            monitors = winapi.list_monitors()
        except Exception as exc:  # a COM failure here must not take the picker with it
            monitors = []
            self.show_message(
                _("The screens could not be listed: {reason}").format(reason=exc)
            )
            _report(f"list_monitors() failed: {exc!r}")
        specs: list[dict[str, object]] = []
        if len(monitors) > 1:
            total = sum(m.width * m.height for m in monitors)
            specs.append(
                {
                    "key": "all:screens",
                    "payload": -1,
                    "icon": "monitor",
                    "name": _("All {count} screens").format(
                        count=len(monitors), word=_screens_word(len(monitors))
                    ),
                    "detail": _("one file each · {total} Mpx in total").format(
                        total=f"{total / 1_000_000:.1f}"
                    ),
                    "monitor": None,
                }
            )
        for monitor in monitors:
            detail = [f"{monitor.width} × {monitor.height}"]
            # winapi has no primary flag; in Windows desktop coordinates the primary
            # output is the one whose top-left corner is the origin.
            if monitor.x == 0 and monitor.y == 0:
                detail.append(_("primary"))
            if monitor.rotated:
                detail.append(_("rotated"))
            specs.append(
                {
                    "key": f"monitor:{monitor.index}",
                    "payload": monitor.index,
                    "icon": "monitor_rotated" if monitor.rotated else "monitor",
                    # The player names a recorded picture "Screen 1" too, and the two
                    # are not the same word in every language; `pick` keeps them apart.
                    "name": pick("source.screen_number", "Screen {number}").format(
                        number=monitor.index + 1
                    ),
                    "detail": " · ".join(detail),
                    "monitor": monitor,
                }
            )
        for card in self.screens.set_cards(specs):
            self._cards_by_key[card.key] = card
        if not monitors and self.message.isHidden():
            self.show_message(_("No screens were reported by DXGI."))

    def _load_windows(self) -> None:
        try:
            found = winapi.list_windows()
        except Exception as exc:  # EnumWindows can be interrupted by a dying window
            found = []
            self.show_message(
                _("The window list could not be read: {reason}").format(reason=exc)
            )
            _report(f"list_windows() failed: {exc!r}")
        specs: list[dict[str, object]] = []
        for info in found:
            unnamed = _("Window {handle}").format(handle=info.hwnd)
            specs.append(
                {
                    "key": f"window:{info.hwnd}",
                    "payload": info.hwnd,
                    "icon": "window",
                    "name": info.title or info.process or unnamed,
                    "detail": f"{info.process} · {info.width} × {info.height}",
                    "window_info": info,
                }
            )
        for card in self.windows.set_cards(specs):
            self._cards_by_key[card.key] = card

    def set_mode(self, key: str) -> None:
        """Show one segment. Also marks the card the settings currently point at."""
        if key not in self._pages:
            _report(f"unknown source mode {key!r}; showing the screens")
            key = config.SOURCE_MONITOR
        self._mode = key
        self.segments.set_current(key)
        self._stack.setCurrentWidget(self._pages[key])
        self._mark_selection()
        self._request_thumbnails(key)
        self._fit_height()

    def _on_segment(self, key: str) -> None:
        self.clear_message()
        self.set_mode(key)

    def _pick_screen_card(self, card) -> None:
        """One screen, or all of them - they share the Screen page."""
        if getattr(card, "key", "") == "all:screens":
            self.chosen.emit(config.SOURCE_ALL_SCREENS, None)
            self._mark_selection()
            return
        self._pick_monitor(card)

    def _mark_selection(self) -> None:
        kind = self.settings.source_kind
        for card in self.screens.cards():
            if getattr(card, "key", "") == "all:screens":
                card.set_selected(kind == config.SOURCE_ALL_SCREENS)
                continue
            card.set_selected(
                kind == config.SOURCE_MONITOR
                and card.payload == self.settings.monitor_index
            )
        title = self.settings.window_title
        for card in self.windows.cards():
            info = card.window_info
            card.set_selected(
                kind == config.SOURCE_WINDOW
                and bool(title)
                and info is not None
                and info.title == title
            )

    # -- thumbnails ---------------------------------------------------------

    def _cannot_start(self, exc: BaseException) -> str:
        """Say the loader did not start: once on the surface, once in the log."""
        _report(f"the thumbnail loader could not start: {exc!r}")
        return _("The thumbnail loader could not start: {reason}").format(reason=exc)

    def _ensure_loader(self) -> bool:
        """Build the thumbnail loader on first use. False means: placeholders only.

        The import is deliberately late. `ui.thumbnails` starts an ffmpeg grab worker, and
        a picker that is never opened should not pay for one; it also lets the module be
        missing without taking the whole interface down with it.
        """
        if self._loader is not None:
            return True
        if self._loader_problem:
            return False
        try:
            from . import thumbnails
        except ImportError as exc:
            self._loader_problem = _(
                "Thumbnails are unavailable ({reason}); the cards show icons instead."
            ).format(reason=exc)
            # The stored sentence is shown on the surface, so it is translated; the log
            # line stays English, because a log is read by whoever wrote the code.
            _report(f"thumbnails are unavailable: {exc!r}")
            return False
        maker = getattr(thumbnails, "ThumbnailLoader", None)
        if maker is None:
            self._loader_problem = _(
                "ui.thumbnails has no ThumbnailLoader; the cards show icons instead."
            )
            _report("ui.thumbnails has no ThumbnailLoader")
            return False
        try:
            loader = maker(self)
        except TypeError:
            # Allow a loader that takes no parent, rather than refuse to show pictures.
            try:
                loader = maker()
            except Exception as exc:
                self._loader_problem = self._cannot_start(exc)
                return False
        except Exception as exc:
            self._loader_problem = self._cannot_start(exc)
            return False
        ready = getattr(loader, "ready", None)
        if ready is None:
            self._loader_problem = _("The thumbnail loader has no 'ready' signal.")
            _report("the thumbnail loader has no 'ready' signal")
            return False
        ready.connect(self._on_thumbnail)
        self._loader = loader
        return True

    def _request_thumbnails(self, mode: str) -> None:
        """Ask for the pictures of the page being shown, and no others."""
        if mode == config.SOURCE_MONITOR:
            cards = self.screens.cards()
        elif mode == config.SOURCE_WINDOW:
            cards = self.windows.cards()
        else:
            return
        # The 'All screens' card stands for every screen at once, so there is no
        # single frame to grab for it; asking would make the loader report
        # "neither a monitor nor a window" every time the picker opens.
        wanted = [
            card for card in cards
            if not card.has_thumbnail()
            and (card.monitor is not None or card.window_info is not None)
        ]
        if not wanted:
            return
        if not self._ensure_loader():
            if self._loader_problem:
                self.show_message(self._loader_problem, "text_dim")
            return
        request = getattr(self._loader, "request", None)
        if request is None:
            self._loader_problem = _("The thumbnail loader has no 'request' method.")
            _report("the thumbnail loader has no 'request' method")
            self.show_message(self._loader_problem, "text_dim")
            return
        for card in wanted:
            try:
                request(
                    card.key,
                    monitor=card.monitor,
                    window=card.window_info,
                    width=THUMB_WIDTH,
                )
            except Exception as exc:
                _report(f"thumbnail request for {card.key!r} failed: {exc!r}")

    def _on_thumbnail(self, key: str, thumb: object) -> None:
        card = self._cards_by_key.get(key)
        if card is None:
            # The list was refreshed while the grab was in flight. Nothing to show, and
            # nothing wrong - but say so, because a key that never matches is a bug.
            _report(f"thumbnail {key!r} arrived with no card to put it on")
            return
        if thumb is None:
            # The loader keeps the reason per key; say it rather than just "no picture".
            problems = getattr(self._loader, "problems", None)
            why = ""
            if isinstance(problems, dict):
                why = str(problems.get(key, ""))
            _report(
                f"no thumbnail for {key!r}; the card keeps its placeholder"
                + (f" ({why})" if why else "")
            )
            card.set_thumbnail(None)
            return
        pixmap = getattr(thumb, "pixmap", None)
        if not isinstance(pixmap, QtGui.QPixmap):
            _report(f"thumbnail {key!r} carried {type(thumb).__name__}, not a Thumb")
            card.set_thumbnail(None)
            return
        card.set_thumbnail(pixmap)

    def _stop_loader(self) -> None:
        """Stop the loader and let go of it. Safe to call twice, and when there is none.

        The loader is a child of this surface, so whatever destroys the surface would
        otherwise destroy the loader behind its worker's back: the worker then raises
        ``RuntimeError: Signal source has been deleted`` on the next ``ready`` and the
        thread dies with it, a grab in flight is never killed, and the ffmpeg child and
        its temp PNG are left behind. `thumbnails.ThumbnailLoader` says as much - stop()
        before the loader is destroyed.

        The loader is dropped rather than kept, because `stop()` is permanent: a stopped
        loader answers every later request with "the thumbnail loader has been stopped".
        `_ensure_loader` therefore builds a fresh one if the picker is used again.
        """
        loader = self._loader
        if loader is None:
            return
        self._loader = None
        stop = getattr(loader, "stop", None)
        if stop is None:
            _report("the thumbnail loader has no stop(); its worker may outlive the app")
        else:
            try:
                stop()
            except Exception as exc:
                _report(f"stopping the thumbnail loader failed: {exc!r}")
        delete_later = getattr(loader, "deleteLater", None)
        if delete_later is not None:
            try:
                delete_later()
            except RuntimeError as exc:
                # Already gone on the C++ side - nothing left to delete, and the stop()
                # above was the part that mattered.
                _report(f"the thumbnail loader was already deleted: {exc}")

    def shutdown(self) -> None:
        """Release what the picker owns outside Qt's parent/child graph.

        The controller may call this before dropping the picker; it does not have to,
        because `closeEvent` and the deferred-delete hook below call it too. Hiding the
        picker deliberately does *not* - a popover that is put away is reopened in a
        second, and throwing the thumbnail cache away each time would cost a grab per
        card per open.
        """
        self._stop_loader()

    def event(self, event: QtCore.QEvent) -> bool:
        """Catch the deferred delete, which is the last moment Python still owns us.

        `destroyed` is too late: by the time it fires the C++ object, and with it the
        child loader, is already gone. `QEvent.Type.DeferredDelete` is delivered to this
        object *before* `QObject` deletes it, so `deleteLater()` on the picker - what
        the controller does when it rebuilds one - reaches `stop()` after all.
        """
        if event.type() == QtCore.QEvent.Type.DeferredDelete:
            self._stop_loader()
        return super().event(event)

    # -- choosing -----------------------------------------------------------

    def _pick_monitor(self, card: SourceCard) -> None:
        index = card.payload
        try:
            monitors = winapi.list_monitors()
        except Exception as exc:
            # The same COM failure `_load_monitors` guards against. The decision here is
            # to *proceed*: a listing that failed is no evidence the screen is gone, and
            # refusing would leave the user unable to pick anything until it recovered.
            # It matches the empty-list branch below, which also proceeds. No message is
            # shown, because the pick closes the surface in the next few lines and the
            # line would never be read; the recorder reports it if the screen really has
            # gone.
            monitors = []
            _report(f"list_monitors() failed while picking a screen: {exc!r}")
        if monitors and not any(m.index == index for m in monitors):
            # Refresh first: it clears the message line, so the explanation has to be
            # written after the rebuild, not before it.
            self.refresh()
            self.set_mode(config.SOURCE_MONITOR)
            self.show_message(
                _(
                    "Screen {number} is no longer connected. The list has been "
                    "refreshed."
                ).format(number=int(index) + 1)
            )
            return
        self.settings.source_kind = config.SOURCE_MONITOR
        self.settings.monitor_index = int(index)
        self._mark_selection()
        self._close(emit_cancelled=False)
        self.chosen.emit(config.SOURCE_MONITOR, int(index))

    def _pick_window(self, card: SourceCard) -> None:
        hwnd = int(card.payload)
        alive = True
        try:
            alive = winapi.window_is_alive(hwnd)
        except Exception as exc:
            alive = False
            _report(f"window_is_alive({hwnd}) failed: {exc!r}")
        if not alive:
            name = card.window_info.title if card.window_info else str(hwnd)
            # Refresh first: it clears the message line, so the explanation has to be
            # written after the rebuild, not before it.
            self.refresh()
            self.set_mode(config.SOURCE_WINDOW)
            self.show_message(
                _(
                    "“{name}” has closed since the list was made. The list has been "
                    "refreshed — pick another window."
                ).format(name=name)
            )
            return
        self.settings.source_kind = config.SOURCE_WINDOW
        if card.window_info is not None:
            self.settings.window_title = card.window_info.title
        self._mark_selection()
        self._close(emit_cancelled=False)
        self.chosen.emit(config.SOURCE_WINDOW, hwnd)

    def _pick_new_region(self) -> None:
        """Run the existing region selector, hiding this surface while it is up."""
        try:
            from .. import overlay
        except ImportError as exc:
            self.show_message(
                _("The region selector is unavailable ({reason}).").format(reason=exc)
            )
            _report(f"overlay import failed: {exc!r}")
            return
        was_visible = self.isVisible()
        self._close(emit_cancelled=False)
        try:
            rect = overlay.RegionSelector.choose(None)
        except Exception as exc:
            _report(f"RegionSelector.choose() failed: {exc!r}")
            if was_visible:
                # The surface was hidden a moment ago to get it out of the selector's
                # way, so the explanation would land on a hidden label and the next
                # `popup_near` would clear it before anyone saw it. Bring the picker
                # back and say so there - and do *not* emit `cancelled`, because a
                # failure is not the user pressing Esc and the two must not look alike.
                self.show()
                self.raise_()
                self.show_message(
                    _("The region could not be selected: {reason}").format(reason=exc)
                )
                return
            # Nothing on screen to write on. Tell the caller instead, so the picker's
            # state does not simply hang.
            self.show_message(
                _("The region could not be selected: {reason}").format(reason=exc)
            )
            self.cancelled.emit()
            return
        if rect is None:
            self.cancelled.emit()
            return
        self.settings.source_kind = config.SOURCE_REGION
        self.settings.region = list(rect)
        self.region.set_region(tuple(rect))
        self.chosen.emit(config.SOURCE_REGION, tuple(rect))

    def _reuse_region(self) -> None:
        if len(self.settings.region) != 4:
            self.show_message(_("There is no remembered area to use."))
            return
        rect = tuple(int(v) for v in self.settings.region)
        self.settings.source_kind = config.SOURCE_REGION
        self._close(emit_cancelled=False)
        self.chosen.emit(config.SOURCE_REGION, rect)

    # -- messages -----------------------------------------------------------

    def show_message(self, text: str, role: str = "error_text") -> None:
        self.message.setText(text)
        self.message.setStyleSheet(
            f"color: {theme.COLOURS.get(role, theme.COLOURS['error_text'])};"
            " background: transparent;"
        )
        self.message.setVisible(bool(text))
        self._fit_height()

    def clear_message(self) -> None:
        self.message.setText("")
        self.message.setVisible(False)

    # -- geometry -----------------------------------------------------------

    def _chrome_height(self) -> int:
        """Everything in the surface that is not the page."""
        height = 2 * TOP_MARGIN + HEADER_H + BODY_GAP
        # `isHidden`, not `isVisible`: the message is part of the layout whether or not
        # the surface itself is on screen, and a height computed while hidden must not
        # change the moment the surface is shown.
        if not self.message.isHidden():
            height += BODY_GAP + max(
                16, self.message.heightForWidth(WIDTH - 2 * SIDE_MARGIN)
            )
        return height

    def _fit_height(self) -> None:
        page = self._pages[self._mode]
        natural = page.natural_height()  # type: ignore[attr-defined]
        chrome = self._chrome_height()
        available = max(80, MAX_HEIGHT - chrome)
        page_height = max(60, min(natural, available))
        self._stack.setFixedHeight(page_height)
        self.setFixedHeight(min(MAX_HEIGHT, chrome + page_height))

    def popup_near(self, widget: QtWidgets.QWidget) -> None:
        """Place the surface under `widget`, or over it, and always on a real screen.

        Geometry comes from `winapi.list_monitors()`, which is in desktop pixels - the
        same space `QWidget.mapToGlobal` reports once DPI awareness is on, and the same
        space the recorder crops in.
        """
        self._anchor = widget
        self.refresh()
        width, height = self.width(), self.height()

        top_left = widget.mapToGlobal(QtCore.QPoint(0, 0))
        anchor_x, anchor_y = top_left.x(), top_left.y()
        anchor_w, anchor_h = widget.width(), widget.height()
        gap = 8

        monitors: list[winapi.Monitor] = []
        try:
            monitors = winapi.list_monitors()
        except Exception as exc:
            _report(f"list_monitors() failed while placing the picker: {exc!r}")
        monitor = winapi.monitor_at(
            monitors, anchor_x + anchor_w // 2, anchor_y + anchor_h // 2
        )
        if monitor is None and monitors:
            monitor = monitors[0]
        if monitor is not None:
            left, top = monitor.x, monitor.y
            right, bottom = monitor.x + monitor.width, monitor.y + monitor.height
        else:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                _report("no monitors and no QScreen; placing the picker at the anchor")
                left, top = anchor_x, anchor_y
                right, bottom = anchor_x + width, anchor_y + height
            else:
                area = screen.availableGeometry()
                left, top = area.left(), area.top()
                right, bottom = area.left() + area.width(), area.top() + area.height()

        y = anchor_y + anchor_h + gap
        if y + height > bottom - EDGE:
            above = anchor_y - gap - height
            if above >= top + EDGE:
                y = above
            else:
                # Neither side fits: sit against the bottom edge rather than off-screen.
                y = max(top + EDGE, bottom - EDGE - height)

        x = anchor_x + anchor_w // 2 - width // 2
        x = max(left + EDGE, min(x, right - EDGE - width))
        if right - left < width + 2 * EDGE:
            x = left + max(0, (right - left - width) // 2)

        self.move(int(x), int(y))
        self.show()
        self.raise_()

    # -- dragging -----------------------------------------------------------
    #
    # The picker is placed by `popup_near` every time it is opened, so a drag only has
    # to last while it is up: the position is deliberately **not** remembered. A
    # popover belongs to the chip that opened it, and one that came back where it was
    # dropped last time - possibly nowhere near the bar, possibly on another screen -
    # would stop reading as that chip's popover. The settings panel is the other way
    # round and does remember; it is a panel, not a popover.

    def _in_header(self, point: QtCore.QPoint) -> bool:
        """Whether `point` (in surface coordinates) is on the top strip.

        The band reaches the top of the surface, so the margin above the segmented
        control is draggable too - that is where the pointer actually lands when
        someone grabs "the top of the window".
        """
        return point.y() <= self.header.geometry().bottom()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        point = event.position().toPoint()
        # A control in the header stays a control. Most of them accept the press and it
        # never gets here; `Segmented` does not, which is what `control_at` is for.
        if not self._in_header(point) or surfaces.control_at(self, point) is not None:
            event.ignore()
            return
        self._drag.begin(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag.active():
            event.ignore()
            return
        self._drag.to(event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        # A press that travelled less than `bar.DRAG_SLOP` is a click, and a click on
        # the bare header is not a command - every control got the event first.
        self._drag.end()
        event.accept()

    # -- dismissal ----------------------------------------------------------

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        app = QtWidgets.QApplication.instance()
        if app is not None and not self._filtering:
            app.installEventFilter(self)
            self._filtering = True

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._remove_filter()
        # A surface hidden mid-drag never sees the release, and a half-finished drag
        # left behind would move the picker on the first mouse move after it reopened.
        self._drag.cancel()
        super().hideEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._remove_filter()
        # A close is a teardown, not a hide: nothing in this module calls close(), so
        # the only caller is the application putting the picker away for good.
        self._stop_loader()
        super().closeEvent(event)

    def _remove_filter(self) -> None:
        if not self._filtering:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._filtering = False

    def _close(self, emit_cancelled: bool) -> None:
        self._remove_filter()
        self.hide()
        if emit_cancelled:
            self.cancelled.emit()

    def cancel(self) -> None:
        """Dismiss without choosing. Safe to call when already hidden."""
        self._close(emit_cancelled=True)

    def _owns(self, obj: QtCore.QObject) -> bool:
        """True for this surface, and for the widget that opened it.

        The anchor counts, or a second click on the chip would close the picker here and
        the bar would immediately reopen it - one click, two state changes, a flicker.
        Only the anchor itself and its children, though, and not the whole bar: pressing
        Record while the picker is open should put the picker away.

        **A real press is seen twice.** The platform delivers it to the ``QWindow``
        first, and Qt only then turns it into a press on the child widget, so an
        application-wide filter runs once with the surface's ``QWidgetWindow`` and once
        with the widget inside it. Measured without the `QWindow` arm below: every
        genuine click *inside* the picker dismissed it on that first pass - switching
        segment became a dismissal, and clicking a card emitted `cancelled` and then
        `chosen`. `QTest.mouseClick(widget, …)` posts straight to the widget and never
        goes through the window, which is why the widget-level checks could not see it.
        """
        if isinstance(obj, QtGui.QWindow):
            handle = self.windowHandle()
            if handle is not None and obj is handle:
                return True
            anchor = self._anchor
            if anchor is None:
                return False
            try:
                anchor_window = anchor.window()
            except RuntimeError:
                # The anchor was deleted out from under us; it cannot own anything now.
                self._anchor = None
                return False
            anchor_handle = (
                anchor_window.windowHandle() if anchor_window is not None else None
            )
            # The anchor's whole window, because this pass cannot say which widget in it
            # was pressed. The widget pass that follows makes that distinction, so
            # pressing Record in the bar still dismisses the picker.
            return anchor_handle is not None and obj is anchor_handle
        if not isinstance(obj, QtWidgets.QWidget):
            return False
        if obj is self or self.isAncestorOf(obj) or obj.window() is self:
            return True
        anchor = self._anchor
        if anchor is None:
            return False
        try:
            return obj is anchor or anchor.isAncestorOf(obj)
        except RuntimeError:
            # The anchor was deleted out from under us; it cannot own anything now.
            self._anchor = None
            return False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        kind = event.type()
        if kind == QtCore.QEvent.Type.KeyPress and self.isVisible():
            if isinstance(event, QtGui.QKeyEvent) and event.key() in (
                QtCore.Qt.Key.Key_Escape,
            ):
                self.cancel()
                return True
        elif kind in (
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QEvent.Type.NonClientAreaMouseButtonPress,
        ):
            if self.isVisible() and not self._owns(watched):
                # Dismiss, but let the click through: the user was pressing something.
                self.cancel()
        return super().eventFilter(watched, event)

    def _on_app_state(self, state: QtCore.Qt.ApplicationState) -> None:
        if state == QtCore.Qt.ApplicationState.ApplicationInactive and self.isVisible():
            self.cancel()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """Only reached if something did give this surface the keyboard; still honour it."""
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        super().keyPressEvent(event)
