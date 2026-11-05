"""Checks for `screenrec.player.chooser` - the dialog shown before playback starts.

The dialog is the first thing a viewer of a two-screen recording sees, and almost
everything it has to get right is a rule from `docs/PLAYER_LAYOUTS.md` §2 rather than a
matter of taste: the recommended card is the one `layouts.recommend` names, the
impossible card is shown and disabled with the reason on it, the "displays have changed"
line appears when and only when `layouts.layout_for_signature` says the saved choice no
longer holds, and the window opens inside a screen that exists. Each of those is a test
here, so the dialog can be checked without a person and without a second monitor.

Two things are deliberately **not** pinned. The wording of any sentence: the Russian
belongs to `translations/player_chooser.py` and the `player` block, and a test that
spells a sentence out turns a reworded label into a red suite. And the exact numbers of
the recommendation: those are `tests/test_player_layouts.py`'s, and asserting them again
here would mean two files to edit when the formula changes. What is checked is that the
dialog shows *what `layouts` returned*, in the language in force at the moment it is
asked - which is the only half of it this module is responsible for.

No media, no engine and no visible window: the Qt part runs under the offscreen platform
plugin, set before PySide6 is imported, and the chooser has nothing to play anything
with - see `EngineDistanceTest` at the bottom, which is the proof rather than the claim.
"""
from __future__ import annotations

import ast
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
from PySide6.QtTest import QTest  # noqa: E402

from screenrec import i18n  # noqa: E402
from screenrec.player import chooser, layouts  # noqa: E402
from screenrec.player.window import screen_titles  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


#: The user's own recording: a 2560x1440 landscape screen and a rotated 1440x2560 one.
#: The same pair `tests/test_player_layouts.py` scores, so the recommendation this
#: dialog shows is one that file has already proved the arithmetic of.
STREAMS = (
    layouts.Stream(index=0, title="Screen 1", size=(2560, 1440)),
    layouts.Stream(index=1, title="Screen 2", size=(1440, 2560)),
)

#: One monitor, taskbar already excluded - the desk this whole feature exists for.
ONE_MONITOR = (QtCore.QRect(0, 0, 2560, 1392),)
#: The user's two-monitor desk.
TWO_MONITORS = (
    QtCore.QRect(0, 0, 2560, 1392),
    QtCore.QRect(2560, 0, 1440, 2512),
)


class Stored:
    """What the dialog reads out of `config.Settings` - §2.3's two fields.

    A stand-in rather than the real dataclass, because the chooser reads these with
    `getattr` and writes nothing: a test that built a `Settings` would be checking
    `config`'s defaults as well as this dialog's behaviour.
    """

    def __init__(self, layout: str = "", signature: str = "") -> None:
        self.player_layout = layout
        self.player_display_signature = signature


class Recording:
    """The two fields `window.screen_titles` reads, and nothing else.

    A stand-in rather than a real `.screenrec.json` index, because the chooser opens
    no media: the only question it ever asks a recording is what its screens are
    called, so a test that loaded one would be exercising the sidecar reader instead.
    `titles` left as `None` is the recording that carries no names of its own, which
    is the case where `screen_titles` invents "Screen 2" and the case this dialog has
    to re-generate on a language switch.
    """

    def __init__(self, count: int = 2, titles: list[str] | None = None) -> None:
        self.screens = [{"monitor": index} for index in range(count)]
        if titles is not None:
            self.titles = titles


class ChooserTestCase(unittest.TestCase):
    """A dialog per test, and the language put back afterwards.

    The language is process-wide state, like a setting; a test that switched it and
    left would turn every later test's English assertion red.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)

    def make(
        self,
        areas: tuple[QtCore.QRect, ...] = ONE_MONITOR,
        settings: object | None = None,
        streams: tuple[layouts.Stream, ...] = STREAMS,
        recording: object | None = None,
    ) -> chooser.LayoutChooser:
        dialog = chooser.LayoutChooser(
            streams, areas, settings if settings is not None else Stored(), recording
        )
        self.addCleanup(dialog.deleteLater)
        return dialog


# ------------------------------------------------------------------- the cards


class CardsTest(ChooserTestCase):
    """§2.1: a card per layout, drawn, in the contract's own order."""

    def test_there_is_a_card_for_every_layout(self) -> None:
        dialog = self.make()
        self.assertEqual(set(dialog.cards), set(layouts.LAYOUTS))

    def test_the_cards_are_laid_out_in_the_contract_s_order(self) -> None:
        """§1's table, read left to right and top to bottom off the grid itself."""
        dialog = self.make()
        grid = dialog.grid_holder.layout()
        placed: list[tuple[int, int, str]] = []
        for layout, card in dialog.cards.items():
            index = grid.indexOf(card)
            row, column, _rows, _columns = grid.getItemPosition(index)
            placed.append((row, column, layout))
        placed.sort()
        self.assertEqual([entry[2] for entry in placed], list(layouts.LAYOUTS))

    def test_a_short_last_row_is_centred_and_leaves_no_empty_cell(self) -> None:
        """Picture in picture was the sixth card; five into three leaves a hole.

        The hole was cell (1, 2) - 236 px of blank card space under "Focus on one",
        which reads as a card that failed to draw rather than as a row that is short.
        Measured off the shown dialog rather than off the layout, because what the
        viewer sees is where the widgets ended up.
        """
        dialog = self.make()
        dialog.resize(dialog.sizeHint())
        dialog.show()
        self.addCleanup(dialog.hide)
        QtWidgets.QApplication.processEvents()

        rows: dict[int, list[QtCore.QRect]] = {}
        for card in dialog.cards.values():
            rows.setdefault(card.y(), []).append(card.geometry())
        self.assertEqual(len(rows), 2, "the cards are not in two rows any more")

        full, short = (rows[key] for key in sorted(rows))
        self.assertEqual(len(full), chooser.COLUMNS)
        self.assertLess(len(short), chooser.COLUMNS)

        def span(boxes: list[QtCore.QRect]) -> tuple[int, int]:
            return min(b.left() for b in boxes), max(b.right() for b in boxes)

        full_left, full_right = span(full)
        short_left, short_right = span(short)
        self.assertGreater(short_left, full_left, "the short row was left hanging")
        self.assertLess(short_right, full_right)
        # Centred: the same slack on both sides, to within a pixel of rounding.
        self.assertLessEqual(
            abs((short_left - full_left) - (full_right - short_right)), 1
        )

    def test_every_grid_cell_in_use_holds_a_card(self) -> None:
        """No layout is placed on top of another by the half-column arithmetic."""
        dialog = self.make()
        grid = dialog.grid_holder.layout()
        taken: set[tuple[int, int]] = set()
        for card in dialog.cards.values():
            row, column, _rows, columns = grid.getItemPosition(grid.indexOf(card))
            cells = {(row, column + step) for step in range(columns)}
            self.assertFalse(cells & taken, "two cards share a cell")
            taken |= cells

    def test_a_card_reads_its_title_from_layouts(self) -> None:
        """Not from a copy of the table: one spelling of every layout's name."""
        dialog = self.make()
        for layout, card in dialog.cards.items():
            self.assertEqual(card.title_text(), layouts.title(layout))
            self.assertEqual(card.description_text(), layouts.description(layout))

    def test_a_card_cannot_be_built_for_a_layout_that_does_not_exist(self) -> None:
        with self.assertRaises(ValueError):
            chooser.LayoutCard("mosaic", [(1920, 1080), (1920, 1080)])


class RecommendationTest(ChooserTestCase):
    """§2.2: the recommendation is marked, preselected, and says why."""

    def test_the_recommendation_is_preselected(self) -> None:
        wanted, _why = layouts.recommend(ONE_MONITOR, STREAMS)
        dialog = self.make()
        self.assertEqual(dialog.selected(), wanted)
        self.assertTrue(dialog.cards[wanted].is_selected())

    def test_the_recommendation_is_preselected_on_two_monitors_too(self) -> None:
        """The desk that changes the answer: `one_per_monitor` becomes the advice."""
        wanted, _why = layouts.recommend(TWO_MONITORS, STREAMS)
        self.assertEqual(wanted, layouts.LAYOUT_ONE_PER_MONITOR)
        dialog = self.make(TWO_MONITORS)
        self.assertEqual(dialog.selected(), wanted)

    def test_only_the_recommended_card_is_badged(self) -> None:
        wanted, _why = layouts.recommend(ONE_MONITOR, STREAMS)
        dialog = self.make()
        badged = [name for name, card in dialog.cards.items() if card.is_recommended()]
        self.assertEqual(badged, [wanted])

    def test_the_recommended_card_carries_the_reason_layouts_gives(self) -> None:
        wanted, why = layouts.recommend(ONE_MONITOR, STREAMS)
        dialog = self.make()
        self.assertEqual(dialog.cards[wanted].reason_text(), why)
        self.assertNotEqual(why, "")

    def test_every_offered_card_carries_a_reason(self) -> None:
        """§2.2's last line: `separate` and `focus` get "a reason line of their own".

        The dialog used to put the line under the recommended card only, so a viewer
        choosing one of the five it does not advise was told nothing about what they
        were about to get - while `layouts` had already built the sentence.
        """
        for areas in (ONE_MONITOR, TWO_MONITORS):
            dialog = self.make(areas)
            for name, card in dialog.cards.items():
                if card.isEnabled():
                    self.assertNotEqual(
                        card.reason_text(), "", f"{name} on {len(areas)} monitor(s)"
                    )

    def test_focus_and_separate_say_what_they_will_give(self) -> None:
        """The two that are never recommended, named: this is the failing case.

        `layouts.score`'s docstring is what is being honoured - the reason line is
        built for a layout that cannot win "so a card the viewer picks says what they
        will get" - and these are the two layouts that cannot win.
        """
        dialog = self.make(ONE_MONITOR)
        for name in sorted(layouts.NEVER_RECOMMENDED):
            card = dialog.cards[name]
            self.assertTrue(card.isEnabled(), name)
            self.assertFalse(card.is_recommended(), name)
            self.assertNotEqual(card.reason_text(), "", name)

    def test_a_card_s_reason_is_the_sentence_layouts_builds_for_it(self) -> None:
        """Not a second wording here: one spelling of every one of these sentences."""
        dialog = self.make(TWO_MONITORS)
        recommended, why = layouts.recommend(TWO_MONITORS, STREAMS)
        for name, card in dialog.cards.items():
            if not card.isEnabled():
                continue
            wanted = why if name == recommended else layouts._reason(
                name, TWO_MONITORS, STREAMS
            )
            self.assertEqual(card.reason_text(), wanted, name)

    def test_a_disabled_card_shows_its_why_not_instead_of_a_reason(self) -> None:
        """One line under a card, and on a dead card it is the one saying why."""
        card = self.make(ONE_MONITOR).cards[layouts.LAYOUT_ONE_PER_MONITOR]
        self.assertFalse(card.isEnabled())
        self.assertEqual(card.reason_text(), "")
        self.assertNotEqual(card.note_text(), "")


class DisabledCardTest(ChooserTestCase):
    """§2.1: `one_per_monitor` is always shown, and disabled with the reason on it."""

    def test_one_per_monitor_is_shown_and_disabled_on_one_monitor(self) -> None:
        dialog = self.make(ONE_MONITOR)
        card = dialog.cards[layouts.LAYOUT_ONE_PER_MONITOR]
        self.assertIn(layouts.LAYOUT_ONE_PER_MONITOR, dialog.cards)
        self.assertFalse(card.isEnabled())
        self.assertNotEqual(card.note_text(), "")

    def test_one_per_monitor_is_offered_on_two_monitors(self) -> None:
        dialog = self.make(TWO_MONITORS)
        card = dialog.cards[layouts.LAYOUT_ONE_PER_MONITOR]
        self.assertTrue(card.isEnabled())
        self.assertEqual(card.note_text(), "")

    def test_layouts_possible_is_what_decides(self) -> None:
        """Not a second rule here: every card's state is `layouts.possible`'s answer."""
        for areas in (ONE_MONITOR, TWO_MONITORS):
            dialog = self.make(areas)
            for name, card in dialog.cards.items():
                self.assertEqual(
                    card.isEnabled(),
                    layouts.possible(name, areas, STREAMS),
                    f"{name} on {len(areas)} monitor(s)",
                )

    def test_a_disabled_card_cannot_be_selected(self) -> None:
        dialog = self.make(ONE_MONITOR)
        before = dialog.selected()
        self.assertFalse(dialog.select(layouts.LAYOUT_ONE_PER_MONITOR))
        self.assertEqual(dialog.selected(), before)

    def test_a_disabled_card_does_not_answer_a_click(self) -> None:
        dialog = self.make(ONE_MONITOR)
        before = dialog.selected()
        QTest.mouseClick(
            dialog.cards[layouts.LAYOUT_ONE_PER_MONITOR],
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.assertEqual(dialog.selected(), before)

    def test_an_unknown_layout_is_refused_rather_than_guessed(self) -> None:
        dialog = self.make()
        before = dialog.selected()
        self.assertFalse(dialog.select("mosaic"))
        self.assertEqual(dialog.selected(), before)

    def test_every_card_is_disabled_when_there_is_nothing_to_arrange(self) -> None:
        """A one-stream recording never reaches the dialog, but if it did, Open is
        refused rather than left promising a layout nothing can build."""
        dialog = self.make(ONE_MONITOR, streams=STREAMS[:1])
        self.assertEqual(dialog.selected(), "")
        self.assertFalse(dialog.open_button.isEnabled())
        for name, card in dialog.cards.items():
            self.assertFalse(card.isEnabled(), name)
            self.assertNotEqual(card.note_text(), "", name)


# ---------------------------------------------------------------- the chip


class MonitorCountTest(ChooserTestCase):
    """§2.1's "how many monitors are available", under the Russian plural rule."""

    def test_the_chip_counts_the_areas_it_was_given(self) -> None:
        self.assertEqual(self.make(ONE_MONITOR).monitor_chip.text(),
                         chooser.monitors_available(1))
        self.assertEqual(self.make(TWO_MONITORS).monitor_chip.text(),
                         chooser.monitors_available(2))

    def test_the_count_is_in_the_sentence(self) -> None:
        for count in (1, 2, 5, 11):
            self.assertIn(str(count), chooser.monitors_available(count))

    def test_english_has_two_forms(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        self.assertNotEqual(
            self._shape(chooser.monitors_available(1)),
            self._shape(chooser.monitors_available(2)),
        )
        self.assertEqual(
            self._shape(chooser.monitors_available(2)),
            self._shape(chooser.monitors_available(5)),
        )

    def test_russian_has_three(self) -> None:
        """1 монитор / 2 монитора / 5 мониторов - three shapes, not two.

        Compared as shapes with the number taken out, so the rule is checked without
        this file spelling a Russian word that belongs to the catalogue.
        """
        i18n.set_language(i18n.LANG_RU)
        one = self._shape(chooser.monitors_available(1))
        few = self._shape(chooser.monitors_available(2))
        many = self._shape(chooser.monitors_available(5))
        self.assertEqual(len({one, few, many}), 3)

    def test_the_russian_eleven_to_fourteen_exception(self) -> None:
        """11 takes the *many* form although it ends in 1; 21 takes the *one* form."""
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(
            self._shape(chooser.monitors_available(11)),
            self._shape(chooser.monitors_available(5)),
        )
        self.assertEqual(
            self._shape(chooser.monitors_available(21)),
            self._shape(chooser.monitors_available(1)),
        )

    def test_the_chip_follows_a_language_change(self) -> None:
        dialog = self.make(TWO_MONITORS)
        i18n.set_language(i18n.LANG_EN)
        english = dialog.monitor_chip.text()
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(dialog.monitor_chip.text(), english)

    @staticmethod
    def _shape(sentence: str) -> str:
        """The sentence with its digits removed, so only the wording is compared."""
        return "".join(character for character in sentence if not character.isdigit())


class HeadingTest(ChooserTestCase):
    """The line over the cards says how many screens the recording has."""

    def test_two_screens_is_spelled_out(self) -> None:
        self.assertNotIn("2", chooser.heading_for(2))

    def test_more_than_two_carries_the_number(self) -> None:
        self.assertIn("3", chooser.heading_for(3))

    def test_the_heading_is_translated(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        english = chooser.heading_for(2)
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(chooser.heading_for(2), english)

    def test_the_plural_reaches_the_counted_heading_in_both_languages(self) -> None:
        """Both sentences take the counted word from `i18n.plural`, not just one.

        The English key used to spell "screens" out and carry no `{word}` hole at
        all, which `str.format` silently accepts: "This recording has 1 screens" for
        the single-screen recording the constructor reaches. Compared as shapes with
        the digits removed, so the rule is checked without this file spelling out a
        word that belongs to the catalogue.
        """
        for code in (i18n.LANG_EN, i18n.LANG_RU):
            i18n.set_language(code)
            self.assertNotIn("{", chooser.heading_for(5), code)
            self.assertIn("5", chooser.heading_for(5), code)
            self.assertNotEqual(
                self._shape(chooser.heading_for(1)),
                self._shape(chooser.heading_for(3)),
                code,
            )

    def test_the_russian_three_forms_reach_the_heading(self) -> None:
        """1 экран / 3 экрана / 5 экранов - the form English has no equivalent of."""
        i18n.set_language(i18n.LANG_RU)
        shapes = {self._shape(chooser.heading_for(n)) for n in (1, 3, 5)}
        self.assertEqual(len(shapes), 3)

    @staticmethod
    def _shape(sentence: str) -> str:
        """The sentence with its digits removed, so only the wording is compared."""
        return "".join(character for character in sentence if not character.isdigit())


# -------------------------------------------------- the displays have changed


class NoticeTest(ChooserTestCase):
    """§2.3: one line above the cards when the saved choice no longer holds."""

    def test_nothing_is_said_when_nothing_was_remembered(self) -> None:
        dialog = self.make(ONE_MONITOR, Stored())
        self.assertEqual(dialog.notice_text(), "")
        self.assertFalse(dialog.notice.isVisible())

    def test_the_displays_changed_line_appears(self) -> None:
        stored = Stored(layouts.LAYOUT_STACKED, "a signature from another desk")
        dialog = self.make(ONE_MONITOR, stored)
        self.assertNotEqual(dialog.notice_text(), "")

    def test_the_line_is_the_one_layout_for_signature_gives(self) -> None:
        """Called, not re-derived: §2.3's decision lives in exactly one place."""
        stored = Stored(layouts.LAYOUT_STACKED, "a signature from another desk")
        _chosen, reason, mode = layouts.layout_for_signature(
            stored.player_layout, stored.player_display_signature,
            ONE_MONITOR, STREAMS,
        )
        self.assertEqual(mode, "ask")
        self.assertEqual(self.make(ONE_MONITOR, stored).notice_text(), reason)

    def test_an_impossible_saved_layout_is_said_too(self) -> None:
        """`one_per_monitor` remembered on a desk that now has one monitor."""
        stored = Stored(layouts.LAYOUT_ONE_PER_MONITOR, layouts.current_signature())
        dialog = self.make(ONE_MONITOR, stored)
        self.assertNotEqual(dialog.notice_text(), "")
        self.assertFalse(dialog.cards[layouts.LAYOUT_ONE_PER_MONITOR].isEnabled())
        self.assertNotEqual(dialog.selected(), layouts.LAYOUT_ONE_PER_MONITOR)

    def test_the_notice_follows_a_language_change(self) -> None:
        stored = Stored(layouts.LAYOUT_STACKED, "a signature from another desk")
        dialog = self.make(ONE_MONITOR, stored)
        i18n.set_language(i18n.LANG_EN)
        english = dialog.notice_text()
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(dialog.notice_text(), english)
        self.assertNotEqual(dialog.notice_text(), "")


# ----------------------------------------------------------------- answering


class AnswerTest(ChooserTestCase):
    """What `choice()` reports, and when it reports nothing at all."""

    def test_there_is_no_choice_until_the_dialog_is_answered(self) -> None:
        self.assertIsNone(self.make().choice())

    def test_open_reports_the_selected_layout(self) -> None:
        dialog = self.make()
        self.assertTrue(dialog.select(layouts.LAYOUT_STACKED))
        dialog.accept()
        self.assertEqual(dialog.choice(), (layouts.LAYOUT_STACKED, False))

    def test_remember_is_reported(self) -> None:
        dialog = self.make()
        dialog.set_remember(True)
        self.assertTrue(dialog.select(layouts.LAYOUT_FOCUS))
        dialog.accept()
        self.assertEqual(dialog.choice(), (layouts.LAYOUT_FOCUS, True))

    def test_the_checkbox_is_what_remember_reads(self) -> None:
        dialog = self.make()
        self.assertFalse(dialog.remember())
        dialog.remember_box.setChecked(True)
        self.assertTrue(dialog.remember())

    def test_cancel_reports_nothing(self) -> None:
        dialog = self.make()
        dialog.select(layouts.LAYOUT_STACKED)
        dialog.reject()
        self.assertIsNone(dialog.choice())

    def test_the_cancel_button_cancels(self) -> None:
        dialog = self.make()
        QTest.mouseClick(dialog.cancel_button, QtCore.Qt.MouseButton.LeftButton)
        self.assertIsNone(dialog.choice())
        self.assertEqual(dialog.result(), int(QtWidgets.QDialog.DialogCode.Rejected))

    def test_escape_cancels(self) -> None:
        dialog = self.make()
        QTest.keyClick(dialog, QtCore.Qt.Key.Key_Escape)
        self.assertIsNone(dialog.choice())

    def test_the_open_button_opens(self) -> None:
        dialog = self.make()
        QTest.mouseClick(dialog.open_button, QtCore.Qt.MouseButton.LeftButton)
        self.assertIsNotNone(dialog.choice())

    def test_open_with_nothing_selectable_is_a_cancel(self) -> None:
        """Rather than a tuple naming a layout nothing on this desk can build."""
        dialog = self.make(ONE_MONITOR, streams=STREAMS[:1])
        dialog.accept()
        self.assertIsNone(dialog.choice())
        self.assertEqual(dialog.result(), int(QtWidgets.QDialog.DialogCode.Rejected))


class KeyboardTest(ChooserTestCase):
    """§7's point, applied to the dialog: it has to work without a mouse."""

    def test_an_arrow_moves_to_the_next_card(self) -> None:
        dialog = self.make(TWO_MONITORS)
        self.assertTrue(dialog.select(layouts.LAYOUT_SIDE_BY_SIDE))
        QTest.keyClick(
            dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE], QtCore.Qt.Key.Key_Right
        )
        self.assertEqual(dialog.selected(), layouts.LAYOUT_STACKED)

    def test_an_arrow_moves_down_a_row(self) -> None:
        dialog = self.make(TWO_MONITORS)
        self.assertTrue(dialog.select(layouts.LAYOUT_SIDE_BY_SIDE))
        QTest.keyClick(
            dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE], QtCore.Qt.Key.Key_Down
        )
        self.assertEqual(dialog.selected(), layouts.LAYOUT_SEPARATE)

    def test_an_arrow_steps_over_a_disabled_card_onto_the_next_one(self) -> None:
        """Keeping the direction, rather than stopping on a card it cannot choose.

        No desk disables a card in the middle of the grid today - `one_per_monitor` is
        last - so one is disabled here on purpose. That is the behaviour the skipping
        is written for, and it would otherwise be reachable only by unplugging a
        monitor that does not exist on this machine.
        """
        dialog = self.make(TWO_MONITORS)
        dialog.cards[layouts.LAYOUT_STACKED].setEnabled(False)
        self.assertTrue(dialog.select(layouts.LAYOUT_SIDE_BY_SIDE))
        QTest.keyClick(
            dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE], QtCore.Qt.Key.Key_Right
        )
        self.assertEqual(dialog.selected(), layouts.LAYOUT_FOCUS)

    def test_an_arrow_past_the_last_usable_card_changes_nothing(self) -> None:
        """`one_per_monitor` is last in the grid, so stopping on it would be a dead
        end the keyboard could not leave; past it there is nothing to move to."""
        dialog = self.make(ONE_MONITOR)
        self.assertTrue(dialog.select(layouts.LAYOUT_SEPARATE))
        QTest.keyClick(dialog.cards[layouts.LAYOUT_SEPARATE], QtCore.Qt.Key.Key_Right)
        self.assertEqual(dialog.selected(), layouts.LAYOUT_SEPARATE)

    def test_an_arrow_at_the_edge_changes_nothing(self) -> None:
        dialog = self.make(TWO_MONITORS)
        self.assertTrue(dialog.select(layouts.LAYOUT_SIDE_BY_SIDE))
        QTest.keyClick(
            dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE], QtCore.Qt.Key.Key_Left
        )
        self.assertEqual(dialog.selected(), layouts.LAYOUT_SIDE_BY_SIDE)

    def test_space_selects_the_focused_card(self) -> None:
        dialog = self.make(TWO_MONITORS)
        card = dialog.cards[layouts.LAYOUT_SEPARATE]
        QTest.keyClick(card, QtCore.Qt.Key.Key_Space)
        self.assertEqual(dialog.selected(), layouts.LAYOUT_SEPARATE)

    def test_enter_on_a_card_opens_with_it(self) -> None:
        dialog = self.make(TWO_MONITORS)
        card = dialog.cards[layouts.LAYOUT_STACKED]
        QTest.keyClick(card, QtCore.Qt.Key.Key_Return)
        self.assertEqual(dialog.choice(), (layouts.LAYOUT_STACKED, False))

    def test_enter_on_a_disabled_card_cannot_open_it(self) -> None:
        dialog = self.make(ONE_MONITOR)
        card = dialog.cards[layouts.LAYOUT_ONE_PER_MONITOR]
        card.activated.emit(card.layout_id)  # the key itself never reaches a disabled
        self.assertIsNone(dialog.choice())   # card; this is the signal doing so anyway

    def test_a_double_click_opens(self) -> None:
        dialog = self.make(TWO_MONITORS)
        QTest.mouseDClick(
            dialog.cards[layouts.LAYOUT_STACKED], QtCore.Qt.MouseButton.LeftButton
        )
        self.assertEqual(dialog.choice(), (layouts.LAYOUT_STACKED, False))

    def test_a_card_can_take_the_keyboard(self) -> None:
        dialog = self.make()
        for card in dialog.cards.values():
            self.assertEqual(
                card.focusPolicy(), QtCore.Qt.FocusPolicy.StrongFocus, card.layout_id
            )


# ------------------------------------------------------------- both languages


class LanguageTest(ChooserTestCase):
    """Every string a person reads goes through `_()`, and follows a live switch."""

    def test_every_visible_string_changes_with_the_language(self) -> None:
        stored = Stored(layouts.LAYOUT_STACKED, "a signature from another desk")
        dialog = self.make(ONE_MONITOR, stored)
        i18n.set_language(i18n.LANG_EN)
        english = self._strings(dialog)
        i18n.set_language(i18n.LANG_RU)
        russian = self._strings(dialog)
        self.assertEqual(set(english), set(russian))
        for name, text in english.items():
            self.assertNotEqual(russian[name], text, name)

    def test_the_reason_line_follows_a_switch(self) -> None:
        """`layouts.recommend`'s docstring forbids caching it; this is that rule."""
        dialog = self.make(ONE_MONITOR)
        for code in (i18n.LANG_EN, i18n.LANG_RU):
            i18n.set_language(code)
            wanted, why = layouts.recommend(ONE_MONITOR, STREAMS)
            self.assertEqual(dialog.cards[wanted].reason_text(), why, code)

    def test_the_disabled_reason_follows_a_switch(self) -> None:
        dialog = self.make(ONE_MONITOR)
        card = dialog.cards[layouts.LAYOUT_ONE_PER_MONITOR]
        i18n.set_language(i18n.LANG_EN)
        english = card.note_text()
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(card.note_text(), english)
        self.assertNotEqual(card.note_text(), "")

    def test_the_screen_names_inside_the_reason_follow_a_switch(self) -> None:
        """The whole sentence turns over, names included - not the frame around them.

        The names were generated by `window.screen_titles` when the caller built the
        `Stream` list, so without re-generating them a switch left the recommended
        card reading "Screen 1 — 50 %": half the one sentence the module's own
        docstring promises a switch can always reach.
        """
        dialog = self.make(ONE_MONITOR, recording=Recording())
        for code in (i18n.LANG_EN, i18n.LANG_RU):
            i18n.set_language(code)
            reason = dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE].reason_text()
            for title in screen_titles(Recording(), 2):
                self.assertIn(title, reason, code)

    def test_the_names_are_left_alone_when_no_recording_was_given(self) -> None:
        """A title the caller invented is the caller's word, not "Screen 2"."""
        theirs = (
            layouts.Stream(index=0, title="Левый", size=(2560, 1440)),
            layouts.Stream(index=1, title="Правый", size=(1440, 2560)),
        )
        dialog = self.make(ONE_MONITOR, streams=theirs)
        i18n.set_language(i18n.LANG_EN)
        reason = dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE].reason_text()
        self.assertIn("Левый", reason)
        self.assertIn("Правый", reason)

    def test_a_recording_s_own_names_survive_a_switch_too(self) -> None:
        """`screen_titles` hands those back untouched; this dialog does not undo it.

        The viewer's own word for a screen is not a string in any catalogue, and a
        switch that replaced it with "Экран 2" would be losing information rather
        than translating it.
        """
        theirs = ["Ноутбук", "Монитор"]
        named = Recording(titles=list(theirs))
        streams = tuple(
            layouts.Stream(index=index, title=title, size=STREAMS[index].size)
            for index, title in enumerate(theirs)
        )
        dialog = self.make(ONE_MONITOR, streams=streams, recording=named)
        for code in (i18n.LANG_EN, i18n.LANG_RU):
            i18n.set_language(code)
            reason = dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE].reason_text()
            for title in theirs:
                self.assertIn(title, reason, code)

    def test_a_different_number_of_names_leaves_the_streams_alone(self) -> None:
        """The guard against losing a stream, which `zip` would do silently.

        `window.screen_titles` answers with exactly as many names as it was asked
        for, so this cannot happen today and is forced here: the point of the check
        is that a future answer of the wrong length costs the dialog a name, not a
        stream, and `zip` on its own would quietly drop the second one.
        """
        original = chooser.screen_titles
        self.addCleanup(setattr, chooser, "screen_titles", original)
        chooser.screen_titles = lambda recording, count: ["Only one"]
        dialog = self.make(ONE_MONITOR, recording=Recording())
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(len(dialog._streams), len(STREAMS))
        reason = dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE].reason_text()
        for stream in STREAMS:
            self.assertIn(stream.title, reason)

    def test_a_card_s_title_follows_a_switch_without_being_told(self) -> None:
        """Nothing textual is cached on a card: the titles are read as they are drawn."""
        card = chooser.LayoutCard(layouts.LAYOUT_STACKED, [(1920, 1080), (1920, 1080)])
        self.addCleanup(card.deleteLater)
        i18n.set_language(i18n.LANG_EN)
        english = card.title_text()
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(card.title_text(), english)

    @staticmethod
    def _strings(dialog: chooser.LayoutChooser) -> dict[str, str]:
        return {
            "heading": dialog.heading.text(),
            "subtitle": dialog.subtitle.text(),
            "chip": dialog.monitor_chip.text(),
            "notice": dialog.notice_text(),
            "remember": dialog.remember_box.text(),
            "hint": dialog.hint.text(),
            "cancel": dialog.cancel_button.text(),
            "open": dialog.open_button.text(),
            "title": dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE].title_text(),
            "badge": dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE].badge_text(),
            "reason": dialog.cards[layouts.LAYOUT_SIDE_BY_SIDE].reason_text(),
            "note": dialog.cards[layouts.LAYOUT_ONE_PER_MONITOR].note_text(),
        }


# ----------------------------------------------------------------- the picture


class ThumbnailTest(ChooserTestCase):
    """The drawn arrangements: shapes from `layouts`, and nothing that raises."""

    def test_two_strips_are_the_contract_s_own_halves(self) -> None:
        """Not a second copy of "half of this area": `tile_rects` is asked."""
        frame = QtCore.QRect(10, 20, 201, 101)
        self.assertEqual(
            chooser._strips(frame, 2, True),
            layouts.tile_rects(frame, layouts.TILING_EQUAL_LR),
        )
        self.assertEqual(
            chooser._strips(frame, 2, False),
            layouts.tile_rects(frame, layouts.TILING_EQUAL_TB),
        )

    def test_strips_fill_the_frame_exactly_and_never_overlap(self) -> None:
        """An odd width must not leave a one-pixel gap between two screens."""
        frame = QtCore.QRect(0, 0, 193, 97)
        for count in (2, 3, 4):
            for across in (True, False):
                strips = chooser._strips(frame, count, across)
                self.assertEqual(len(strips), count)
                covered = QtCore.QRect()
                for strip in strips:
                    self.assertFalse(covered.intersects(strip))
                    covered = covered.united(strip)
                self.assertEqual(covered, frame)

    def test_every_layout_draws_something(self) -> None:
        pixmap = QtGui.QPixmap(200, 120)
        for layout in layouts.LAYOUTS:
            pixmap.fill(QtGui.QColor("black"))
            painter = QtGui.QPainter(pixmap)
            try:
                chooser.paint_arrangement(
                    painter,
                    QtCore.QRect(10, 10, 180, 100),
                    layout,
                    [(2560, 1440), (1440, 2560)],
                    True,
                )
            finally:
                painter.end()
            image = pixmap.toImage()
            painted = any(
                image.pixelColor(x, y) != QtGui.QColor("black")
                for x in range(0, 200, 4)
                for y in range(0, 120, 4)
            )
            self.assertTrue(painted, layout)

    def test_a_frame_too_small_to_label_still_paints(self) -> None:
        """Called on every repaint of a squeezed card; it must not raise there."""
        pixmap = QtGui.QPixmap(12, 8)
        for layout in layouts.LAYOUTS:
            painter = QtGui.QPainter(pixmap)
            try:
                chooser.paint_arrangement(
                    painter, QtCore.QRect(0, 0, 10, 6), layout, [(16, 9)], False
                )
            finally:
                painter.end()

    def test_every_card_paints_in_every_state(self) -> None:
        dialog = self.make(ONE_MONITOR)
        for card in dialog.cards.values():
            for selected in (False, True):
                card.set_selected(selected)
                pixmap = QtGui.QPixmap(card.size())
                pixmap.fill(QtGui.QColor("black"))
                card.render(pixmap)

    def test_the_whole_dialog_renders(self) -> None:
        stored = Stored(layouts.LAYOUT_STACKED, "a signature from another desk")
        dialog = self.make(ONE_MONITOR, stored)
        pixmap = QtGui.QPixmap(dialog.size())
        pixmap.fill(QtGui.QColor("black"))
        dialog.render(pixmap)


# ----------------------------------------------------------------- geometry


class PlacementTest(ChooserTestCase):
    """§6: never between two monitors, and never bigger than the one it is on."""

    def test_it_is_centred_in_the_only_screen(self) -> None:
        screen = QtCore.QRect(0, 0, 1920, 1040)
        spot = chooser.dialog_spot(QtCore.QSize(800, 600), [screen])
        self.assertTrue(screen.contains(spot))
        self.assertEqual(spot.center(), screen.center())

    def test_it_never_lands_between_two_monitors(self) -> None:
        """The failure `window.transport_spot` records: one screen above and to the
        left of the other puts the union's centre where no screen is."""
        screens = [
            QtCore.QRect(0, 0, 1920, 1040),
            QtCore.QRect(2600, 1400, 1920, 1040),
        ]
        spot = chooser.dialog_spot(
            QtCore.QSize(800, 600), screens, QtCore.QRect(2600, 1400, 1920, 1040)
        )
        self.assertTrue(screens[1].contains(spot))

    def test_the_preferred_screen_is_the_one_it_opens_on(self) -> None:
        screens = [
            QtCore.QRect(0, 0, 1920, 1040),
            QtCore.QRect(1920, 0, 1440, 2512),
        ]
        for index, area in enumerate(screens):
            spot = chooser.dialog_spot(QtCore.QSize(700, 500), screens, area)
            self.assertTrue(screens[index].contains(spot), index)

    def test_a_window_bigger_than_the_screen_is_shrunk(self) -> None:
        screen = QtCore.QRect(100, 100, 800, 600)
        spot = chooser.dialog_spot(QtCore.QSize(2000, 2000), [screen])
        self.assertEqual(spot, screen)

    def test_with_no_screens_it_falls_back_to_the_origin(self) -> None:
        size = QtCore.QSize(640, 480)
        self.assertEqual(
            chooser.dialog_spot(size, []), QtCore.QRect(QtCore.QPoint(0, 0), size)
        )

    def test_the_dialog_places_itself_inside_a_screen(self) -> None:
        screens = [
            QtCore.QRect(0, 0, 1920, 1040),
            QtCore.QRect(1920, -300, 1440, 2512),
        ]
        dialog = self.make(ONE_MONITOR)
        dialog.place_on(screens)
        self.assertTrue(
            any(screen.contains(dialog.geometry()) for screen in screens),
            dialog.geometry(),
        )

    def test_it_is_already_placed_before_it_is_shown(self) -> None:
        """A dialog is modal: one that opened off the desk could not even be closed."""
        dialog = self.make(ONE_MONITOR)
        self.assertFalse(dialog.geometry().isEmpty())
        areas = [
            screen.availableGeometry() for screen in QtGui.QGuiApplication.screens()
        ]
        self.assertTrue(any(area.intersects(dialog.geometry()) for area in areas))


# -------------------------------------------------------------- choose()


class ChooseTest(ChooserTestCase):
    """The module function: every answer it can give without showing a window."""

    def test_no_streams_asks_nothing(self) -> None:
        self.assertIsNone(chooser.choose([], ONE_MONITOR, Stored()))

    def test_one_stream_asks_nothing(self) -> None:
        """§1: "A recording with one stream never asks anything.\""""
        self.assertIsNone(chooser.choose(STREAMS[:1], ONE_MONITOR, Stored()))

    def test_no_areas_does_not_raise(self) -> None:
        answer = chooser.choose(STREAMS, [], Stored())
        self.assertIsNotNone(answer)
        self.assertIn(answer[0], layouts.LAYOUTS)
        self.assertFalse(answer[1])

    def test_no_streams_and_no_areas_does_not_raise(self) -> None:
        self.assertIsNone(chooser.choose([], [], Stored()))

    def test_it_works_without_any_settings_at_all(self) -> None:
        answer = chooser.choose(STREAMS, ONE_MONITOR, None)
        self.assertIsNotNone(answer)

    def test_a_saved_choice_that_still_applies_is_not_asked_about(self) -> None:
        """§2.3: signature matches, so playback starts straight away."""
        stored = Stored(layouts.LAYOUT_STACKED, layouts.current_signature())
        self.assertEqual(
            chooser.choose(STREAMS, ONE_MONITOR, stored),
            (layouts.LAYOUT_STACKED, True),
        )

    def test_a_saved_choice_from_another_desk_is_not_applied(self) -> None:
        """It would have opened `one_per_monitor` on a monitor that is not there."""
        stored = Stored(layouts.LAYOUT_ONE_PER_MONITOR, layouts.current_signature())
        answer = chooser.choose(STREAMS, ONE_MONITOR, stored)
        self.assertIsNotNone(answer)
        self.assertNotEqual(answer[0], layouts.LAYOUT_ONE_PER_MONITOR)

    def test_nobody_to_answer_takes_the_recommendation_and_does_not_remember(
        self,
    ) -> None:
        """Under the offscreen platform `exec()` would wait for a click that cannot
        come. The recording still opens; the choice is not stored as if made."""
        wanted, _why = layouts.recommend(ONE_MONITOR, STREAMS)
        self.assertEqual(chooser.choose(STREAMS, ONE_MONITOR, Stored()), (wanted, False))

    def test_this_platform_really_is_one_of_the_headless_ones(self) -> None:
        """Otherwise the test above proves nothing and the suite would hang on
        `exec()` instead of passing."""
        self.assertIn(
            QtGui.QGuiApplication.platformName(), chooser.HEADLESS_PLATFORMS
        )


# --------------------------------------------------------------- the clock


class EngineDistanceTest(unittest.TestCase):
    """§6: "none of this may create a second clock."

    The chooser is shown before playback starts, so the cheapest way to keep that
    promise is for it not to know the engine exists. This reads the source rather than
    trusting the claim: an import added later fails here, which is the moment to ask
    whether the dialog has started doing something playback-shaped.
    """

    def test_the_chooser_imports_no_player_and_no_engine(self) -> None:
        source = Path(chooser.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        for name in imported:
            self.assertNotIn("engine", name)
            self.assertNotIn("Multimedia", name)

    def test_the_module_holds_no_engine_of_its_own(self) -> None:
        self.assertFalse(hasattr(chooser, "engine"))
        self.assertFalse(hasattr(chooser, "QtMultimedia"))


if __name__ == "__main__":
    unittest.main()
