"""Two languages, one catalogue, switched while the program is running.

The whole interface was written in English. The user asked for Russian as well, with a
switch in the settings, so this module is the layer that makes a string translatable
without any of the calling code having to think about it:

    from ..i18n import _
    button.setText(_("Start recording"))

**The English text is the key.** There is no `STR_START_RECORDING` constant anywhere.
Writing the sentence where it is used keeps the source readable - a reader of `bar.py`
sees what the button says - and it means an untranslated string degrades to correct
English rather than to a bare identifier on a button. The cost is that two different
senses of one English word need distinguishing by hand; `pick()` exists for that and is
used, not worked around.

**No .ts files and no build step.** Qt's own translation system would be the obvious
choice and it was not taken: it needs `lrelease` run over a `.ts` file to produce a
binary `.qm`, which is a build artefact to generate, ship and keep in step with the
source. This program has no build step at all - it is run from the files on disk - and
adding one so that a hundred strings can live in a binary nobody can diff is a bad
trade. A dictionary in a Python file is diffable, greppable, and testable, and the test
that matters is written: `tests/test_i18n.py` walks every source file, collects every
literal passed to `_()`, and fails if one of them is missing from the catalogue. A
missing translation is therefore a failing test, not a sentence a user finds in the
wrong language.

**Switching is live where it can be.** `notifier.changed` fires on a change, and a
window that can rebuild its own text connects to it and does. A window that cannot is
not a lie as long as the setting says so - see `ui/settings_panel.py`, which says which
parts need reopening.

**Russian plurals are not English plurals.** "3 screens" is one rule; Russian has three
forms (1 экран, 2 экрана, 5 экранов) chosen by the last digit. `plural()` implements the
real rule, including the 11-14 exception that catches every naive attempt.
"""
from __future__ import annotations

from PySide6 import QtCore

__all__ = [
    "LANG_EN", "LANG_RU", "LANGUAGES", "NATIVE_NAMES",
    "_", "pick", "plural", "language", "set_language", "default_language",
    "notifier", "CATALOGUE", "missing_translations",
]

LANG_EN = "en"
LANG_RU = "ru"

#: The order the settings panel offers them in.
LANGUAGES: tuple[str, ...] = (LANG_EN, LANG_RU)

#: Each language's name *in that language*. A list of languages written in a language
#: the reader cannot read is a small cruelty that costs nothing to avoid.
NATIVE_NAMES: dict[str, str] = {LANG_EN: "English", LANG_RU: "Русский"}


class _Notifier(QtCore.QObject):
    """Carries the language change to any window that can rebuild its own text."""

    changed = QtCore.Signal(str)


notifier = _Notifier()

_current = LANG_EN


def language() -> str:
    """The language in force now."""
    return _current


def set_language(code: str) -> str:
    """Switch the language. Returns the code actually in force.

    An unknown code is refused rather than stored: the settings file is editable by
    hand, and a program that silently runs in a language nobody asked for is harder to
    explain than one that stays in English.
    """
    global _current
    if code not in LANGUAGES:
        return _current
    if code != _current:
        _current = code
        notifier.changed.emit(code)
    return _current


def default_language() -> str:
    """Russian for a Russian system, English otherwise.

    Only consulted when the settings file has no choice stored - once the user picks,
    their pick wins for ever, including when they pick the one the system suggests.
    """
    try:
        name = QtCore.QLocale.system().name()
    except Exception:  # noqa: BLE001 - a locale must never stop the program starting
        return LANG_EN
    return LANG_RU if name.startswith("ru") else LANG_EN


def _(text: str) -> str:
    """The translation of `text` for the current language, or `text` itself.

    Falling back to the English source is deliberate: an untranslated string shows as a
    correct English sentence. `tests/test_i18n.py` is what stops that fallback becoming
    the normal case.
    """
    if _current == LANG_EN:
        return text
    entry = CATALOGUE.get(text)
    if entry is None:
        return text
    return entry.get(_current, text)


def pick(key: str, english: str) -> str:
    """For an English word whose two senses need two Russian words.

    `_("Screen")` cannot be both the monitor and the picture. `pick("source.screen",
    "Screen")` looks the *key* up and falls back to the English text, so the call site
    still reads as the sentence it produces.
    """
    if _current == LANG_EN:
        return english
    entry = CATALOGUE.get(key)
    if entry is None:
        return english
    return entry.get(_current, english)


def plural(count: int, one: str, few: str, many: str) -> str:
    """The right Russian form for `count`, or `one`/`many` in English.

    The rule, which every naive version gets wrong: 11 to 14 take the *many* form
    whatever their last digit, so 11 is "экранов" while 21 is "экран".
    """
    if _current != LANG_RU:
        return one if abs(count) == 1 else many
    number = abs(count) % 100
    if 11 <= number <= 14:
        return many
    last = number % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


#: English source text -> {language code: translation}.
#:
#: Keys that start with a dotted prefix (``source.screen``) are `pick()` keys, not
#: sentences; everything else is the English string exactly as it is written at the call
#: site, whitespace and punctuation included.
CATALOGUE: dict[str, dict[str, str]] = {}


def add(entries: dict[str, str], language_code: str = LANG_RU) -> None:
    """Merge a block of translations in. Used by the catalogue files below."""
    for source, translated in entries.items():
        CATALOGUE.setdefault(source, {})[language_code] = translated


def missing_translations(texts: list[str]) -> list[str]:
    """Which of `texts` have no Russian. The test's half of the contract."""
    return [
        text for text in texts
        if LANG_RU not in CATALOGUE.get(text, {})
    ]


from .translations import DUPLICATES, RUSSIAN  # noqa: E402 - `add` must exist first

add(RUSSIAN)

#: Keys two areas both translate. Empty in a healthy tree; `tests/test_i18n.py` holds it
#: that way. Kept as data rather than raised at import, because a duplicated word must
#: not stop the program starting.
duplicate_keys: list[str] = list(DUPLICATES)
