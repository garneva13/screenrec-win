"""The log that exists because two failures in one day said nothing at all.

ScreenRec runs with no console, and Qt hands an exception raised inside a slot to
`sys.excepthook` rather than letting it out of the signal - so the program keeps
running, looks well, and does nothing. On 2026-11-13 that cost two long diagnoses:
a start that wedged the interface, and a recording that refused to begin. Both would
have been a minute's work with a file to read.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from screenrec import errorlog, paths  # noqa: E402


class WritingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = paths.own_settings_folder()
        self._folder.__enter__()

    def tearDown(self) -> None:
        self._folder.__exit__(None, None, None)

    def test_an_entry_lands_in_a_file_with_a_clock_on_it(self) -> None:
        path = errorlog.write("something went wrong")
        self.assertIsNotNone(path)
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("something went wrong", text)
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    def test_entries_accumulate_rather_than_replacing_each_other(self) -> None:
        errorlog.write("first")
        path = errorlog.write("second")
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("first", text)
        self.assertIn("second", text)

    def test_it_is_rotated_rather_than_filling_the_disk(self) -> None:
        """A timer that throws on every tick would otherwise write until the disk the
        recordings are going to is full."""
        path = errorlog.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * (errorlog.MAX_BYTES + 1), encoding="utf-8")
        errorlog.write("after the rotation")
        self.assertTrue(path.with_suffix(".log.1").exists())
        self.assertNotIn("after the rotation", path.with_suffix(".log.1").read_text(
            encoding="utf-8"))
        self.assertIn("after the rotation", path.read_text(encoding="utf-8"))

    def test_a_folder_that_cannot_be_written_is_not_an_error_of_its_own(self) -> None:
        """This runs while something has already gone wrong; it must never raise."""
        with mock.patch.object(Path, "mkdir", side_effect=OSError("no")):
            self.assertIsNone(errorlog.write("nowhere to put this"))


class HookTest(unittest.TestCase):
    """`install` must add to what is there, not take it over."""

    def setUp(self) -> None:
        self._folder = paths.own_settings_folder()
        self._folder.__enter__()
        self._was = sys.excepthook
        self._was_thread = threading.excepthook
        errorlog._installed = False

    def tearDown(self) -> None:
        sys.excepthook = self._was
        threading.excepthook = self._was_thread
        errorlog._installed = False
        self._folder.__exit__(None, None, None)

    def test_an_unhandled_exception_is_written_down_and_still_passed_on(self) -> None:
        seen: list = []
        sys.excepthook = lambda *args: seen.append(args)
        errorlog.install()
        try:
            raise ValueError("the slot threw")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        self.assertEqual(len(seen), 1, "whatever was there still runs")
        text = errorlog.log_path().read_text(encoding="utf-8")
        self.assertIn("ValueError: the slot threw", text)

    def test_a_thread_that_dies_is_written_down_with_its_name(self) -> None:
        """The feeder, the meters and the hotkey listener are all threads, and an
        exception on one of them used to go to a console this build has not got."""
        errorlog.install()

        def explode() -> None:
            raise RuntimeError("the capture thread died")

        thread = threading.Thread(target=explode, name="loopback")
        thread.start()
        thread.join()
        text = errorlog.log_path().read_text(encoding="utf-8")
        self.assertIn("loopback", text)
        self.assertIn("the capture thread died", text)

    def test_installing_twice_does_not_stack_the_hooks(self) -> None:
        errorlog.install()
        once = sys.excepthook
        errorlog.install()
        self.assertIs(sys.excepthook, once)


if __name__ == "__main__":
    unittest.main()
