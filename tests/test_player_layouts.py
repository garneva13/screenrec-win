"""Checks for `screenrec.player.layouts`.

This module is the one place in the player where a wrong number is invisible: a layout
that scores badly is not a crash, it is a viewer squinting at a half-size picture and
not knowing there was a better choice. So the formulas of `docs/PLAYER_LAYOUTS.md`
§2.2 are pinned here with arithmetic done by hand, not with "assert it is above zero".

Five of these tests exist because of a specific failure they would catch:

* the display signature changing when Windows merely renumbers the screens - which
  would show the layout dialog to someone whose desk has not moved;
* the geometric mean quietly becoming an average - proved with two numbers whose
  *averages* are identical and whose geometric means are not;
* `tile_rects` leaving a one-pixel strip of desktop between two borderless windows on
  an odd-width monitor;
* a window ending up partly off the screen it was moved to, where its close box cannot
  be reached;
* `one_per_monitor` scored on the sidecar's order instead of on the best stream ->
  monitor arrangement, which recommends `focus` - half the recording hidden - over a
  layout that would have shown both screens at 1:1.

The user's real desk - a 2560x1440 at 0,0 with a rotated 1440x2560 at 2560,0 - is used
wherever a concrete configuration is needed, so the numbers in the module's docstring
and the numbers here are about the same machine. `DESK_STREAMS` is listed in the same
order as `DESK`; `DESK_STREAMS_SWAPPED` is the same recording with the sidecar naming
the portrait screen first, which is just as legal and which nothing in the screen list
lines up with.

A `QApplication` exists because `current_signature` reads `QGuiApplication.screens()`;
the offscreen platform plugin is set before PySide6 is imported, so nothing is shown.
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import i18n  # noqa: E402
from screenrec.player import layouts  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


@contextmanager
def _in(code: str) -> Iterator[None]:
    """Run the block with `code` in force, and put the language back afterwards.

    The language is process-wide, so a test that left it in Russian would fail the
    next file's English assertions and the blame would land in the wrong module.
    `LanguageSafeTest` is the belt to this one's braces.
    """
    before = i18n.language()
    i18n.set_language(code)
    try:
        yield
    finally:
        i18n.set_language(before)


class LanguageSafeTest(unittest.TestCase):
    """English in force before each test, whatever ran before it left behind."""

    def setUp(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        self.addCleanup(i18n.set_language, i18n.LANG_EN)


def _rect(x: int, y: int, width: int, height: int) -> QtCore.QRect:
    return QtCore.QRect(x, y, width, height)


#: The user's desk: a landscape screen with a portrait one standing to its right.
DESK: list[QtCore.QRect] = [_rect(0, 0, 2560, 1440), _rect(2560, 0, 1440, 2560)]
DESK_SIGNATURE = "2560x1440+0+0@1.0|1440x2560+2560+0@1.0"

#: The two streams that desk records, at their native sizes, in the same order as DESK.
DESK_STREAMS: list[layouts.Stream] = [
    layouts.Stream(0, "Экран 1", (2560, 1440)),
    layouts.Stream(1, "Экран 2", (1440, 2560)),
]

#: The same recording with the sidecar naming the portrait screen first. Nothing in the
#: screen list aligns with the sidecar's order, so this is as likely as the one above.
DESK_STREAMS_SWAPPED: list[layouts.Stream] = [
    layouts.Stream(0, "Экран 1", (1440, 2560)),
    layouts.Stream(1, "Экран 2", (2560, 1440)),
]


class SignatureTests(unittest.TestCase):
    """§2.3: what must change the stamp, and the one thing that must not."""

    def test_the_desk_reads_as_the_contract_writes_it(self) -> None:
        self.assertEqual(
            layouts.display_signature(DESK, [1.0, 1.0]), DESK_SIGNATURE
        )

    def test_a_reordered_screen_list_gives_the_same_signature(self) -> None:
        # Windows renumbers screens when one is woken or re-plugged. The desk did not
        # move, so the remembered layout must still apply without a dialog.
        swapped = layouts.display_signature(
            [DESK[1], DESK[0]], [1.0, 1.0]
        )
        self.assertEqual(swapped, DESK_SIGNATURE)

    def test_a_moved_screen_changes_the_signature(self) -> None:
        moved = [DESK[0], _rect(2560, 100, 1440, 2560)]
        self.assertNotEqual(layouts.display_signature(moved, [1.0, 1.0]),
                            DESK_SIGNATURE)

    def test_a_removed_screen_changes_the_signature(self) -> None:
        self.assertNotEqual(layouts.display_signature([DESK[0]], [1.0]),
                            DESK_SIGNATURE)

    def test_a_rescaled_screen_changes_the_signature(self) -> None:
        rescaled = layouts.display_signature(DESK, [1.25, 1.0])
        self.assertNotEqual(rescaled, DESK_SIGNATURE)
        self.assertIn("@1.25", rescaled)

    def test_a_ratio_list_of_the_wrong_length_is_refused_in_words(self) -> None:
        with self.assertRaises(ValueError) as caught:
            layouts.display_signature(DESK, [1.0])
        self.assertIn("same screens", str(caught.exception))

    def test_current_signature_matches_the_live_screens(self) -> None:
        _app()
        screens = QtGui.QGuiApplication.screens()
        expected = layouts.display_signature(
            [screen.availableGeometry() for screen in screens],
            [screen.devicePixelRatio() for screen in screens],
        )
        self.assertEqual(layouts.current_signature(), expected)
        self.assertTrue(layouts.current_signature())


class FitTests(unittest.TestCase):
    """Letterboxing: the shape is kept, the picture is never blown up."""

    def test_a_small_stream_is_not_upscaled(self) -> None:
        box = _rect(0, 0, 1000, 1000)
        drawn = layouts.fit(box, (100, 50))
        self.assertEqual(drawn.size(), QtCore.QSize(100, 50))
        self.assertEqual(drawn.topLeft(), QtCore.QPoint(450, 475))
        self.assertEqual(layouts.scale_in(box, (100, 50)), 1.0)

    def test_a_big_stream_is_scaled_down_and_centred(self) -> None:
        drawn = layouts.fit(_rect(10, 20, 100, 100), (1000, 500))
        self.assertEqual(drawn, _rect(10, 45, 100, 50))

    def test_the_result_never_leaves_the_box(self) -> None:
        box = _rect(0, 0, 1001, 701)
        for size in ((2560, 1440), (1440, 2560), (1001, 701), (3, 7000)):
            with self.subTest(size=size):
                self.assertTrue(box.contains(layouts.fit(box, size)))

    def test_a_degenerate_size_does_not_divide_by_zero(self) -> None:
        box = _rect(5, 6, 100, 100)
        for size in ((0, 0), (0, 720), (1280, 0), (-4, 9)):
            with self.subTest(size=size):
                self.assertEqual(layouts.fit(box, size), _rect(5, 6, 0, 0))
                self.assertEqual(layouts.scale_in(box, size), 0.0)

    def test_a_degenerate_box_does_not_divide_by_zero(self) -> None:
        self.assertEqual(layouts.fit(_rect(0, 0, 0, 0), (16, 9)), _rect(0, 0, 0, 0))
        self.assertEqual(layouts.scale_in(_rect(0, 0, 100, 0), (16, 9)), 0.0)


class ScoreTests(unittest.TestCase):
    """§2.2's formulas, with the arithmetic done by hand."""

    def test_the_geometric_mean_punishes_a_lopsided_layout(self) -> None:
        """0.8 and 0.2 average the same as 0.5 and 0.5, and must not score the same.

        On a 1000x1000 area `side_by_side` gives each stream a 500x1000 box, so a
        625x1250 stream is shown at 0.8 and a 2500x5000 one at 0.2, while two
        1000x2000 streams are shown at 0.5 each. Both pairs average 0.5. The
        geometric means are 0.400 and 0.500 - those are the two numbers this feature
        rests on, and an arithmetic mean would make the first layout look as good as
        the second.
        """
        area = [_rect(0, 0, 1000, 1000)]
        lopsided = [
            layouts.Stream(0, "Экран 1", (625, 1250)),
            layouts.Stream(1, "Экран 2", (2500, 5000)),
        ]
        even = [
            layouts.Stream(0, "Экран 1", (1000, 2000)),
            layouts.Stream(1, "Экран 2", (1000, 2000)),
        ]
        self.assertAlmostEqual((0.8 + 0.2) / 2, (0.5 + 0.5) / 2)  # the averages tie
        self.assertAlmostEqual(
            layouts.score(layouts.LAYOUT_SIDE_BY_SIDE, area, lopsided), 0.4
        )
        self.assertAlmostEqual(
            layouts.score(layouts.LAYOUT_SIDE_BY_SIDE, area, even), 0.5
        )

    def test_the_desk_scores_as_the_module_says_it_does(self) -> None:
        scores = {
            layout: layouts.score(layout, DESK, DESK_STREAMS)
            for layout in layouts.LAYOUTS
        }
        self.assertAlmostEqual(scores[layouts.LAYOUT_ONE_PER_MONITOR], 1.0)
        self.assertAlmostEqual(
            scores[layouts.LAYOUT_SIDE_BY_SIDE], (0.5 * 0.5625) ** 0.5
        )
        self.assertAlmostEqual(scores[layouts.LAYOUT_STACKED], (0.5 * 0.28125) ** 0.5)
        self.assertEqual(scores[layouts.LAYOUT_SEPARATE], 0.0)
        self.assertEqual(scores[layouts.LAYOUT_FOCUS], 0.0)

    def test_the_two_that_are_offered_and_never_recommended(self) -> None:
        """`separate` and `focus` are drawn on a card and can never win.

        `focus` is the correction: it used to score `scale(main) * 0.9`, which on one
        2560x1440 came to 0.900 and beat every layout showing both streams - so the
        advice on the commonest desk there is was to watch half of the recording. It
        has outlived picture in picture, which it used to be measured against, and
        the number is unchanged. Both are still `possible`,
        both still have a title, a description and a reason line, and neither is ever
        what `recommend` returns.
        """
        self.assertEqual(
            layouts.NEVER_RECOMMENDED,
            frozenset({layouts.LAYOUT_SEPARATE, layouts.LAYOUT_FOCUS}),
        )
        for layout in layouts.NEVER_RECOMMENDED:
            with self.subTest(layout=layout):
                self.assertTrue(layouts.possible(layout, DESK, DESK_STREAMS))
                self.assertEqual(layouts.score(layout, DESK, DESK_STREAMS), 0.0)
                self.assertEqual(
                    layouts.score(layout, [DESK[0]], DESK_STREAMS), 0.0
                )
                self.assertTrue(layouts.title(layout))
                self.assertTrue(layouts.description(layout))

    def test_one_per_monitor_needs_a_monitor_per_stream(self) -> None:
        one = [DESK[0]]
        self.assertFalse(
            layouts.possible(layouts.LAYOUT_ONE_PER_MONITOR, one, DESK_STREAMS)
        )
        self.assertEqual(
            layouts.score(layouts.LAYOUT_ONE_PER_MONITOR, one, DESK_STREAMS), 0.0
        )
        self.assertTrue(
            layouts.possible(layouts.LAYOUT_ONE_PER_MONITOR, DESK, DESK_STREAMS)
        )

    def test_an_unknown_layout_id_is_refused_rather_than_scored_zero(self) -> None:
        with self.assertRaises(ValueError):
            layouts.score("tiled", DESK, DESK_STREAMS)

    def test_nothing_is_possible_without_an_area_or_two_streams(self) -> None:
        for layout in layouts.LAYOUTS:
            with self.subTest(layout=layout):
                self.assertFalse(layouts.possible(layout, [], DESK_STREAMS))
                self.assertFalse(layouts.possible(layout, DESK, DESK_STREAMS[:1]))


class PairingTests(LanguageSafeTest):
    """§5: which stream goes on which monitor is searched, never assumed.

    `areas` comes from the screen list and `streams` from the sidecar. Pairing stream
    *i* with area *i* is a guess, and these are the two cases where the guess is not a
    rounding error: one where it misreports the size, and one where it changes the
    recommendation to a layout that hides half the recording.
    """

    def test_a_sidecar_in_the_other_order_is_still_paired_correctly(self) -> None:
        self.assertEqual(layouts.best_pairing(DESK, DESK_STREAMS_SWAPPED), (1, 0))
        self.assertAlmostEqual(
            layouts.score(
                layouts.LAYOUT_ONE_PER_MONITOR, DESK, DESK_STREAMS_SWAPPED
            ),
            1.0,
        )
        layout, reason = layouts.recommend(DESK, DESK_STREAMS_SWAPPED)
        self.assertEqual(layout, layouts.LAYOUT_ONE_PER_MONITOR)
        self.assertEqual(reason, "each screen on its own monitor, at natural size")
        with _in(i18n.LANG_RU):
            _layout, russian = layouts.recommend(DESK, DESK_STREAMS_SWAPPED)
        self.assertEqual(
            russian, "каждый экран на своём мониторе, в натуральную величину"
        )

    def test_an_aligned_sidecar_is_left_in_its_own_order(self) -> None:
        # Ties keep the arrangement found first, and the identity is tried first, so a
        # sidecar that already matched the screens is not shuffled for nothing.
        self.assertEqual(layouts.best_pairing(DESK, DESK_STREAMS), (0, 1))

    def test_the_identity_mapping_would_have_lost_both_monitors(self) -> None:
        """The reviewed case: pairing by index throws a whole monitor away.

        A 1440x810 stream and a 2560x1440 one on this desk. Paired by index the
        portrait monitor is given the landscape stream and vice versa, which scores
        0.75 where the other pairing scores 1.0 - so a window on each of the two
        monitors standing on the desk would be advertised at three quarters of natural
        size for no reason but the order the sidecar happened to list the streams in.

        The mapping used to cost more than a number: `focus` scored 0.9 then, so the
        mis-paired `one_per_monitor` lost outright and the dialog recommended hiding
        half the recording. `focus` scores 0.0 now, so what is left is the size.
        """
        streams = [
            layouts.Stream(0, "Экран 1", (1440, 810)),
            layouts.Stream(1, "Экран 2", (2560, 1440)),
        ]
        by_index = layouts.score(
            layouts.LAYOUT_ONE_PER_MONITOR, DESK, streams, (0, 1)
        )
        self.assertAlmostEqual(by_index, 0.75)

        self.assertEqual(layouts.best_pairing(DESK, streams), (1, 0))
        self.assertAlmostEqual(
            layouts.score(layouts.LAYOUT_ONE_PER_MONITOR, DESK, streams), 1.0
        )
        layout, _reason = layouts.recommend(DESK, streams)
        self.assertEqual(layout, layouts.LAYOUT_ONE_PER_MONITOR)

    def test_the_users_own_pairing_is_scored_and_described(self) -> None:
        # §5 lets the user change the mapping. Once they have, the card must quote the
        # size their windows will really have, not the best one available.
        self.assertAlmostEqual(
            layouts.score(
                layouts.LAYOUT_ONE_PER_MONITOR, DESK, DESK_STREAMS_SWAPPED, (0, 1)
            ),
            0.5625,
        )
        _layout, reason = layouts.recommend(DESK, DESK_STREAMS_SWAPPED, (0, 1))
        self.assertEqual(
            reason, "each screen on its own monitor, 56% of natural size each"
        )
        with _in(i18n.LANG_RU):
            _again, russian = layouts.recommend(DESK, DESK_STREAMS_SWAPPED, (0, 1))
        self.assertEqual(
            russian, "каждый экран на своём мониторе, по 56 % от натуральной величины"
        )

    def test_a_pairing_that_cannot_be_placed_is_refused_in_words(self) -> None:
        for bad, expected in (
            ((0,), "one entry per stream"),
            ((0, 5), "no screen is"),
            ((1, 1), "two screens on one monitor"),
        ):
            with self.subTest(pairing=bad):
                with self.assertRaises(ValueError) as caught:
                    layouts.score(
                        layouts.LAYOUT_ONE_PER_MONITOR, DESK, DESK_STREAMS, bad
                    )
                self.assertIn(expected, str(caught.exception))

    def test_pairing_without_a_monitor_each_says_so(self) -> None:
        with self.assertRaises(ValueError) as caught:
            layouts.best_pairing([DESK[0]], DESK_STREAMS)
        self.assertIn("needs 2 monitors", str(caught.exception))
        # The layout is impossible, so `score` answers 0.0 instead of raising: the
        # dialog scores every card before it knows which ones it will draw.
        self.assertEqual(
            layouts.score(layouts.LAYOUT_ONE_PER_MONITOR, [DESK[0]], DESK_STREAMS),
            0.0,
        )

    def test_more_streams_than_the_search_allows_still_pairs_one_each(self) -> None:
        # 8! = 40320 arrangements, past the bound, so the greedy fallback answers. No
        # desk has eight recorded screens; what is pinned is that the player neither
        # stalls nor puts two of them on one monitor.
        count = 8
        self.assertGreater(math.perm(count, count), layouts._MAX_PAIRINGS)
        areas = [_rect(i * 1000, 0, 800 + i * 50, 600) for i in range(count)]
        streams = [
            layouts.Stream(i, f"Экран {i + 1}", (400 + i * 60, 300))
            for i in range(count)
        ]
        order = layouts.best_pairing(areas, streams)
        self.assertEqual(sorted(order), list(range(count)))


class RecommendTests(LanguageSafeTest):
    """The choice, and the line under the card that explains it."""

    def test_the_desk_with_both_monitors_gets_one_per_monitor(self) -> None:
        layout, reason = layouts.recommend(DESK, DESK_STREAMS)
        self.assertEqual(layout, layouts.LAYOUT_ONE_PER_MONITOR)
        # Both streams are on their own monitor at exactly 1.0, so the line says so:
        # "почти" here would describe the one case the arithmetic is sure of as an
        # approximation, and argue a viewer out of the best layout there is.
        self.assertEqual(reason, "each screen on its own monitor, at natural size")
        with _in(i18n.LANG_RU):
            _layout, russian = layouts.recommend(DESK, DESK_STREAMS)
        self.assertEqual(
            russian, "каждый экран на своём мониторе, в натуральную величину"
        )

    def test_almost_natural_size_is_still_said_when_it_is_almost(self) -> None:
        # A desk 5 % smaller than the recording in both directions: 0.95 each, which
        # is near enough to say "почти" and not near enough to claim 1:1.
        areas = [_rect(0, 0, 2432, 1368), _rect(2432, 0, 1368, 2432)]
        _layout, reason = layouts.recommend(areas, DESK_STREAMS)
        self.assertEqual(
            reason, "each screen on its own monitor, almost at natural size"
        )
        with _in(i18n.LANG_RU):
            _again, russian = layouts.recommend(areas, DESK_STREAMS)
        self.assertEqual(
            russian, "каждый экран на своём мониторе, почти в натуральную величину"
        )

    def test_one_monitor_gets_side_by_side_and_never_focus(self) -> None:
        """What a single 2560x1440 is advised, pinned by what `recommend` answers.

        That desk is the one the whole feature was designed for. `focus` used to score
        0.900 and win it: the answer to "how do I watch what I just recorded" was
        "watch one of the two screens". Picture in picture answered it next, at 0.870,
        until the viewer found its splitter-less thumbnail earned nothing `side_by_side`
        did not already give. Both are gone and the advice is `side_by_side` at 0.530 -
        half the width each, which is what one monitor can do with a 2560-wide
        recording and a portrait one when both have to stay visible.
        """
        one = [DESK[0]]
        self.assertEqual(layouts.score(layouts.LAYOUT_FOCUS, one, DESK_STREAMS), 0.0)
        self.assertAlmostEqual(
            layouts.score(layouts.LAYOUT_SIDE_BY_SIDE, one, DESK_STREAMS),
            (0.5 * 0.5625) ** 0.5,
        )
        layout, reason = layouts.recommend(one, DESK_STREAMS)
        self.assertEqual(layout, layouts.LAYOUT_SIDE_BY_SIDE)
        self.assertEqual(
            reason,
            "both screens in full: Экран 1 — 50%, Экран 2 — 56% of natural size",
        )
        with _in(i18n.LANG_RU):
            _again, russian = layouts.recommend(one, DESK_STREAMS)
        self.assertEqual(
            russian,
            "оба экрана целиком: Экран 1 — 50 %, Экран 2 — 56 % "
            "от натуральной величины",
        )

    def test_both_monitors_still_win_on_the_real_desk(self) -> None:
        # The other half of the correction: taking `focus` out of the running must not
        # have disturbed the two-monitor answer, which was already right.
        layout, _reason = layouts.recommend(DESK, DESK_STREAMS)
        self.assertEqual(layout, layouts.LAYOUT_ONE_PER_MONITOR)
        self.assertAlmostEqual(
            layouts.score(layouts.LAYOUT_ONE_PER_MONITOR, DESK, DESK_STREAMS), 1.0
        )

    def test_no_desk_is_ever_told_to_hide_half_the_recording(self) -> None:
        # Not only the two desks above: over a spread of monitors and recordings, the
        # answer is never one of the two that show less than everything.
        monitors = (
            _rect(0, 0, 1366, 768),
            _rect(0, 0, 1920, 1080),
            _rect(0, 0, 2560, 1440),
            _rect(0, 0, 3840, 2160),
            _rect(0, 0, 1440, 2560),
        )
        sizes = ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160), (1440, 2560))
        for area in monitors:
            for first in sizes:
                for second in sizes:
                    streams = [
                        layouts.Stream(0, "Экран 1", first),
                        layouts.Stream(1, "Экран 2", second),
                    ]
                    layout, _reason = layouts.recommend([area], streams)
                    self.assertNotIn(layout, layouts.NEVER_RECOMMENDED)

    def test_a_tie_goes_to_side_by_side_and_says_the_percentage(self) -> None:
        # Two square streams on a square screen: halving the width and halving the
        # height cost exactly the same, so `side_by_side` and `stacked` score alike
        # and the tie-break is the only thing that can decide between them.
        area = [_rect(0, 0, 1000, 1000)]
        streams = [
            layouts.Stream(0, "Экран 1", (2000, 2000)),
            layouts.Stream(1, "Экран 2", (2000, 2000)),
        ]
        self.assertAlmostEqual(
            layouts.score(layouts.LAYOUT_SIDE_BY_SIDE, area, streams), 0.25
        )
        self.assertAlmostEqual(
            layouts.score(layouts.LAYOUT_STACKED, area, streams), 0.25
        )
        layout, reason = layouts.recommend(area, streams)
        self.assertEqual(layout, layouts.LAYOUT_SIDE_BY_SIDE)
        self.assertEqual(reason, "both screens in full, 25% of natural size each")
        with _in(i18n.LANG_RU):
            _again, russian = layouts.recommend(area, streams)
        self.assertEqual(
            russian, "оба экрана целиком, по 25 % от натуральной величины"
        )

    def test_with_nothing_to_measure_it_says_so(self) -> None:
        for areas, streams in (([], DESK_STREAMS), (DESK, []), ([], [])):
            with self.subTest(areas=len(areas), streams=len(streams)):
                layout, reason = layouts.recommend(areas, streams)
                self.assertEqual(layout, layouts.LAYOUT_SIDE_BY_SIDE)
                self.assertIn("not known yet", reason)
                with _in(i18n.LANG_RU):
                    _again, russian = layouts.recommend(areas, streams)
                self.assertIn("не известны", russian)

    def test_one_stream_gets_the_default_without_inventing_a_size(self) -> None:
        """A one-stream recording never reaches the dialog (§1) - but `recommend` is
        a public function, and a caller that asks it anyway must not be handed a
        percentage for a layout that cannot be built. Every layout is impossible with
        one stream, so the best score is 0.0 and the answer is the default plus the
        fallback line - not `side_by_side` quoting a pane the stage would never draw.

        Pinned because this is the branch the picture-in-picture removal was
        re-checked against, and the only fallback nothing held: the other one
        (`test_with_nothing_to_measure_it_says_so`) is reached with no areas at all,
        this one with real areas that no layout can use.
        """
        layout, reason = layouts.recommend(DESK, DESK_STREAMS[:1])
        self.assertEqual(layout, layouts.LAYOUT_SIDE_BY_SIDE)
        self.assertEqual(reason, "no layout fitted these screens — the default layout")
        with _in(i18n.LANG_RU):
            _again, russian = layouts.recommend(DESK, DESK_STREAMS[:1])
        self.assertEqual(
            russian, "ни одна раскладка не подошла по размеру — раскладка по умолчанию"
        )

    def test_every_layout_has_a_title_and_a_description(self) -> None:
        self.assertEqual(set(layouts.TITLES), set(layouts.LAYOUTS))
        self.assertEqual(set(layouts.DESCRIPTIONS), set(layouts.LAYOUTS))
        self.assertEqual(set(layouts.TILING_TITLES), set(layouts.TILINGS))
        self.assertEqual(
            layouts.STAGE_LAYOUTS | layouts.WINDOW_LAYOUTS, set(layouts.LAYOUTS)
        )
        self.assertFalse(layouts.STAGE_LAYOUTS & layouts.WINDOW_LAYOUTS)


class LanguageTests(LanguageSafeTest):
    """Every word this module hands the dialog, in both languages.

    The accessors are what the dialog must call. `TITLES` and its two neighbours hold
    the *English source text*, which is also the catalogue key, so a card filled by
    indexing the dict would sit in English inside a Russian window - and nothing on
    screen would say which of the two was the mistake. These tests pin the accessors
    and, separately, pin that every value in the three dicts has a Russian entry: a
    seventh layout added with no translation would otherwise reach a viewer as English.
    """

    def test_every_title_and_description_resolves_in_both_languages(self) -> None:
        for layout in layouts.LAYOUTS:
            with self.subTest(layout=layout):
                with _in(i18n.LANG_EN):
                    english = (layouts.title(layout), layouts.description(layout))
                with _in(i18n.LANG_RU):
                    russian = (layouts.title(layout), layouts.description(layout))
                self.assertEqual(english[0], layouts.TITLES[layout])
                self.assertEqual(english[1], layouts.DESCRIPTIONS[layout])
                for word in russian:
                    self.assertTrue(word.strip())
                self.assertNotEqual(english, russian)

    def test_every_tiling_title_resolves_in_both_languages(self) -> None:
        for tiling in layouts.TILINGS:
            with self.subTest(tiling=tiling):
                with _in(i18n.LANG_EN):
                    english = layouts.tiling_title(tiling)
                with _in(i18n.LANG_RU):
                    russian = layouts.tiling_title(tiling)
                self.assertEqual(english, layouts.TILING_TITLES[tiling])
                self.assertNotEqual(english, russian)

    def test_the_three_dicts_are_entirely_in_the_catalogue(self) -> None:
        sources = (
            list(layouts.TITLES.values())
            + list(layouts.DESCRIPTIONS.values())
            + list(layouts.TILING_TITLES.values())
        )
        self.assertEqual(i18n.missing_translations(sources), [])

    def test_an_unknown_id_is_refused_rather_than_shown_as_itself(self) -> None:
        for call in (layouts.title, layouts.description, layouts.tiling_title):
            with self.subTest(call=call.__name__):
                with self.assertRaises(ValueError):
                    call("tiled")

    def test_a_reason_line_is_not_the_same_sentence_in_both(self) -> None:
        # The reason is built word by word at the moment it is asked for, so it is the
        # piece most likely to be left half-translated; and `_percent` differs too,
        # because Russian puts a space before the sign and English does not.
        with _in(i18n.LANG_EN):
            _layout, english = layouts.recommend([DESK[0]], DESK_STREAMS)
        with _in(i18n.LANG_RU):
            _again, russian = layouts.recommend([DESK[0]], DESK_STREAMS)
        self.assertNotEqual(english, russian)
        self.assertIn("50%", english)
        self.assertIn("50 %", russian)

    def test_a_reason_follows_a_switch_made_after_the_first_call(self) -> None:
        # Nothing caches a reason line: the same call with the language changed under
        # it must answer in the new language, which is what makes a live switch work
        # for the dialog that calls `recommend` while it is building itself.
        i18n.set_language(i18n.LANG_EN)
        first = layouts.recommend(DESK, DESK_STREAMS)[1]
        i18n.set_language(i18n.LANG_RU)
        second = layouts.recommend(DESK, DESK_STREAMS)[1]
        self.assertNotEqual(first, second)
        self.assertIn("мониторе", second)


class TileTests(unittest.TestCase):
    """§5: two windows filling one monitor with no strip of desktop between them."""

    def test_every_scheme_covers_an_odd_area_exactly(self) -> None:
        area = _rect(7, 11, 1001, 701)  # odd in both directions, and not at the origin
        for tiling in layouts.TILINGS:
            with self.subTest(tiling=tiling):
                first, second = layouts.tile_rects(area, tiling)
                self.assertTrue(first.intersected(second).isEmpty())
                self.assertEqual(
                    first.width() * first.height() + second.width() * second.height(),
                    area.width() * area.height(),
                )
                self.assertEqual(first.united(second), area)
                self.assertTrue(area.contains(first))
                self.assertTrue(area.contains(second))

    def test_the_big_half_is_on_the_side_the_name_says(self) -> None:
        area = _rect(0, 0, 900, 600)
        left, right = layouts.tile_rects(area, layouts.TILING_BIG_LEFT)
        self.assertEqual((left.width(), right.width()), (600, 300))
        left, right = layouts.tile_rects(area, layouts.TILING_BIG_RIGHT)
        self.assertEqual((left.width(), right.width()), (300, 600))
        top, bottom = layouts.tile_rects(area, layouts.TILING_EQUAL_TB)
        self.assertEqual((top.height(), bottom.height()), (300, 300))

    def test_an_unknown_scheme_is_refused_in_words(self) -> None:
        with self.assertRaises(ValueError) as caught:
            layouts.tile_rects(_rect(0, 0, 100, 100), "quarters")
        self.assertIn("equal_lr", str(caught.exception))


class ClampTests(unittest.TestCase):
    def test_a_rect_bigger_than_the_area_is_shrunk_not_merely_moved(self) -> None:
        area = _rect(100, 100, 800, 600)
        self.assertEqual(layouts.clamp_into(_rect(0, 0, 3000, 2000), area), area)

    def test_a_rect_already_inside_is_left_alone(self) -> None:
        area = _rect(100, 100, 800, 600)
        inside = _rect(150, 150, 200, 200)
        self.assertEqual(layouts.clamp_into(inside, area), inside)

    def test_a_rect_hanging_off_an_edge_is_moved_whole(self) -> None:
        area = _rect(0, 0, 1000, 1000)
        moved = layouts.clamp_into(_rect(900, -50, 200, 200), area)
        self.assertEqual(moved, _rect(800, 0, 200, 200))


class RememberTests(LanguageSafeTest):
    """§2.3's decision: apply the saved choice, or show the dialog and say why."""

    def setUp(self) -> None:
        super().setUp()
        _app()
        self.live = layouts.current_signature()

    def test_a_matching_signature_applies_silently(self) -> None:
        layout, reason, ask = layouts.layout_for_signature(
            layouts.LAYOUT_STACKED, self.live, DESK, DESK_STREAMS
        )
        self.assertEqual(layout, layouts.LAYOUT_STACKED)
        self.assertEqual(ask, "")
        self.assertIn("Stacked", reason)
        with _in(i18n.LANG_RU):
            _layout, russian, _ask = layouts.layout_for_signature(
                layouts.LAYOUT_STACKED, self.live, DESK, DESK_STREAMS
            )
        self.assertIn("Друг над другом", russian)
        self.assertIn("Сохранённый выбор", russian)

    def test_a_different_signature_asks_and_says_the_displays_changed(self) -> None:
        layout, reason, ask = layouts.layout_for_signature(
            layouts.LAYOUT_STACKED, "1280x720+0+0@1.0", DESK, DESK_STREAMS
        )
        self.assertEqual(ask, "ask")
        self.assertEqual(layout, layouts.LAYOUT_ONE_PER_MONITOR)
        self.assertIn("The displays have changed", reason)
        with _in(i18n.LANG_RU):
            _layout, russian, _ask = layouts.layout_for_signature(
                layouts.LAYOUT_STACKED, "1280x720+0+0@1.0", DESK, DESK_STREAMS
            )
        self.assertIn("Мониторы изменились", russian)

    def test_a_layout_that_is_no_longer_possible_asks_and_names_itself(self) -> None:
        layout, reason, ask = layouts.layout_for_signature(
            layouts.LAYOUT_ONE_PER_MONITOR, self.live, [DESK[0]], DESK_STREAMS
        )
        self.assertEqual(ask, "ask")
        self.assertNotEqual(layout, layouts.LAYOUT_ONE_PER_MONITOR)
        self.assertIn("One screen per monitor", reason)
        self.assertIn("does not fit now", reason)
        with _in(i18n.LANG_RU):
            _layout, russian, _ask = layouts.layout_for_signature(
                layouts.LAYOUT_ONE_PER_MONITOR, self.live, [DESK[0]], DESK_STREAMS
            )
        self.assertIn("По одному экрану на монитор", russian)
        self.assertIn("не подходит", russian)

    def test_nothing_saved_asks_with_the_recommendation(self) -> None:
        for saved in ("", "tiled"):
            with self.subTest(saved=saved):
                layout, reason, ask = layouts.layout_for_signature(
                    saved, self.live, DESK, DESK_STREAMS
                )
                self.assertEqual(ask, "ask")
                self.assertEqual(layout, layouts.LAYOUT_ONE_PER_MONITOR)
                self.assertIn("monitor", reason)
                with _in(i18n.LANG_RU):
                    _again, russian, _ask = layouts.layout_for_signature(
                        saved, self.live, DESK, DESK_STREAMS
                    )
                self.assertIn("мониторе", russian)

    def test_a_saved_choice_with_no_signature_is_not_trusted(self) -> None:
        _layout, _reason, ask = layouts.layout_for_signature(
            layouts.LAYOUT_STACKED, "", DESK, DESK_STREAMS
        )
        self.assertEqual(ask, "ask")


if __name__ == "__main__":
    unittest.main()
