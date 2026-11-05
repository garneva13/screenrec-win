"""Every top-level window's title, in both languages.

A window title is read in two places a person cannot avoid - the taskbar button and the
Alt+Tab thumbnail - and it is the one string a window shows while nobody is looking at
the window itself. It is also the string easiest to forget, because no test that drives
a widget ever reads it: `annotate.textbox` was translated and the five other top-level
windows were not, and nothing went red.

Two checks, and the first one is the one that matters.

* `SetWindowTitleSourceTest` reads the source of `app/screenrec` and fails on a
  `setWindowTitle` whose argument never reaches `_()` or `pick()`. It is not a list of
  the windows that exist today, so a window added next year is covered the day it is
  written, which a list would not be.
* `WindowTitleTest` builds each window and switches the language under it, which is the
  half the source cannot prove: that the title is really in the catalogue, and that
  `retranslate` is really connected, so a window already on screen when the switch is
  thrown changes with it.

Neither pins a Russian sentence. The expected text is read back out of the catalogue, so
rewording a title is one edit in one block, as it is everywhere else in this suite.

No GPU and no visible window: the Qt part runs under the offscreen platform plugin,
which this file sets before PySide6 is imported.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path
from typing import Callable, Iterator

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtMultimedia, QtWidgets  # noqa: E402

from screenrec import i18n, overlay, winapi  # noqa: E402
from screenrec.annotate import canvas as canvas_module  # noqa: E402
from screenrec.annotate import model, textbox  # noqa: E402
from screenrec.player import window as player_window  # noqa: E402
from screenrec.ui import captureframe  # noqa: E402

SOURCE_ROOT = _APP_DIR / "screenrec"

#: The names a translated string can come out of. `pick` is here because a window title
#: is exactly the kind of short phrase whose two senses may need telling apart.
TRANSLATORS = frozenset({"_", "pick"})


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


def _monitor() -> winapi.Monitor:
    """One ordinary screen, so no check here depends on what is plugged in."""
    return winapi.Monitor(
        index=0,
        device_name="\\\\.\\DISPLAY1",
        x=0,
        y=0,
        width=2560,
        height=1440,
        rotation=0,
        adapter="test",
    )


# ------------------------------------------------------------------ the source


def _source_files() -> Iterator[Path]:
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _translates(node: ast.AST) -> bool:
    """Does this expression pass through `_()` or `pick()` anywhere inside it?

    Anywhere, rather than at the top, so that `_("ScreenRec - {name}").format(name=x)`
    counts: the template is the translated part and the name is the caller's own word.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in TRANSLATORS:
            return True
    return False


def _untranslated_titles() -> list[str]:
    """`file:line` for every `setWindowTitle` whose argument is not translated."""
    found: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "setWindowTitle":
                continue
            if node.args and _translates(node.args[0]):
                continue
            shown = ast.unparse(node.args[0]) if node.args else "<no argument>"
            found.append(
                f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}  {shown}"
            )
    return found


class SetWindowTitleSourceTest(unittest.TestCase):
    """THE DRIFT TEST for titles: a window whose name the taskbar shows in English."""

    def test_every_window_title_is_translated(self) -> None:
        untranslated = _untranslated_titles()
        self.assertEqual(
            untranslated, [],
            "a window title is read in the taskbar and in Alt+Tab, so it is "
            "translated like any other string:\n  "
            + "\n  ".join(untranslated)
            + "\n\nWrap it in _(), and for a title with a name in it use a template - "
            '_("ScreenRec - {name}").format(name=...) - not an f-string.',
        )

    def test_the_check_reads_the_real_windows(self) -> None:
        """A guard on the guard: a rule that matches nothing proves nothing."""
        titles = [
            node
            for path in _source_files()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setWindowTitle"
        ]
        self.assertGreaterEqual(len(titles), 6, "the title calls stopped being found")

    def test_an_untranslated_argument_is_what_the_rule_rejects(self) -> None:
        rejected = ('self.setWindowTitle("ScreenRec")', 'x.setWindowTitle(f"a {b}")')
        for source in rejected:
            with self.subTest(source=source):
                call = ast.parse(source).body[0].value
                self.assertFalse(_translates(call.args[0]))
        accepted = (
            'self.setWindowTitle(_("ScreenRec"))',
            'self.setWindowTitle(i18n._("ScreenRec"))',
            'self.setWindowTitle(pick("title.main", "ScreenRec"))',
            'self.setWindowTitle(_("ScreenRec - {name}").format(name=self._title))',
        )
        for source in accepted:
            with self.subTest(source=source):
                call = ast.parse(source).body[0].value
                self.assertTrue(_translates(call.args[0]))


# ------------------------------------------------------------------ the windows


class WindowTitleTest(unittest.TestCase):
    """Each window really is in the catalogue, and really follows a live switch."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        previous = i18n.language()
        i18n.set_language(i18n.LANG_EN)
        self.addCleanup(i18n.set_language, previous)

        real = winapi.list_monitors
        winapi.list_monitors = lambda: [_monitor()]
        self.addCleanup(setattr, winapi, "list_monitors", real)

    def _build(self, factory: Callable[[], QtWidgets.QWidget]) -> QtWidgets.QWidget:
        widget = factory()
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.hide)
        return widget

    def windows(self) -> list[tuple[str, Callable[[], QtWidgets.QWidget], str]]:
        """Every top-level window, with the English title each should be built with."""
        return [
            (
                "annotate.Canvas",
                lambda: canvas_module.Canvas(model.Document()),
                "ScreenRec - annotations",
            ),
            (
                "annotate.TextBox",
                lambda: textbox.TextBox(model.ToolSettings()),
                "ScreenRec - annotation text",
            ),
            (
                "overlay.RegionSelector",
                overlay.RegionSelector,
                "ScreenRec - select a region",
            ),
            (
                "overlay.ClickHighlight",
                overlay.ClickHighlight,
                "ScreenRec - click highlight",
            ),
            (
                "ui.CaptureFrame",
                captureframe.CaptureFrame,
                "ScreenRec - capture frame",
            ),
            (
                "player.TransportBar",
                player_window.TransportBar,
                "ScreenRec player",
            ),
        ]

    def test_english_shows_the_source_sentence(self) -> None:
        for name, factory, english in self.windows():
            with self.subTest(window=name):
                self.assertEqual(self._build(factory).windowTitle(), english)

    def test_every_title_has_russian_in_the_catalogue(self) -> None:
        wanted = [english for _name, _factory, english in self.windows()]
        self.assertEqual(i18n.missing_translations(wanted), [])

    def test_a_window_built_in_russian_opens_in_russian(self) -> None:
        for name, factory, english in self.windows():
            with self.subTest(window=name):
                i18n.set_language(i18n.LANG_RU)
                widget = self._build(factory)
                self.assertEqual(widget.windowTitle(), i18n._(english))
                self.assertNotEqual(widget.windowTitle(), english)

    def test_a_window_already_open_follows_the_switch_both_ways(self) -> None:
        """The half the source cannot prove: `retranslate` is actually connected."""
        for name, factory, english in self.windows():
            with self.subTest(window=name):
                widget = self._build(factory)
                i18n.set_language(i18n.LANG_RU)
                self.assertEqual(widget.windowTitle(), i18n._(english))
                i18n.set_language(i18n.LANG_EN)
                self.assertEqual(widget.windowTitle(), english)


class ScreenViewTitleTest(unittest.TestCase):
    """The one title with a name in it: only the frame round the name is translated."""

    SCREEN = "Screen 1"
    TEMPLATE = "ScreenRec - {name}"

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        previous = i18n.language()
        i18n.set_language(i18n.LANG_EN)
        self.addCleanup(i18n.set_language, previous)
        self.view = player_window.ScreenView(self.SCREEN, QtMultimedia.QVideoSink())
        self.addCleanup(self.view.deleteLater)
        self.addCleanup(self.view.hide)

    def _expected(self) -> str:
        return i18n._(self.TEMPLATE).format(name=self.SCREEN)

    def test_the_template_is_translated_and_the_name_is_not(self) -> None:
        self.assertEqual(self.view.windowTitle(), f"ScreenRec - {self.SCREEN}")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(self.view.windowTitle(), self._expected())
        self.assertIn(self.SCREEN, self.view.windowTitle())

    def test_set_label_keeps_the_language_in_force(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.view.set_label("Screen 2")
        self.assertEqual(
            self.view.windowTitle(), i18n._(self.TEMPLATE).format(name="Screen 2")
        )

    def test_the_template_survives_a_round_trip_with_its_field(self) -> None:
        """A Russian template that lost `{name}` would silently drop the screen."""
        for code in i18n.LANGUAGES:
            with self.subTest(language=code):
                i18n.set_language(code)
                self.assertIn("{name}", i18n._(self.TEMPLATE))


if __name__ == "__main__":
    unittest.main()
