"""Checks for `screenrec.endpoints`.

Most of this module is COM calls, and the only honest way to check those is to make
them: the hardware-facing tests ask this machine and are skipped where COM is not
available, so the file still runs on a build server that has no audio at all. The
choosing logic - which endpoint counts as "the loud one" - is tested on its own, because
that is the part that decides what the user is told.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from screenrec import endpoints  # noqa: E402


class _Stub(endpoints.Endpoints):
    """An `Endpoints` with the peaks written down and no COM behind it."""

    def __init__(self, peaks: dict) -> None:  # noqa: D107 - deliberately no super()
        self.problem = ""
        self._enumerator = None
        self._devices = []
        self._peaks = dict(peaks)

    def peaks(self) -> dict:
        return dict(self._peaks)


class ChoosingTheLoudestTest(unittest.TestCase):
    """What "the sound is going somewhere else" means, exactly."""

    def test_the_loudest_endpoint_wins(self) -> None:
        pair = _Stub({"Speakers": 0.4, "Headphones": 0.1})
        self.assertEqual(pair.loudest()[0], "Speakers")

    def test_one_can_be_left_out(self) -> None:
        """The endpoint already being recorded is not the answer to "where else?"."""
        pair = _Stub({"Speakers": 0.4, "Headphones": 0.1})
        self.assertEqual(pair.loudest(ignoring="Speakers")[0], "Headphones")

    def test_a_quiet_machine_has_no_loudest(self) -> None:
        pair = _Stub({"Speakers": 0.0, "Headphones": 0.0})
        self.assertEqual(pair.loudest(), ("", 0.0))

    def test_residue_is_not_sound(self) -> None:
        """An idle endpoint shows a little something; sending a user to look at it
        would be worse than saying nothing. Measured at about 0.0004 on 2026-11-13."""
        pair = _Stub({"Speakers": 0.0004, "Headphones": 0.0})
        self.assertEqual(pair.loudest(), ("", 0.0))
        self.assertLess(0.0004, endpoints.AUDIBLE_PEAK)

    def test_the_floor_can_be_argued_with(self) -> None:
        pair = _Stub({"Speakers": 0.0004})
        self.assertEqual(pair.loudest(floor=0.0)[0], "Speakers")

    def test_nothing_at_all_is_not_an_error(self) -> None:
        self.assertEqual(_Stub({}).loudest(), ("", 0.0))


@unittest.skipUnless(endpoints.available(), "COM is not available here")
class AskingThisMachineTest(unittest.TestCase):
    """The parts that only a real audio stack can answer."""

    def setUp(self) -> None:
        self.watcher = endpoints.Endpoints()
        self.addCleanup(self.watcher.close)

    def test_it_opens(self) -> None:
        self.assertEqual(self.watcher.problem, "")

    def test_there_is_a_default_and_it_has_a_name(self) -> None:
        name = self.watcher.default_name()
        self.assertTrue(name.strip(), "Windows always has a playback default")

    def test_every_peak_is_a_number_in_range(self) -> None:
        peaks = self.watcher.peaks()
        self.assertTrue(peaks, "a machine with sound has at least one endpoint")
        for name, peak in peaks.items():
            with self.subTest(endpoint=name):
                self.assertTrue(name.strip())
                self.assertGreaterEqual(peak, 0.0)
                self.assertLessEqual(peak, 1.0)

    def test_the_default_is_one_of_the_endpoints(self) -> None:
        self.assertIn(self.watcher.default_name(), self.watcher.peaks())

    def test_refreshing_keeps_it_usable(self) -> None:
        before = set(self.watcher.peaks())
        self.watcher.refresh()
        self.assertEqual(set(self.watcher.peaks()), before)

    def test_closing_twice_is_safe_and_it_answers_empty_afterwards(self) -> None:
        self.watcher.close()
        self.watcher.close()
        self.assertEqual(self.watcher.default_name(), "")
        self.assertEqual(self.watcher.peaks(), {})
        self.assertEqual(self.watcher.loudest(), ("", 0.0))


if __name__ == "__main__":
    unittest.main()
