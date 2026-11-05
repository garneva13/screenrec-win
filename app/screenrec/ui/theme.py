"""The dark look of the application: one palette, one style sheet, three small helpers.

This module stands alone. It imports nothing from the rest of ScreenRec and nothing from
any other toolset, so it can be dropped into a scratch script to look at a widget.

Why a style sheet at all, and why this much of one: on Windows 11 the native style paints
combo boxes, spin boxes, check boxes and scroll bars with its own light chrome. Styling
only the obvious widgets leaves those untouched and the window looks half-finished, so
every widget the application uses gets a rule here, including the sub-controls.

Three Qt style-sheet traps are worked around below, deliberately rather than by accident:

* **A universal background paints the containers too.** ``QStyleSheetStyle`` sets
  ``WA_StyledBackground`` on *direct* ``QWidget`` instances (measured: ``True`` for a plain
  ``QWidget``, ``False`` for a ``QWidget`` subclass), so a blanket
  ``QWidget { background-color: ...; }`` is not merely a default that children override -
  every bare container fills itself with it. A card's body, a form row holder and the
  viewport of a ``QPlainTextEdit`` are all plain ``QWidget``s, so the window colour was
  being painted straight over the card's panel colour and over the field colour of a text
  edit. The base rule below therefore sets no background at all; the window surface comes
  from ``QMainWindow``/``QDialog`` and, for a plain top-level widget, from the palette's
  ``Window`` role (measured: a top-level ``QWidget`` still reads ``bg``). Every surface
  that *should* carry a colour names itself.

  The tempting one-line repair - ``QGroupBox > QWidget { background: transparent; }`` -
  is wrong and was measured to be wrong: two type selectors outrank the single-type
  ``QComboBox`` and ``QLineEdit`` rules, so a combo box or line edit placed directly in a
  group box loses its ``field`` background and reads ``panel``. Container rules here stay
  at id specificity for that reason.

* **Spin box arrows.** ``QSpinBox::up-button`` is positioned inside the *padding* box by
  default. Reserving room for the arrows with ``padding-right`` therefore moves the arrows
  left by that same amount - the gap is subtracted twice and the buttons end up floating
  over the text, or outside the rounded border once a border radius is in play. The fix
  is to anchor the buttons to the border box (``subcontrol-origin: border``) with explicit
  ``width``/``height``, and to keep the text clear with ``padding-right`` that is then
  used for its real purpose only.
* **Selector order.** A sub-control comes before a pseudo-state:
  ``QSlider::handle:horizontal:disabled`` matches, ``QSlider:disabled::handle`` matches
  nothing at all and fails silently. Every rule below is written in that order.

Measured contrast (sRGB relative luminance per WCAG 2.1, computed by the formula, not by
eye; the same numbers are recomputed and asserted in ``tests/test_theme.py``):

    text        on bg      17.50 : 1
    text        on panel   16.74 : 1
    text_dim    on panel    5.86 : 1
    accent_text on accent    4.71 : 1

All four clear 4.5 : 1, including ``text_dim``, so no 3.0 exception is being taken.
For completeness: ``warn`` on panel is 7.49, ``ok`` on panel 6.36, ``error_text`` on panel
5.91, ``accent_light`` on panel 6.71. ``disabled`` on panel is 3.42, which is intentional
and permitted - WCAG exempts disabled controls, and the whole point of the colour is to
read as unavailable.

``accent`` (2.85 on panel) and ``record`` (3.10 on panel) are **fill** colours. They are
never used for body text; text drawn on top of them uses ``accent_text``. Where a message
has to be coloured like the record state, ``error_text`` is the readable variant.
"""
from __future__ import annotations

import os
from pathlib import Path
from string import Template

from PySide6 import QtCore, QtGui, QtWidgets

__all__ = [
    "COLOURS",
    "Card",
    "apply",
    "caption",
    "palette",
    "row",
    "style_sheet",
    "template_keys",
]

#: Every colour in the application, as ``#rrggbb``. Nothing below hard-codes a colour.
COLOURS: dict[str, str] = {
    # Surfaces, darkest to lightest. The whole app is three planes and a hairline:
    # a sunken plane for anything inset, the bar's own plane, and one raised plane
    # that every floating thing shares, so popovers, menus and panels read as one.
    "bg": "#0F1216",  # sunken: a card's thumbnail well, a field's inside
    "field": "#0F1216",
    "panel": "#14171C",  # the bar, and any window's own ground
    "panel_raised": "#1A1E25",  # everything that floats: popovers, panels, menus
    "hover": "#1F242C",
    "line": "#242932",  # the hairline that carries elevation instead of a shadow
    "line_strong": "#333A45",  # the same hairline, under the pointer
    # Text, four weights. The faintest is metadata and is deliberately below 4.5:1.
    "text": "#F5F7FA",
    "text_soft": "#C3CAD4",
    "text_dim": "#8A94A3",
    "text_faint": "#6B7584",
    "disabled": "#596271",
    # Violet means "this is the one you picked" - selection, armed toggles, the
    # chosen tile. Their own mock-up chose it and it is a better split than the
    # reference's green, because it leaves green free to mean "live right now":
    # the ready dot, the microphone meter. Red stays the record state and nothing
    # else.
    "accent": "#8B5CF6",
    "accent_light": "#A78BFA",  # accent as *text* on a dark plane
    "accent_hover": "#9D7BF7",
    "accent_press": "#7C3AED",
    "accent_text": "#0B0618",  # ink on an accent fill; white on violet is 4.23
    "live": "#34D399",  # a device that is actually feeding us, and the level meter
    # Red means "this is live", and nothing else ever uses it.
    "record": "#E85349",
    "record_hover": "#F0615A",
    "record_press": "#CC4239",
    "record_dim": "#7E2822",
    "record_text": "#FFFFFF",  # the glyph inside the record button, a shape not text
    "error_text": "#FF8A80",
    # Status
    "warn": "#EFA23A",
    "ok": "#34D399",
}

#: Corner radii, in the one place they can be kept consistent. Taken from the
#: reference the user asked this to look like: the bar is the roundest thing on
#: screen, floating surfaces one step less, cards and controls less again.
RADIUS = {
    "bar": 17,
    "surface": 15,
    "card": 14,
    "control": 11,
    "chip": 10,
    "small": 8,
}

# Geometry shared by the spin box and combo box sub-controls. Changing BUTTON_W here is
# the only place the reserved gap and the button width can drift apart.
_BUTTON_W = 20
_TEXT_GAP = _BUTTON_W + 8

_STYLE_SHEET = Template(
    """
/* ------------------------------------------------------------------ base */

/* No background-color here, deliberately - see the first trap in the module docstring.
   A background on this rule is painted by every bare container widget, not just the
   window, and it lands on top of the card and field colours set further down. */
QWidget {
    color: $text;
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 9pt;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QWidget:disabled {
    color: $disabled;
}
QMainWindow, QDialog {
    background-color: $bg;
}

/* Plain containers - a card's body, a form row, any box of controls assembled by the
   caller - carry no colour of their own and must show the surface they sit on. Stated
   explicitly rather than left to inheritance, so that re-adding a universal background
   would not quietly fill them again. Id specificity only: a descendant selector such as
   `QFrame#card QWidget` or a child selector such as `QGroupBox > QWidget` would outrank
   the QLineEdit / QComboBox / QListView type rules and strip $field off the controls. */
QWidget#cardBody, QWidget#formRow {
    background: transparent;
}

/* ---------------------------------------------------------------- labels */

QLabel {
    background: transparent;
    color: $text;
}
QLabel:disabled {
    color: $disabled;
}
QLabel#heading {
    font-size: 12pt;
    font-weight: 600;
}
QLabel#cardTitle {
    font-size: 9pt;
    font-weight: 600;
    color: $text;
    padding: 0;
}
QLabel#caption {
    color: $text_dim;
}
QLabel#rowLabel {
    color: $text_dim;
}
QLabel#value {
    font-family: "Consolas", "Cascadia Mono", monospace;
    color: $text;
}
QLabel#ok      { color: $ok; }
QLabel#warn    { color: $warn; }
QLabel#error   { color: $error_text; }
QLabel#link    { color: $accent_light; }

/* --------------------------------------------------------------- buttons */

QPushButton {
    background-color: $panel_raised;
    color: $text;
    border: 1px solid $line;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}
QPushButton:hover {
    background-color: $hover;
    border-color: $line_strong;
}
QPushButton:pressed {
    background-color: $panel;
}
QPushButton:focus {
    border-color: $accent;
}
QPushButton:disabled {
    background-color: $panel;
    color: $disabled;
    border-color: $line;
}
QPushButton:checked {
    background-color: $accent_press;
    border-color: $accent;
    color: $accent_text;
}

QPushButton#primary {
    background-color: $accent;
    color: $accent_text;
    border: 1px solid $accent;
    font-weight: 600;
}
QPushButton#primary:hover   { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:pressed { background-color: $accent_press; border-color: $accent_press; }
QPushButton#primary:disabled {
    background-color: $panel;
    color: $disabled;
    border-color: $line;
}

QPushButton#record {
    background-color: $record;
    color: $accent_text;
    border: 1px solid $record;
    font-weight: 600;
}
QPushButton#record:hover    { background-color: $record_hover; border-color: $record_hover; }
QPushButton#record:pressed  { background-color: $record_press; border-color: $record_press; }
QPushButton#record:disabled {
    background-color: $record_dim;
    color: $disabled;
    border-color: $record_dim;
}

QPushButton#stop {
    background-color: $panel_raised;
    color: $error_text;
    border: 1px solid $record;
    font-weight: 600;
}
QPushButton#stop:hover   { background-color: $record_dim; color: $accent_text; }
QPushButton#stop:pressed { background-color: $record_press; color: $accent_text; }
QPushButton#stop:disabled {
    background-color: $panel;
    color: $disabled;
    border-color: $line;
}

QPushButton#ghost {
    background: transparent;
    color: $text_dim;
    border: 1px solid transparent;
    padding: 5px 10px;
}
QPushButton#ghost:hover   { background-color: $hover; color: $text; border-color: $line; }
QPushButton#ghost:pressed { background-color: $panel; }
QPushButton#ghost:disabled { color: $disabled; background: transparent; }

/* ------------------------------------------------------------ tool button */

QToolButton {
    background: transparent;
    color: $text;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 6px;
}
QToolButton:hover {
    background-color: $hover;
    border-color: $line;
}
QToolButton:pressed, QToolButton:checked {
    background-color: $accent_press;
    border-color: $accent;
    color: $accent_text;
}
QToolButton:disabled {
    color: $disabled;
    background: transparent;
}
QToolButton::menu-indicator {
    image: none;
    width: 0;
}

/* --------------------------------------------------- line edit / spin box */

QLineEdit {
    background-color: $field;
    color: $text;
    border: 1px solid $line;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QLineEdit:hover    { border-color: $line_strong; }
QLineEdit:focus    { border-color: $accent; }
QLineEdit:disabled { background-color: $panel; color: $disabled; }
QLineEdit:read-only { background-color: $panel; color: $text_dim; }

QSpinBox, QDoubleSpinBox {
    background-color: $field;
    color: $text;
    border: 1px solid $line;
    border-radius: 6px;
    /* Left padding is the text inset. Right padding is the real gap that keeps the text
       away from the arrows; the arrows themselves are anchored to the border box below,
       so this gap is applied once and not subtracted from their position as well. */
    padding: 5px ${text_gap}px 5px 8px;
    min-height: 18px;
}
QSpinBox:hover, QDoubleSpinBox:hover       { border-color: $line_strong; }
QSpinBox:focus, QDoubleSpinBox:focus       { border-color: $accent; }
QSpinBox:disabled, QDoubleSpinBox:disabled { background-color: $panel; color: $disabled; }

QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: ${button_w}px;
    height: 13px;
    margin: 1px 1px 0 0;
    background-color: $panel_raised;
    border: none;
    border-top-right-radius: 5px;
    border-left: 1px solid $line;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: ${button_w}px;
    height: 13px;
    margin: 0 1px 1px 0;
    background-color: $panel_raised;
    border: none;
    border-bottom-right-radius: 5px;
    border-left: 1px solid $line;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: $hover;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
    background-color: $accent_press;
}
QSpinBox::up-button:disabled, QDoubleSpinBox::up-button:disabled,
QSpinBox::down-button:disabled, QDoubleSpinBox::down-button:disabled {
    background-color: $panel;
}

/* Painted PNGs, not CSS triangles. Qt's style sheet gives a sub-control its box but does
   not draw transparent-border triangles inside it, so the border trick leaves the button
   empty - which is exactly what these looked like before. See `glyph_url`. */
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: $glyph_up;
    width: 11px;
    height: 11px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: $glyph_down;
    width: 11px;
    height: 11px;
}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off,
QDoubleSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:off {
    image: $glyph_up_off;
}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off,
QDoubleSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:off {
    image: $glyph_down_off;
}

/* ------------------------------------------------------------- combo box */

QComboBox {
    background-color: $field;
    color: $text;
    border: 1px solid $line;
    border-radius: 6px;
    padding: 5px ${text_gap}px 5px 8px;
    min-height: 18px;
}
QComboBox:hover     { border-color: $line_strong; }
QComboBox:focus, QComboBox:on { border-color: $accent; }
QComboBox:disabled  { background-color: $panel; color: $disabled; }
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: ${button_w}px;
    margin: 1px 1px 1px 0;
    background-color: $panel_raised;
    border: none;
    border-left: 1px solid $line;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}
QComboBox::drop-down:hover    { background-color: $hover; }
QComboBox::drop-down:disabled { background-color: $panel; }
QComboBox::down-arrow {
    image: $glyph_combo;
    width: 11px;
    height: 11px;
}
QComboBox::down-arrow:disabled {
    image: $glyph_combo_off;
}
/* The popup is a separate top-level window and inherits none of the above. */
QComboBox QAbstractItemView {
    background-color: $panel;
    color: $text;
    border: 1px solid $line;
    border-radius: 6px;
    padding: 4px;
    outline: none;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QComboBox QAbstractItemView::item {
    min-height: 22px;
    padding: 2px 6px;
    border-radius: 4px;
}
QComboBox QAbstractItemView::item:hover {
    background-color: $hover;
}
QComboBox QAbstractItemView::item:selected {
    background-color: $accent;
    color: $accent_text;
}

/* ------------------------------------------------------------- check box */

QCheckBox, QRadioButton {
    background: transparent;
    color: $text;
    spacing: 8px;
    padding: 2px 0;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: $disabled;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px;
    height: 15px;
    background-color: $field;
    border: 1px solid $line_strong;
}
QCheckBox::indicator  { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: $accent;
}
/* The tick is an image for the same reason as the arrows: a filled accent square with no
   mark in it reads as a half-set state rather than a set one. */
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: $accent;
    border-color: $accent;
    image: $glyph_tick;
}
QCheckBox::indicator:indeterminate {
    background-color: $accent_press;
    border-color: $accent;
    image: $glyph_dash;
}
QCheckBox::indicator:checked:hover {
    background-color: $accent_light;
    border-color: $accent_light;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background-color: $panel;
    border-color: $line;
}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {
    background-color: $panel;
    border-color: $line;
    image: $glyph_tick_off;
}

/* -------------------------------------------------------- frames / groups */

QFrame#card {
    background-color: $panel;
    border: 1px solid $line;
    border-radius: 8px;
}
QFrame#cardHeader {
    background: transparent;
    border: none;
}
QFrame[frameShape="4"] {   /* HLine */
    background: transparent;
    border: none;
    border-top: 1px solid $line;
    max-height: 1px;
}
QFrame[frameShape="5"] {   /* VLine */
    background: transparent;
    border: none;
    border-left: 1px solid $line;
    max-width: 1px;
}

QGroupBox {
    background-color: $panel;
    border: 1px solid $line;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background-color: $bg;
    color: $text_dim;
}
QGroupBox::title:disabled {
    color: $disabled;
}

/* ----------------------------------------------------------- progress bar */

QProgressBar {
    background-color: $field;
    border: 1px solid $line;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: $text;
}
QProgressBar::chunk {
    background-color: $accent;
    border-radius: 5px;
}
QProgressBar#record::chunk {
    background-color: $record;
}
QProgressBar:disabled {
    color: $disabled;
}

/* ---------------------------------------------------------------- slider */

QSlider::groove:horizontal {
    background-color: $field;
    border: 1px solid $line;
    border-radius: 3px;
    height: 4px;
}
QSlider::sub-page:horizontal {
    background-color: $accent;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: $text;
    border: none;
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}
QSlider::handle:horizontal:hover {
    background-color: $accent_light;
}
/* Sub-control first, state second - the reverse matches nothing. */
QSlider::handle:horizontal:disabled {
    background-color: $disabled;
}
QSlider::sub-page:horizontal:disabled {
    background-color: $line_strong;
}

/* --------------------------------------------------- scroll area and bars */

QScrollArea {
    background-color: transparent;
    border: 1px solid $line;
    border-radius: 8px;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}
QAbstractScrollArea {
    background-color: transparent;
}
QAbstractScrollArea::corner {
    background: transparent;
    border: none;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: $line_strong;
    border-radius: 3px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background-color: $text_dim;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
    border: none;
}
QScrollBar::handle:horizontal {
    background-color: $line_strong;
    border-radius: 3px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover {
    background-color: $text_dim;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    height: 0;
    background: none;
    border: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

/* ------------------------------------------------------------ list / text */

QListView, QListWidget, QTreeView, QTableView, QPlainTextEdit, QTextEdit {
    background-color: $field;
    color: $text;
    border: 1px solid $line;
    border-radius: 6px;
    outline: none;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QListView::item, QListWidget::item {
    padding: 3px 6px;
    border-radius: 4px;
}
QListView::item:hover, QListWidget::item:hover {
    background-color: $hover;
}
QListView::item:selected, QListWidget::item:selected {
    background-color: $accent;
    color: $accent_text;
}
QHeaderView::section {
    background-color: $panel_raised;
    color: $text_dim;
    border: none;
    border-right: 1px solid $line;
    border-bottom: 1px solid $line;
    padding: 4px 6px;
}

/* ------------------------------------------------------------------ tabs */

QTabWidget::pane {
    background-color: $panel;
    border: 1px solid $line;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background-color: transparent;
    color: $text_dim;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 14px;
    margin-right: 2px;
}
QTabBar::tab:hover {
    background-color: $hover;
    color: $text;
}
QTabBar::tab:selected {
    background-color: $panel;
    color: $text;
    border-color: $line;
    border-bottom-color: $panel;
}
QTabBar::tab:disabled {
    color: $disabled;
}

/* ------------------------------------------------------------------ menu */

QMenuBar {
    background-color: $bg;
    color: $text;
    border-bottom: 1px solid $line;
}
QMenuBar::item {
    background: transparent;
    padding: 5px 10px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: $hover;
}
QMenu {
    background-color: $panel;
    color: $text;
    border: 1px solid $line;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    background: transparent;
    padding: 6px 22px 6px 14px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: $accent;
    color: $accent_text;
}
QMenu::item:disabled {
    color: $disabled;
}
QMenu::separator {
    height: 1px;
    background-color: $line;
    margin: 5px 8px;
}
QMenu::indicator {
    width: 13px;
    height: 13px;
    margin-left: 6px;
}

/* --------------------------------------------------------------- tooltip */

QToolTip {
    background-color: $panel_raised;
    color: $text;
    border: 1px solid $line_strong;
    border-radius: 6px;
    padding: 4px 7px;
}

/* -------------------------------------------------------- status / splitter */

QStatusBar {
    background-color: $bg;
    color: $text_dim;
    border-top: 1px solid $line;
}
QStatusBar::item {
    border: none;
}
QSplitter::handle {
    background-color: $line;
}
QSplitter::handle:hover {
    background-color: $accent;
}
"""
)


# ------------------------------------------------------------------- glyphs
#
# Qt's style-sheet engine is not a browser. The border trick that draws a triangle out of
# transparent borders in CSS draws nothing here: the sub-control gets its box and stays
# empty, which is what a spin box's buttons and a check box's tick looked like - plain
# grey blocks and a filled square that reads as a half-set state.
#
# So the glyphs are painted into small PNGs once and referenced by url(). The file name
# carries the colour, so changing the palette produces new files instead of showing the
# old colour. Two rules this has to respect, both of which cost an interpreter if broken:
#
# * A QPixmap cannot be constructed before there is a QApplication - that is a qFatal,
#   and a try/except around it catches nothing. Ask for the instance instead.
# * An exception leaving a live QPainter takes the process down, so every paint is in a
#   try/finally that ends the painter.

_GLYPH_CACHE: dict[tuple[str, str], str] = {}


def _glyph_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "ScreenRec" / "glyphs"


def _paint_glyph(kind: str, colour: str, size: int) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        pen_colour = QtGui.QColor(colour)
        if kind in ("up", "down"):
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(pen_colour)
            mid = size / 2
            inset = size * 0.22
            if kind == "up":
                points = [
                    QtCore.QPointF(inset, size - inset),
                    QtCore.QPointF(size - inset, size - inset),
                    QtCore.QPointF(mid, inset),
                ]
            else:
                points = [
                    QtCore.QPointF(inset, inset),
                    QtCore.QPointF(size - inset, inset),
                    QtCore.QPointF(mid, size - inset),
                ]
            painter.drawPolygon(QtGui.QPolygonF(points))
        elif kind == "tick":
            pen = QtGui.QPen(pen_colour, max(1.6, size * 0.16))
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(
                QtGui.QPolygonF(
                    [
                        QtCore.QPointF(size * 0.22, size * 0.52),
                        QtCore.QPointF(size * 0.42, size * 0.72),
                        QtCore.QPointF(size * 0.78, size * 0.28),
                    ]
                )
            )
        elif kind == "dash":  # the indeterminate check box
            pen = QtGui.QPen(pen_colour, max(1.6, size * 0.16))
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                QtCore.QPointF(size * 0.25, size * 0.5),
                QtCore.QPointF(size * 0.75, size * 0.5),
            )
        else:
            raise ValueError(f"unknown glyph {kind!r}")
    finally:
        painter.end()
    return pixmap


def glyph_url(kind: str, colour: str, size: int = 11) -> str:
    """A ``url(...)`` for the style sheet, or ``none`` when there is no QApplication.

    Returning ``none`` rather than failing keeps `style_sheet()` usable from a test or a
    tool that never starts an application; the window simply has no glyphs there, which
    nobody can see anyway.
    """
    key = (kind, f"{colour}-{size}")
    cached = _GLYPH_CACHE.get(key)
    if cached is not None:
        return cached
    if QtWidgets.QApplication.instance() is None:
        return "none"
    target = _glyph_dir() / f"{kind}-{colour.lstrip('#')}-{size}.png"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            pixmap = _paint_glyph(kind, colour, size)
            if not pixmap.save(str(target), "PNG"):
                return "none"
    except (OSError, ValueError):
        return "none"
    url = 'url("{}")'.format(str(target).replace("\\", "/"))
    _GLYPH_CACHE[key] = url
    return url


#: Template names that resolve to a ``url(...)`` instead of a colour.
GLYPH_KEYS: frozenset[str] = frozenset(
    {
        "glyph_up",
        "glyph_up_off",
        "glyph_down",
        "glyph_down_off",
        "glyph_combo",
        "glyph_combo_off",
        "glyph_tick",
        "glyph_tick_off",
        "glyph_dash",
    }
)


def _glyph_values() -> dict[str, str]:
    return {
        "glyph_up": glyph_url("up", COLOURS["text"]),
        "glyph_up_off": glyph_url("up", COLOURS["disabled"]),
        "glyph_down": glyph_url("down", COLOURS["text"]),
        "glyph_down_off": glyph_url("down", COLOURS["disabled"]),
        "glyph_combo": glyph_url("down", COLOURS["text_dim"]),
        "glyph_combo_off": glyph_url("down", COLOURS["disabled"]),
        "glyph_tick": glyph_url("tick", COLOURS["accent_text"], 13),
        "glyph_tick_off": glyph_url("tick", COLOURS["disabled"], 13),
        "glyph_dash": glyph_url("dash", COLOURS["accent_text"], 13),
    }


def template_keys() -> set[str]:
    """Every ``$name`` the style-sheet template substitutes.

    Exposed so a test can prove each one exists in `COLOURS` (or in the geometry values
    added by `style_sheet`). A key that is present in the template but nowhere in the
    substitution map would otherwise land in the sheet as an empty value, and Qt drops the
    whole declaration without a word.
    """
    found: set[str] = set()
    for match in _STYLE_SHEET.pattern.finditer(_STYLE_SHEET.template):
        name = match.group("named") or match.group("braced")
        if name:
            found.add(name)
    return found


#: Keys the template uses that are sizes, not colours. Kept next to `template_keys` so the
#: test can subtract them and check the remainder against `COLOURS`.
GEOMETRY_KEYS: frozenset[str] = frozenset({"button_w", "text_gap"}) | GLYPH_KEYS


def style_sheet(extra: str = "") -> str:
    """The whole application style sheet, with `extra` appended verbatim.

    Raises `KeyError` if the template ever references a name that is not in `COLOURS` or
    `GEOMETRY_KEYS` - `Template.substitute` is used rather than `safe_substitute` on
    purpose, so a typo fails loudly at start-up instead of quietly un-styling a widget.
    """
    values: dict[str, object] = dict(COLOURS)
    values.update(_glyph_values())
    values["button_w"] = _BUTTON_W
    values["text_gap"] = _TEXT_GAP
    sheet = _STYLE_SHEET.substitute(values)
    return sheet + ("\n" + extra if extra else "")


def palette() -> QtGui.QPalette:
    """A dark `QPalette` matching `COLOURS`.

    The style sheet covers the widgets, but anything that paints from the palette - native
    popups, item delegates drawing their own background, `QPainter` code in the overlay -
    would otherwise still use the light default.
    """
    role = QtGui.QPalette.ColorRole
    group = QtGui.QPalette.ColorGroup
    colour = QtGui.QColor

    pal = QtGui.QPalette()
    pal.setColor(role.Window, colour(COLOURS["bg"]))
    pal.setColor(role.WindowText, colour(COLOURS["text"]))
    pal.setColor(role.Base, colour(COLOURS["field"]))
    pal.setColor(role.AlternateBase, colour(COLOURS["panel"]))
    pal.setColor(role.Text, colour(COLOURS["text"]))
    pal.setColor(role.PlaceholderText, colour(COLOURS["text_dim"]))
    pal.setColor(role.Button, colour(COLOURS["panel_raised"]))
    pal.setColor(role.ButtonText, colour(COLOURS["text"]))
    pal.setColor(role.BrightText, colour(COLOURS["error_text"]))
    pal.setColor(role.Highlight, colour(COLOURS["accent"]))
    pal.setColor(role.HighlightedText, colour(COLOURS["accent_text"]))
    pal.setColor(role.ToolTipBase, colour(COLOURS["panel_raised"]))
    pal.setColor(role.ToolTipText, colour(COLOURS["text"]))
    pal.setColor(role.Link, colour(COLOURS["accent_light"]))
    pal.setColor(role.LinkVisited, colour(COLOURS["accent_light"]))
    pal.setColor(role.Light, colour(COLOURS["hover"]))
    pal.setColor(role.Midlight, colour(COLOURS["line_strong"]))
    pal.setColor(role.Mid, colour(COLOURS["line"]))
    pal.setColor(role.Dark, colour(COLOURS["field"]))
    pal.setColor(role.Shadow, colour("#000000"))

    for disabled_role in (role.WindowText, role.Text, role.ButtonText, role.PlaceholderText):
        pal.setColor(group.Disabled, disabled_role, colour(COLOURS["disabled"]))
    pal.setColor(group.Disabled, role.Base, colour(COLOURS["panel"]))
    pal.setColor(group.Disabled, role.Button, colour(COLOURS["panel"]))
    pal.setColor(group.Disabled, role.Highlight, colour(COLOURS["line"]))
    return pal


def apply(app: QtWidgets.QApplication, extra: str = "") -> None:
    """Dress `app`: Fusion style, dark palette, style sheet.

    Fusion first, because the Windows 11 native style ignores several of the sub-control
    rules below (notably the spin box buttons and the check box indicator) and paints its
    own light chrome instead. Fusion honours the sheet on every platform.
    """
    if app is None:  # a caller that forgot QApplication.instance() returned None
        raise ValueError("theme.apply() needs a QApplication, got None")
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(style_sheet(extra))


# ------------------------------------------------------------------- widgets


def caption(text: str) -> QtWidgets.QLabel:
    """A dimmed, word-wrapping label for a hint under a control."""
    label = QtWidgets.QLabel(text)
    label.setObjectName("caption")
    label.setWordWrap(True)
    label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def row(label: str, widget: QtWidgets.QWidget, label_width: int = 110) -> QtWidgets.QWidget:
    """A label on the left, `widget` filling the rest - one line of a settings form.

    The label gets a fixed width so a column of rows lines up without a grid layout. Pass
    an empty `label` for a row that is all control.

    The holder is named ``formRow`` so the style sheet can keep it transparent; a bare
    `QWidget` otherwise fills itself with whatever background the sheet gives `QWidget`.
    """
    holder = QtWidgets.QWidget()
    holder.setObjectName("formRow")
    layout = QtWidgets.QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    text = QtWidgets.QLabel(label)
    text.setObjectName("rowLabel")
    text.setFixedWidth(label_width)
    text.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
    )
    layout.addWidget(text)
    layout.addWidget(widget, 1)
    return holder


class Card(QtWidgets.QFrame):
    """A titled panel: a bold header, an optional subtitle, and a body to fill.

    Add content to the body, not to the card::

        card = Card("Video")
        card.body_layout.addWidget(row("Monitor", combo))
        card.body_layout.addWidget(caption("ddagrab addresses a screen by DXGI index."))

    `body()` returns the same container the layout belongs to, for the caller that would
    rather set its own layout on it; `body_layout` is the convenience for everyone else.
    `content_layout` is an alias of `body_layout`, kept because the contract names it.
    """

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)  # the sheet draws the border

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        self._title = QtWidgets.QLabel(title)
        self._title.setObjectName("cardTitle")
        self._title.setVisible(bool(title))
        outer.addWidget(self._title)

        self._subtitle = caption(subtitle)
        self._subtitle.setVisible(bool(subtitle))
        outer.addWidget(self._subtitle)

        self._body = QtWidgets.QWidget(self)
        self._body.setObjectName("cardBody")
        self._body_layout = QtWidgets.QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(6)
        outer.addWidget(self._body, 1)

        self._outer_layout = outer

    # -- accessors ---------------------------------------------------------

    def body(self) -> QtWidgets.QWidget:
        """The container every piece of content goes into."""
        return self._body

    @property
    def body_layout(self) -> QtWidgets.QVBoxLayout:
        """The vertical layout of `body()`."""
        return self._body_layout

    #: The contract's name for `body_layout`; the same object, not a copy.
    @property
    def content_layout(self) -> QtWidgets.QVBoxLayout:
        return self._body_layout

    def title(self) -> str:
        return self._title.text()

    def set_title(self, text: str) -> None:
        self._title.setText(text)
        self._title.setVisible(bool(text))

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    # -- building ----------------------------------------------------------

    def add_widget(self, widget: QtWidgets.QWidget, stretch: int = 0) -> QtWidgets.QWidget:
        """Append `widget` to the body and return it, so calls can be chained."""
        self._body_layout.addWidget(widget, stretch)
        return widget

    def add_row(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """Append `row(label, widget)` to the body and return the row holder."""
        return self.add_widget(row(label, widget))

    def add_separator(self) -> QtWidgets.QFrame:
        line = QtWidgets.QFrame(self._body)
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
        self._body_layout.addWidget(line)
        return line

    def add_stretch(self, stretch: int = 1) -> None:
        self._body_layout.addStretch(stretch)
