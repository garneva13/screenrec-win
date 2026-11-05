"""The translations `tools/check_translations.py` is structurally unable to see.

The drift test in `test_i18n.py` reads the source and collects every *literal* handed
to `_()`. That covers the whole interface except one shape, and the settings panel is
full of it: a table of data holds the English, and the call site translates a variable.

    items += [(_(preset.name), preset.key) for preset in presets_mod.BUILTIN]
    return [(_(label), key) for key, label in shellops.ACTIONS]

The checker sees `_(preset.name)` and can only record "a variable, not a literal". So
the Preset combo and the "When it ends" combo stayed English for a whole release while
the catalogue, the call sites and the whole suite were green: `_()` fell back to its
argument and nothing anywhere could notice.

This file closes that hole for the tables the recorder area owns. It checks the data,
not the panel, because the data is what is missing - and it pins no wording, so
rewording the Russian keeps it green and rewording the *English* in `presets.py` or
`shellops.py` without touching the block turns it red, which is the point.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from screenrec import i18n, presets, shellops  # noqa: E402


class LanguageTestCase(unittest.TestCase):
    """Restores the language afterwards: it is process-wide state, like a setting."""

    def setUp(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)


class PresetTableTest(LanguageTestCase):
    """`presets.BUILTIN` - the Preset combo and the note under it."""

    def test_every_built_in_name_and_note_has_russian(self) -> None:
        texts = [preset.name for preset in presets.BUILTIN]
        texts += [preset.note for preset in presets.BUILTIN if preset.note]
        self.assertTrue(texts, "presets.BUILTIN is empty")
        self.assertEqual(
            i18n.missing_translations(texts), [],
            "a built-in preset shows English in the Russian interface; add it to "
            "app/screenrec/translations/recorder.py",
        )

    def test_the_combo_text_really_changes_language(self) -> None:
        """What the panel builds: `_(preset.name)` over the table, under Russian."""
        i18n.set_language(i18n.LANG_RU)
        for preset in presets.BUILTIN:
            with self.subTest(preset=preset.key):
                self.assertNotEqual(i18n._(preset.name), preset.name)
                if preset.note:
                    self.assertNotEqual(i18n._(preset.note), preset.note)


class ShellActionTableTest(LanguageTestCase):
    """`shellops.ACTIONS` - the "When it ends" combo."""

    def test_every_action_label_has_russian(self) -> None:
        labels = [label for _key, label in shellops.ACTIONS]
        self.assertTrue(labels, "shellops.ACTIONS is empty")
        self.assertEqual(
            i18n.missing_translations(labels), [],
            "an after-recording action shows English in the Russian interface; add "
            "it to app/screenrec/translations/recorder.py",
        )

    def test_the_combo_text_really_changes_language(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        for key, label in shellops.ACTIONS:
            with self.subTest(action=key or "nothing"):
                self.assertNotEqual(i18n._(label), label)


if __name__ == "__main__":
    unittest.main()
