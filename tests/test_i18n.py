"""The catalogue, the switch, and the one test that stops them drifting apart.

`screenrec.i18n` keeps the English sentence as the key, which buys a readable source and
a safe fallback and costs exactly one thing: nothing but spelling links a call site to
translation. Reword a button and `_()` quietly returns the English, the window still
works, no error is raised, and the only symptom is a user seeing the wrong language.

`test_every_translatable_string_has_russian` is that symptom turned into a failing test.
It does not pin any wording - it reads the real source with `tools/check_translations.py`
and compares the strings that reach `_()` and `pick()` with the catalogue. Adding a
button with no translation fails it; renaming a sentence and forgetting its block fails
it; rewording the Russian does not.

The rest holds the machinery itself: the fallback, a refused language code, exactly one
signal per real change, the Russian plural rule with its 11-14 exception, and a default
language that is a language whatever the machine's locale says.

No window and no QApplication: `i18n` needs only QObject, so the suite stays headless.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path

from screenrec import i18n

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = PROJECT_ROOT / "tools" / "check_translations.py"


def _load_checker() -> types.ModuleType:
    """Import `tools/check_translations.py`, which is a script and not on sys.path.

    The module goes into `sys.modules` before it is executed: `@dataclass` looks its own
    class's module up there while the class body is being processed, and raises an
    AttributeError on None if it is not yet registered.
    """
    spec = importlib.util.spec_from_file_location("check_translations", CHECKER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the translation checker from {CHECKER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _translated_keys() -> list[str]:
    """Every catalogue key that has Russian, in a stable order."""
    return sorted(
        key for key, entry in i18n.CATALOGUE.items() if entry.get(i18n.LANG_RU)
    )


def _a_sentence_key() -> str:
    """Some translated sentence - a `_()` key, so not one of the dotted `pick()` keys.

    Chosen from the catalogue at run time rather than written here on purpose: these
    tests prove the machinery, and the wording belongs to whoever owns that block. A
    test that spells out a Russian sentence turns a reworded label into a red suite.
    """
    for key in _translated_keys():
        if "." not in key:
            return key
    raise unittest.SkipTest("the catalogue holds no translated sentence yet")


def _a_pick_key() -> str:
    """Some translated `pick()` key - the dotted kind."""
    for key in _translated_keys():
        if "." in key and " " not in key:
            return key
    raise unittest.SkipTest("the catalogue holds no pick() key yet")


class LanguageTestCase(unittest.TestCase):
    """Restores the language afterwards: it is process-wide state, like a setting."""

    def setUp(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)


# ------------------------------------------------------------------ the catalogue


class CatalogueTest(unittest.TestCase):
    """Does the catalogue still cover what the interface shows?"""

    def test_every_translatable_string_has_russian(self) -> None:
        """THE DRIFT TEST: a string shown to a user with no entry in the catalogue.

        Covers every `_("...")` and `pick("...")` in `app/screenrec`, found by parsing
        the source rather than by importing or running it, so a string on a branch that
        only a second monitor reaches is checked like one on the main window. A failure
        names the key and the file and line that shows it; the fix is a line in that
        area's block under `screenrec/translations`.
        """
        usage = checker.collect()
        missing = checker.missing_keys(usage)
        if missing:
            lines = [
                f"  {key!r}  ({usage.keys[key][0]})" for key in missing
            ]
            self.fail(
                f"{len(missing)} strings reach the interface with no Russian:\n"
                + "\n".join(lines)
                + "\n\nAdd each to the block for its area in app/screenrec/"
                "translations/, then run tools/check_translations.py."
            )

    def test_no_key_is_translated_in_two_blocks(self) -> None:
        self.assertEqual(
            i18n.duplicate_keys, [],
            "a key may live in exactly one block; move a shared sentence to "
            "translations/common.py, or tell two senses apart with i18n.pick()",
        )

    def test_no_translation_is_empty(self) -> None:
        empty = sorted(
            key for key, entry in i18n.CATALOGUE.items()
            if not entry.get(i18n.LANG_RU, "x").strip()
        )
        self.assertEqual(empty, [], "an empty translation shows as a blank control")

    def test_nothing_is_translated_through_an_f_string(self) -> None:
        """`_(f"{n} frames")` can never find a key: the sentence is built first.

        The template is what belongs in the catalogue - `_("{count} frames")
        .format(count=n)` - with `i18n.plural()` for the Russian word that goes with it.
        """
        usage = checker.collect()
        f_strings = [
            f"  {reference}  {why}" for reference, why in usage.unchecked
            if "f-string" in why
        ]
        self.assertEqual(f_strings, [], "\n" + "\n".join(f_strings))

    def test_the_catalogue_is_reachable_as_data(self) -> None:
        self.assertTrue(i18n.CATALOGUE, "no block reached the merged catalogue")
        for key, entry in i18n.CATALOGUE.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(entry, dict)


# ------------------------------------------------------------------ the switch


class TranslationTest(LanguageTestCase):
    def test_russian_is_returned_for_a_known_string(self) -> None:
        key = _a_sentence_key()
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(i18n._(key), i18n.CATALOGUE[key][i18n.LANG_RU])
        self.assertNotEqual(i18n._(key), key)

    def test_an_unknown_string_falls_back_to_the_english_source(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        unknown = "No block translates this sentence."
        self.assertNotIn(unknown, i18n.CATALOGUE)
        self.assertEqual(i18n._(unknown), unknown)

    def test_english_returns_the_source_untouched(self) -> None:
        key = _a_sentence_key()
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(i18n._(key), key)

    def test_pick_takes_the_key_and_falls_back_to_the_english_word(self) -> None:
        key = _a_pick_key()
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(i18n.pick(key, "Screen"), i18n.CATALOGUE[key][i18n.LANG_RU])
        self.assertEqual(i18n.pick("no.such.key", "Screen"), "Screen")
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(i18n.pick(key, "Screen"), "Screen")


class SetLanguageTest(LanguageTestCase):
    def test_a_known_code_is_adopted_and_returned(self) -> None:
        self.assertEqual(i18n.set_language(i18n.LANG_RU), i18n.LANG_RU)
        self.assertEqual(i18n.language(), i18n.LANG_RU)

    def test_an_unknown_code_is_refused_and_the_current_one_kept(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(i18n.set_language("de"), i18n.LANG_RU)
        self.assertEqual(i18n.language(), i18n.LANG_RU)
        self.assertEqual(i18n.set_language(""), i18n.LANG_RU)
        self.assertEqual(i18n.language(), i18n.LANG_RU)


class NotifierTest(LanguageTestCase):
    """One signal per real change - a window that retranslates must not do it twice."""

    def setUp(self) -> None:
        super().setUp()
        self.seen: list[str] = []
        i18n.notifier.changed.connect(self.seen.append)
        self.addCleanup(i18n.notifier.changed.disconnect, self.seen.append)

    def test_a_real_change_fires_exactly_once(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        self.seen.clear()
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(self.seen, [i18n.LANG_RU])

    def test_setting_the_language_already_in_force_fires_nothing(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.seen.clear()
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(self.seen, [])

    def test_a_refused_code_fires_nothing(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.seen.clear()
        i18n.set_language("de")
        self.assertEqual(self.seen, [])


# ------------------------------------------------------------------ plurals


class PluralTest(LanguageTestCase):
    FORMS = ("экран", "экрана", "экранов")

    def test_the_russian_rule_including_the_eleven_exception(self) -> None:
        """11 is the number that catches a version written from the last digit alone."""
        i18n.set_language(i18n.LANG_RU)
        expected = {
            1: "экран",
            2: "экрана",
            5: "экранов",
            11: "экранов",  # the exception: 11-14 take the many form
            21: "экран",
            22: "экрана",
            25: "экранов",
        }
        for count, word in expected.items():
            with self.subTest(count=count):
                self.assertEqual(i18n.plural(count, *self.FORMS), word)

    def test_the_rest_of_the_teens_take_the_many_form(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        for count in (0, 12, 13, 14, 100, 111):
            with self.subTest(count=count):
                self.assertEqual(i18n.plural(count, *self.FORMS), "экранов")

    def test_a_negative_count_is_read_by_its_size(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(i18n.plural(-1, *self.FORMS), "экран")
        self.assertEqual(i18n.plural(-11, *self.FORMS), "экранов")

    def test_english_uses_one_and_many_only(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(i18n.plural(1, "screen", "screens", "screens"), "screen")
        self.assertEqual(i18n.plural(2, "screen", "screens", "screens"), "screens")
        self.assertEqual(i18n.plural(11, "screen", "screens", "screens"), "screens")


# ------------------------------------------------------------------ the default


def _locale_named(code: str) -> type:
    """A stand-in for QLocale whose `system().name()` is `code`."""

    class Locale:
        @staticmethod
        def system() -> "Locale":
            return Locale()

        def name(self) -> str:
            return code

    return Locale


class _FailingLocale:
    """A QLocale that cannot answer - the case `default_language` guards against."""

    @staticmethod
    def system() -> "_FailingLocale":
        raise RuntimeError("no locale on this machine")


class DefaultLanguageTest(LanguageTestCase):
    """Whatever the system says, the answer has to be a language this program has."""

    def _with_locale(self, locale: object) -> None:
        original = i18n.QtCore
        i18n.QtCore = types.SimpleNamespace(QLocale=locale)
        self.addCleanup(setattr, i18n, "QtCore", original)

    def test_a_russian_system_suggests_russian(self) -> None:
        self._with_locale(_locale_named("ru_RU"))
        self.assertEqual(i18n.default_language(), i18n.LANG_RU)
        self.assertIn(i18n.default_language(), i18n.LANGUAGES)

    def test_any_other_system_suggests_english(self) -> None:
        for code in ("en_GB", "de_DE", "C", ""):
            with self.subTest(locale=code):
                self._with_locale(_locale_named(code))
                self.assertEqual(i18n.default_language(), i18n.LANG_EN)
                self.assertIn(i18n.default_language(), i18n.LANGUAGES)

    def test_a_locale_that_raises_still_gives_a_language(self) -> None:
        self._with_locale(_FailingLocale)
        self.assertIn(i18n.default_language(), i18n.LANGUAGES)

    def test_the_real_locale_also_gives_a_language(self) -> None:
        self.assertIn(i18n.default_language(), i18n.LANGUAGES)

    def test_every_language_has_a_native_name(self) -> None:
        for code in i18n.LANGUAGES:
            self.assertTrue(i18n.NATIVE_NAMES.get(code))


# ------------------------------------------------------------------ the checker


FIXTURE = '''\
"""A file the checker reads. _("not code, a docstring")"""
from ..i18n import _, pick

label = _("Hello")
again = i18n._("Hello")
key = pick("a.b", "Screen")
built = _(f"{count} frames")
indirect = _(message)
implicit = _("one " "two")
explicit = _("one " + "two")
private = self._pick(tool)
throwaway = [x for _ in items]
two_args = _("x", "y")
'''


class CheckerTest(unittest.TestCase):
    """The checker is what the drift test trusts, so it is checked on a known file."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="screenrec-i18n-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "fixture.py").write_text(FIXTURE, encoding="utf-8")
        self.usage = checker.collect(self.root)
        translated = _translated_keys()
        if len(translated) < 2:
            self.skipTest("needs two translated keys to tell used from dead")
        self.known = translated[0]
        self.other = translated[1]

    def write(self, source: str) -> object:
        """Replace the fixture with `source` and read the directory again."""
        (self.root / "fixture.py").write_text(source, encoding="utf-8")
        return checker.collect(self.root)

    @staticmethod
    def _report(usage: object, **options: object) -> tuple[int, str]:
        """Run the report with its printing captured, so the suite stays readable."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = checker.report(usage, **options)
        return status, buffer.getvalue()

    def test_it_finds_literals_including_both_concatenations(self) -> None:
        self.assertEqual(
            sorted(self.usage.keys), ["Hello", "a.b", "one two"]
        )

    def test_a_qualified_call_counts_and_a_private_method_does_not(self) -> None:
        self.assertEqual(len(self.usage.keys["Hello"]), 2)  # _() and i18n._()
        self.assertNotIn("tool", self.usage.keys)

    def test_the_first_argument_of_pick_is_the_key(self) -> None:
        self.assertIn("a.b", self.usage.keys)
        self.assertNotIn("Screen", self.usage.keys)

    def test_an_f_string_and_a_variable_are_counted_not_dropped(self) -> None:
        reasons = [why for _reference, why in self.usage.unchecked]
        self.assertEqual(len(reasons), 2, reasons)
        self.assertTrue(any("f-string" in why for why in reasons), reasons)
        self.assertTrue(any("message" in why for why in reasons), reasons)

    def test_a_call_with_the_wrong_number_of_arguments_is_reported(self) -> None:
        reasons = [why for _reference, why in self.usage.malformed]
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("2 arguments", reasons[0])

    def test_a_reference_names_the_file_and_line(self) -> None:
        reference = self.usage.keys["a.b"][0]
        self.assertEqual(reference.line, 6)
        self.assertIn("fixture.py", str(reference))

    def test_it_skips_bytecode_caches(self) -> None:
        cache = self.root / "__pycache__"
        cache.mkdir()
        (cache / "fixture.py").write_text('x = _("Cached")', encoding="utf-8")
        self.assertNotIn("Cached", checker.collect(self.root).keys)

    def test_a_missing_key_is_reported_and_fails_the_run(self) -> None:
        unknown = "Nothing translates this one"
        usage = self.write(f"x = _({unknown!r})")
        self.assertEqual(checker.missing_keys(usage), [unknown])
        status, printed = self._report(usage)
        self.assertEqual(status, 1)
        self.assertIn(unknown, printed)

    def test_a_translated_key_passes_the_run(self) -> None:
        usage = self.write(f"x = _({self.known!r})")
        self.assertEqual(checker.missing_keys(usage), [])
        self.assertEqual(self._report(usage)[0], 0)

    def test_unused_catalogue_entries_are_listed_as_dead(self) -> None:
        dead = checker.dead_keys(self.write(f"x = _({self.known!r})"))
        self.assertNotIn(self.known, dead)
        self.assertIn(self.other, dead)

    def test_strict_fails_on_dead_entries_and_the_default_does_not(self) -> None:
        usage = self.write(f"x = _({self.known!r})")
        self.assertEqual(self._report(usage, strict=False)[0], 0)
        self.assertEqual(self._report(usage, strict=True)[0], 1)

    def test_main_returns_an_exit_status(self) -> None:
        self.write(f"x = _({self.known!r})")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = checker.main(["--root", str(self.root)])
        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
