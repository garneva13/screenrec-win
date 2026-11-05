"""The Russian block for the player_chooser area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.

Most of what the layout dialog says is already translated in the `player` block - the
layout names, the recommendation's reason line, "Рекомендуется", "Запомнить мой выбор",
the two buttons. They are used from there rather than copied here, because a key lives
in one block and one block only.

What is new is the monitor count. Russian agrees the predicate with the number as well
as the noun - *Доступен* один монитор, *Доступно* два монитора, *Доступно* пять
мониторов - so the whole sentence has three forms and not just the noun. A template with
a hole for "монитор" would be right for 1 and wrong for everything else, which is why
these are `i18n.pick()` keys fed to `i18n.plural()` rather than one `_()` template. The
English fallbacks beside them are the two forms English has; `plural()` returns the
`one` form for 1 and the `many` form otherwise whenever the language is not Russian.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- how many monitors there are, as one sentence per plural form ---------
    "chooser.monitors.one": "Доступен {count} монитор",
    "chooser.monitors.few": "Доступно {count} монитора",
    "chooser.monitors.many": "Доступно {count} мониторов",

    # -- how many screens the recording has -----------------------------------
    # The counted word is a hole in both languages, not only in the Russian. The
    # earlier English key spelt "screens" out and took the `{word}` only in its
    # translation, which `str.format` accepts and ignores: right for 3 and 5, and
    # "This recording has 1 screens" for the one-screen case the dialog's own
    # constructor reaches. "two" keeps its spelled-out sentence in the `player`
    # block - it is the sentence nine viewers in ten read, and "В записи 2 экрана"
    # reads like a log line.
    "This recording has {count} {word}": "В записи {count} {word}",

    # -- why a card cannot be chosen ------------------------------------------
    # "Needs a second monitor" is the two-screen case and lives in the `player`
    # block. These two are the rest of it: a recording with more screens than the
    # two-monitor sentence covers, and the case where nothing can be built at all
    # because no screen reported an area or the streams never arrived.
    "Needs one monitor for each screen": "Нужен отдельный монитор для каждого экрана",
    "Not available for this recording": "Для этой записи недоступно",
}
