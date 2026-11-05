"""Text handling in `screenrec.hotkeys`.

Registration itself is not tested here: it needs a real message queue and a combination no
other program holds, which is a live check, not a unit test. What is tested is the part that
decides *what* gets registered and what the user is shown.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from screenrec import hotkeys  # noqa: E402


class ParseHotkeyTests(unittest.TestCase):
    def test_plain_combination(self) -> None:
        self.assertEqual(
            hotkeys.parse_hotkey("Ctrl+Alt+R"),
            (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("R")),
        )

    def test_function_key(self) -> None:
        self.assertEqual(hotkeys.parse_hotkey("Shift+F9"), (hotkeys.MOD_SHIFT, 0x78))

    def test_f24_is_the_last_function_key(self) -> None:
        self.assertEqual(hotkeys.parse_hotkey("F24"), (0, 0x87))
        self.assertIsNone(hotkeys.parse_hotkey("F25"))

    def test_windows_key(self) -> None:
        self.assertEqual(
            hotkeys.parse_hotkey("Win+Alt+F12"),
            (hotkeys.MOD_WIN | hotkeys.MOD_ALT, 0x7B),
        )

    def test_digit(self) -> None:
        self.assertEqual(hotkeys.parse_hotkey("Ctrl+7"), (hotkeys.MOD_CONTROL, 0x37))

    def test_case_and_spaces_are_free(self) -> None:
        expected = (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("R"))
        for text in ("ctrl+alt+r", "CTRL+ALT+R", "  Ctrl + Alt + R  ", "control+ALT+r"):
            with self.subTest(text=text):
                self.assertEqual(hotkeys.parse_hotkey(text), expected)

    def test_modifier_aliases(self) -> None:
        self.assertEqual(hotkeys.parse_hotkey("Super+K"), (hotkeys.MOD_WIN, ord("K")))
        self.assertEqual(hotkeys.parse_hotkey("Meta+K"), (hotkeys.MOD_WIN, ord("K")))

    def test_no_modifier_is_allowed(self) -> None:
        self.assertEqual(hotkeys.parse_hotkey("F9"), (0, 0x78))

    def test_norepeat_is_not_part_of_the_parsed_modifiers(self) -> None:
        modifiers, _vk = hotkeys.parse_hotkey("Ctrl+Alt+R")
        self.assertFalse(modifiers & hotkeys.MOD_NOREPEAT)

    def test_junk(self) -> None:
        for text in (
            "Ctrl+",
            "+R",
            "",
            "   ",
            "Banana",
            "Ctrl+Alt",          # modifiers but no key
            "Ctrl+Banana",
            "Ctrl++R",
            "Ctrl+Alt+R+",
            "R+Ctrl",            # key first, modifier last
            "Ctrl+Escape",       # a real key, but outside the supported set
            "Ctrl+Alt+Shift",
        ):
            with self.subTest(text=text):
                self.assertIsNone(hotkeys.parse_hotkey(text))

    def test_non_string(self) -> None:
        self.assertIsNone(hotkeys.parse_hotkey(None))  # type: ignore[arg-type]
        self.assertIsNone(hotkeys.parse_hotkey(17))  # type: ignore[arg-type]


class FormatHotkeyTests(unittest.TestCase):
    def test_fixed_order(self) -> None:
        every = hotkeys.MOD_WIN | hotkeys.MOD_SHIFT | hotkeys.MOD_ALT | hotkeys.MOD_CONTROL
        self.assertEqual(hotkeys.format_hotkey(every, ord("R")), "Ctrl+Alt+Shift+Win+R")

    def test_simple(self) -> None:
        self.assertEqual(
            hotkeys.format_hotkey(hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("R")),
            "Ctrl+Alt+R",
        )
        self.assertEqual(hotkeys.format_hotkey(hotkeys.MOD_SHIFT, 0x78), "Shift+F9")
        self.assertEqual(hotkeys.format_hotkey(0, 0x87), "F24")

    def test_norepeat_is_ignored(self) -> None:
        self.assertEqual(
            hotkeys.format_hotkey(hotkeys.MOD_CONTROL | hotkeys.MOD_NOREPEAT, ord("R")),
            "Ctrl+R",
        )

    def test_unknown_key_gives_nothing(self) -> None:
        self.assertEqual(hotkeys.format_hotkey(hotkeys.MOD_CONTROL, 0x1B), "")  # VK_ESCAPE
        self.assertEqual(hotkeys.format_hotkey(0, 0), "")


class RoundTripTests(unittest.TestCase):
    def test_text_to_value_to_text(self) -> None:
        """Canonical spellings survive unchanged."""
        for text in ("Ctrl+Alt+R", "Shift+F9", "F24", "Ctrl+Alt+Shift+Win+R", "Win+5"):
            with self.subTest(text=text):
                parsed = hotkeys.parse_hotkey(text)
                self.assertIsNotNone(parsed, text)
                self.assertEqual(hotkeys.format_hotkey(*parsed), text)

    def test_non_canonical_spellings_normalise_then_stay_put(self) -> None:
        """Odd input is re-spelled once, and the re-spelling is itself stable."""
        cases = {
            "win+alt+F12": "Alt+Win+F12",
            "  shift + f9 ": "Shift+F9",
            "ALT+CTRL+q": "Ctrl+Alt+Q",
            "super+meta+a": "Win+A",
        }
        for text, canonical in cases.items():
            with self.subTest(text=text):
                first = hotkeys.format_hotkey(*hotkeys.parse_hotkey(text))
                self.assertEqual(first, canonical)
                self.assertEqual(hotkeys.parse_hotkey(first), hotkeys.parse_hotkey(text))
                self.assertEqual(hotkeys.format_hotkey(*hotkeys.parse_hotkey(first)), canonical)

    def test_every_supported_key_round_trips(self) -> None:
        for name, vk in hotkeys._KEY_TO_VK.items():
            with self.subTest(key=name):
                text = f"Ctrl+{name}"
                parsed = hotkeys.parse_hotkey(text)
                self.assertEqual(parsed, (hotkeys.MOD_CONTROL, vk))
                self.assertEqual(
                    hotkeys.parse_hotkey(hotkeys.format_hotkey(*parsed)), parsed
                )


class ListenerLifecycleTests(unittest.TestCase):
    """No registration here - only the paths that must not touch Windows at all."""

    def test_bad_text_is_refused_without_starting_a_thread(self) -> None:
        listener = hotkeys.HotkeyListener(lambda: None)
        problem = listener.start("Banana")
        self.assertTrue(problem)
        self.assertIn("Banana", problem)
        self.assertFalse(listener.active)
        self.assertEqual(listener.error, problem)

    def test_stop_is_safe_before_start_and_twice(self) -> None:
        listener = hotkeys.HotkeyListener(lambda: None)
        listener.stop()
        listener.stop()
        listener.start("Ctrl+")
        listener.stop()
        listener.stop()
        self.assertFalse(listener.active)


if __name__ == "__main__":
    unittest.main()
