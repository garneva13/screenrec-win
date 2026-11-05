"""Checks for `screenrec.ui.theme`.

Three things are worth proving automatically, because all three fail silently otherwise:

* the contrast numbers written in the module docstring are the numbers the palette
  actually produces (recomputed here from `COLOURS`, by the WCAG formula);
* every ``$name`` in the style-sheet template has a value - Qt drops a declaration with an
  empty value and says nothing, so a typo'd key just un-styles one widget;
* building one of every styled widget and painting it raises nothing. A bad selector is
  ignored by Qt, but a bad *property value* can still blow up at paint time.

No GPU, no recording and no visible window: the Qt part runs under the offscreen
platform plugin, which this file sets before PySide6 is imported.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec.ui import theme  # noqa: E402


# --------------------------------------------------------------------- contrast


def _channel(value: int) -> float:
    """One sRGB channel, 0-255, linearised per WCAG 2.1."""
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of an ``#rrggbb`` string."""
    text = colour.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected #rrggbb, got {colour!r}")
    red, green, blue = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(first: str, second: str) -> float:
    lum_a = relative_luminance(first)
    lum_b = relative_luminance(second)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


class TestPalette(unittest.TestCase):
    def test_every_colour_is_a_hex_triplet(self) -> None:
        for key, value in theme.COLOURS.items():
            with self.subTest(key=key):
                self.assertRegex(value, r"^#[0-9A-Fa-f]{6}$")

    def test_required_keys_exist(self) -> None:
        required = {
            "bg", "panel", "panel_raised", "text", "text_dim", "line", "accent",
            "accent_text", "record", "record_dim", "warn", "ok", "field", "hover",
            "disabled",
        }
        self.assertEqual(set(), required - set(theme.COLOURS))

    def test_body_text_contrast(self) -> None:
        """The four ratios quoted in theme.py's docstring, recomputed."""
        pairs = (
            ("text", "bg"),
            ("text", "panel"),
            ("text_dim", "panel"),
            ("accent_text", "accent"),
        )
        for foreground, background in pairs:
            with self.subTest(pair=f"{foreground} on {background}"):
                ratio = contrast_ratio(
                    theme.COLOURS[foreground], theme.COLOURS[background]
                )
                self.assertGreaterEqual(
                    ratio,
                    4.5,
                    f"{foreground} on {background} is {ratio:.2f}:1, below 4.5:1",
                )

    def test_docstring_numbers_match_the_palette(self) -> None:
        """Catch a palette edit that leaves the quoted numbers stale."""
        expected = {
            ("text", "bg"): 17.50,
            ("text", "panel"): 16.74,
            ("text_dim", "panel"): 5.86,
            ("accent_text", "accent"): 4.71,
        }
        for (foreground, background), quoted in expected.items():
            with self.subTest(pair=f"{foreground} on {background}"):
                ratio = contrast_ratio(
                    theme.COLOURS[foreground], theme.COLOURS[background]
                )
                self.assertAlmostEqual(ratio, quoted, delta=0.01)

    def test_status_colours_are_readable_on_panel(self) -> None:
        for key in ("warn", "ok", "error_text", "accent_light"):
            with self.subTest(key=key):
                ratio = contrast_ratio(theme.COLOURS[key], theme.COLOURS["panel"])
                self.assertGreaterEqual(ratio, 4.5)

    def test_disabled_is_deliberately_dimmer(self) -> None:
        """Documented exception: disabled text is exempt and must read as unavailable."""
        ratio = contrast_ratio(theme.COLOURS["disabled"], theme.COLOURS["panel"])
        self.assertLess(ratio, 4.5)
        self.assertGreater(ratio, 2.5)


# ----------------------------------------------------------------- style sheet


def _without_comments(sheet: str) -> str:
    """The sheet with its ``/* ... */`` comments removed.

    The comments name the selectors that must *not* be used, so a plain substring search
    over the raw sheet would find them there and fail for the wrong reason.
    """
    import re

    return re.sub(r"/\*.*?\*/", "", sheet, flags=re.DOTALL)


class TestStyleSheet(unittest.TestCase):
    def test_every_template_key_has_a_value(self) -> None:
        unknown = theme.template_keys() - set(theme.COLOURS) - set(theme.GEOMETRY_KEYS)
        self.assertEqual(
            set(),
            unknown,
            f"style_sheet() references names with no value: {sorted(unknown)}",
        )

    def test_template_actually_uses_keys(self) -> None:
        """Guard against the scan silently finding nothing and the check passing."""
        self.assertGreater(len(theme.template_keys()), 10)

    def test_style_sheet_substitutes_everything(self) -> None:
        sheet = theme.style_sheet()
        self.assertNotIn("$", sheet)
        self.assertIn(theme.COLOURS["accent"], sheet)
        self.assertIn(theme.COLOURS["record"], sheet)

    def test_no_empty_declarations(self) -> None:
        """A missing substitution would show up as ``property:;`` - Qt drops those."""
        import re

        bad = re.findall(r"[\w-]+\s*:\s*;", theme.style_sheet())
        self.assertEqual([], bad)

    def test_extra_is_appended(self) -> None:
        sheet = theme.style_sheet("QLabel#x { color: red; }")
        self.assertTrue(sheet.rstrip().endswith("QLabel#x { color: red; }"))

    #: A type or id selector, then one or more pseudo-states, then a sub-control - the
    #: order Qt ignores. The earlier pattern here (``::?[\w-]+:[\w-]+::[\w-]+``) matched
    #: none of the three forms below, so this guard used to pass vacuously.
    WRONG_ORDER = r"[\w#]+(?::[\w-]+)+::[\w-]+"

    def test_the_selector_order_pattern_catches_the_mistake(self) -> None:
        """Prove the guard below can fail before trusting that it passes."""
        import re

        for text in (
            "QSlider:disabled::handle { color: red; }",
            "QSpinBox:hover::up-button { }",
            "QComboBox:disabled::drop-down { }",
            "QPushButton#primary:disabled::menu-indicator { }",
        ):
            with self.subTest(wrong=text):
                self.assertTrue(
                    re.findall(self.WRONG_ORDER, text),
                    f"the pattern misses {text!r}, so the guard proves nothing",
                )

        # ...and does not fire on the correct order, or on a declaration.
        for text in (
            "QSlider::handle:horizontal:disabled { }",
            "QSpinBox::up-button:disabled, QDoubleSpinBox::up-button:disabled { }",
            "QComboBox QAbstractItemView::item:selected { }",
            "    background-color: #2563C9;",
        ):
            with self.subTest(right=text):
                self.assertEqual([], re.findall(self.WRONG_ORDER, text))

    def test_sub_control_comes_before_state(self) -> None:
        """``QSlider:disabled::handle`` matches nothing; catch the order being flipped."""
        import re

        wrong = re.findall(self.WRONG_ORDER, theme.style_sheet())
        self.assertEqual([], wrong, f"state before sub-control in: {wrong}")

    def test_spin_buttons_are_anchored_to_the_border_box(self) -> None:
        sheet = theme.style_sheet()
        start = sheet.index("QSpinBox::up-button")
        block = sheet[start : sheet.index("}", start)]
        self.assertIn("subcontrol-origin: border", block)
        self.assertIn("width:", block)
        self.assertIn("height:", block)

    def test_base_widget_rule_paints_no_background(self) -> None:
        """A background here is painted by every bare container, not just the window.

        Qt sets ``WA_StyledBackground`` on direct ``QWidget`` instances, so a universal
        background fills a card's body, a form row and a text edit's viewport as well,
        covering the panel and field colours. ``test_containers_show_the_surface_they_sit_on``
        checks the result on real pixels; this checks the cause.
        """
        import re

        sheet = _without_comments(theme.style_sheet())
        start = sheet.index("QWidget {")
        block = sheet[start : sheet.index("}", start)]
        # `selection-background-color` is not a fill and must not trip this.
        fills = re.findall(r"(?m)^\s*(background(?:-color)?)\s*:", block)
        self.assertEqual([], fills, f"universal background in: {block!r}")

    def test_no_container_rule_outranks_the_control_rules(self) -> None:
        """Two type selectors beat one, so these would strip `field` off the controls."""
        sheet = _without_comments(theme.style_sheet())
        for selector in ("QGroupBox > QWidget", "QFrame#card QWidget"):
            with self.subTest(selector=selector):
                self.assertNotIn(selector, sheet)

    def test_every_required_widget_is_styled(self) -> None:
        sheet = theme.style_sheet()
        for selector in (
            "QWidget", "QLabel", "QPushButton", "QPushButton#primary",
            "QPushButton#record", "QPushButton#stop", "QPushButton#ghost",
            "QComboBox", "QComboBox QAbstractItemView", "QSpinBox", "QLineEdit",
            "QCheckBox", "QGroupBox", "QFrame#card", "QToolButton", "QProgressBar",
            "QScrollArea", "QScrollBar:vertical", "QMenu", "QToolTip",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, sheet)


# ---------------------------------------------------------------- live widgets


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if isinstance(existing, QtWidgets.QApplication):
        return existing
    return QtWidgets.QApplication(sys.argv[:1])


class TestWidgets(unittest.TestCase):
    """Build the real widgets under the offscreen platform and paint them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()
        theme.apply(cls.app)

    def test_style_sheet_was_installed(self) -> None:
        self.assertIn("QPushButton#record", self.app.styleSheet())
        self.assertEqual(
            QtGui.QColor(theme.COLOURS["bg"]),
            self.app.palette().color(QtGui.QPalette.ColorRole.Window),
        )

    def test_apply_rejects_none(self) -> None:
        with self.assertRaises(ValueError):
            theme.apply(None)  # type: ignore[arg-type]

    def test_one_of_every_widget_paints(self) -> None:
        host = QtWidgets.QWidget()
        host.resize(520, 760)
        layout = QtWidgets.QVBoxLayout(host)

        card = theme.Card("Video", "One card, with a header and a body.")
        combo = QtWidgets.QComboBox()
        combo.addItems(["0: 2560x1440 at 0,0", "1: 1440x2560 at 2560,0, rotated 90"])
        card.add_row("Monitor", combo)

        spin = QtWidgets.QSpinBox()
        spin.setRange(5, 240)
        spin.setValue(60)
        spin.setSuffix(" fps")
        card.add_row("Framerate", spin)

        edit = QtWidgets.QLineEdit("ScreenRec_%Y-%m-%d_%H-%M-%S")
        card.add_row("Pattern", edit)

        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.setRange(0, 51)
        slider.setValue(21)
        card.add_row("Quality", slider)

        check = QtWidgets.QCheckBox("Highlight clicks")
        check.setChecked(True)
        card.add_widget(check)

        disabled = QtWidgets.QCheckBox("Disabled, for the dim colour")
        disabled.setEnabled(False)
        card.add_widget(disabled)

        card.add_separator()
        card.add_widget(theme.caption("A caption under the controls."))

        group = QtWidgets.QGroupBox("Audio")
        group_layout = QtWidgets.QVBoxLayout(group)
        group_layout.addWidget(QtWidgets.QCheckBox("Microphone"))
        group_layout.addWidget(theme.row("Device", QtWidgets.QComboBox()))
        card.add_widget(group)

        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(42)
        card.add_widget(bar)

        buttons = QtWidgets.QWidget()
        button_layout = QtWidgets.QHBoxLayout(buttons)
        for name, label in (
            ("record", "Record"),
            ("stop", "Stop"),
            ("primary", "Save"),
            ("ghost", "Cancel"),
            ("", "Plain"),
        ):
            button = QtWidgets.QPushButton(label)
            button.setObjectName(name)
            button_layout.addWidget(button)
        tool = QtWidgets.QToolButton()
        tool.setText("...")
        button_layout.addWidget(tool)
        card.add_widget(buttons)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QLabel("\n".join(f"log line {i}" for i in range(40)))
        scroll.setWidget(inner)
        scroll.setFixedHeight(90)

        layout.addWidget(QtWidgets.QLabel("ScreenRec"))
        layout.addWidget(card)
        layout.addWidget(scroll)

        host.show()
        self.app.processEvents()

        pixmap = host.grab()
        self.assertFalse(pixmap.isNull())
        # Not the requested 520 wide: the controls have a larger minimum. Compare with
        # what the widget actually became rather than with the hint.
        self.assertEqual(
            (host.width(), host.height()), (pixmap.width(), pixmap.height())
        )

        # Grab each styled widget on its own too: a bad property value raises at paint
        # time, and a whole-window grab can clip a widget out of the damaged region.
        for widget in (card, combo, spin, edit, slider, check, group, bar, scroll, tool):
            with self.subTest(widget=type(widget).__name__):
                self.assertFalse(widget.grab().isNull())

        host.close()
        host.deleteLater()
        self.app.processEvents()

    def test_containers_show_the_surface_they_sit_on(self) -> None:
        """Real pixels: a card's body is `panel`, and its controls keep `field`.

        The failure this guards against is invisible to a selector check - the sheet is
        valid either way, the card simply comes out as a dark hole with a lighter frame.
        """
        host = QtWidgets.QWidget()
        host.resize(460, 300)
        layout = QtWidgets.QVBoxLayout(host)

        card = theme.Card("Video", "sub")
        combo = QtWidgets.QComboBox()
        combo.addItems(["0: 2560x1440 at 0,0"])
        row_holder = card.add_row("Monitor", combo)

        # A bare container assembled by the caller, as main_window does.
        box = QtWidgets.QWidget()
        box_layout = QtWidgets.QHBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addWidget(QtWidgets.QLabel("not set"))
        box_layout.addStretch(1)
        card.add_widget(box)

        group = QtWidgets.QGroupBox("Audio")
        group_layout = QtWidgets.QVBoxLayout(group)
        group_combo = QtWidgets.QComboBox()  # a direct child of the group box
        group_combo.addItems(["Default"])
        group_layout.addWidget(group_combo)
        group_edit = QtWidgets.QLineEdit("pattern")
        group_layout.addWidget(group_edit)
        card.add_widget(group)

        layout.addWidget(card)
        host.show()
        self.app.processEvents()
        image = host.grab().toImage()

        def colour_at(widget: QtWidgets.QWidget, dx: int, dy: int) -> str:
            point = widget.mapTo(host, QtCore.QPoint(dx, dy))
            return image.pixelColor(point.x(), point.y()).name().lower()

        panel = theme.COLOURS["panel"].lower()
        field = theme.COLOURS["field"].lower()
        for label, widget, offset, expected in (
            ("card body", card.body(), (2, 2), panel),
            ("row holder", row_holder, (2, 2), panel),
            ("bare container", box, (box.width() - 3, 2), panel),
            ("group box", group, (6, 24), panel),
            ("combo in card", combo, (10, 10), field),
            ("combo in group box", group_combo, (10, 10), field),
            ("line edit in group box", group_edit, (10, 10), field),
        ):
            with self.subTest(where=label):
                self.assertEqual(
                    expected, colour_at(widget, *offset), f"{label} painted the wrong surface"
                )

        host.close()
        host.deleteLater()
        self.app.processEvents()

    def test_menu_and_tooltip_paint(self) -> None:
        menu = QtWidgets.QMenu()
        menu.addAction("Start recording")
        menu.addSeparator()
        action = menu.addAction("Unavailable")
        action.setEnabled(False)
        menu.ensurePolished()
        self.assertFalse(menu.grab().isNull())

        tip = QtWidgets.QLabel("tooltip-ish")
        tip.setToolTip("Writing q to stdin makes ffmpeg write the trailer.")
        self.assertEqual(
            "Writing q to stdin makes ffmpeg write the trailer.", tip.toolTip()
        )
        menu.deleteLater()

    def test_card_api(self) -> None:
        card = theme.Card("Title", "Subtitle")
        self.assertEqual("Title", card.title())
        self.assertIs(card.content_layout, card.body_layout)
        self.assertIs(card.body_layout.parentWidget(), card.body())

        label = QtWidgets.QLabel("x")
        self.assertIs(label, card.add_widget(label))
        self.assertEqual(1, card.body_layout.count())

        card.add_row("Label", QtWidgets.QComboBox())
        card.add_separator()
        self.assertEqual(3, card.body_layout.count())
        card.add_stretch()
        self.assertEqual(4, card.body_layout.count())  # the stretch is an item too
        self.assertIsNone(card.body_layout.itemAt(3).widget())

        card.set_title("")
        self.assertFalse(card.findChild(QtWidgets.QLabel, "cardTitle").isVisible())
        card.deleteLater()

    def test_row_and_caption(self) -> None:
        holder = theme.row("Monitor", QtWidgets.QComboBox())
        labels = holder.findChildren(QtWidgets.QLabel)
        self.assertEqual(1, len(labels))
        self.assertEqual("Monitor", labels[0].text())
        self.assertEqual("rowLabel", labels[0].objectName())

        note = theme.caption("hint")
        self.assertEqual("caption", note.objectName())
        self.assertTrue(note.wordWrap())
        holder.deleteLater()
        note.deleteLater()


if __name__ == "__main__":
    unittest.main()
