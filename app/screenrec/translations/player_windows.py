"""The Russian block for the player_windows area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.

**This block is deliberately short.** The separate-windows mode shares most of its
vocabulary with the rest of the player - «Расположение», «Вернуть раскладку»,
«Показать второй экран», «Перемотка…», the ended badge, the transport's seven
tooltips - and every one of those already lives in `translations/player.py`. A second
copy here would be a duplicate key, which the merge refuses, and the refusal would be
right: two spellings of «Вернуть раскладку» in one window is exactly the drift the
one-key-one-block rule exists to stop. So only the sentences this mode alone says are
written below.

`docs/PLAYER_SOURCES.md`, and why its half is `pick()` keys
-----------------------------------------------------------
The second half of this block is §1 and §2 of `docs/PLAYER_SOURCES.md` - opening files
by hand and lining two cameras up - and every key in it is a dotted `pick()` key rather
than the English sentence. That is deliberate and it is not the usual case.

The stage and the separate-windows bar are **two window layers of one player**, and
they now carry the same four controls: «Открыть…», «Добавить второй экран…»,
«Сохранить набор», «Сбросить», «−1 кадр», «+100 мс». Written as English keys those
would be the same key twice - once in `player_stage.py` and once here - and the merge
refuses a duplicate, rightly. A sentence genuinely shared by two areas belongs in
`common.py`; neither of the two people writing these layers owns that file, and a key
that cannot go in the shared block has to be told apart some other way.
`pick(key, english)` is that way, and the package already documents it: the English at
the call site is identical in both layers - so the English interface reads the same
whichever window the viewer is in - while the Russian is owned here and collides with
nothing.

**What should happen next**: the §1/§2 vocabulary belongs in `translations/common.py`
(or in a `player_sources.py` block of its own), with both layers back on plain `_()`
and the English as the key. These prefixed keys exist because two layers were written
at the same time by two people, not because the two senses differ - which is what
`pick()` is normally for - and that is worth saying out loud rather than leaving
somebody to infer it from the prefix.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- the arrange menu -----------------------------------------------------
    # The four schemes themselves are `layouts.tiling_title()`, translated in the
    # player block beside the layout names they belong with.
    "As they sat on the desktop": "Как на рабочем столе",
    "Tile on monitor": "Раскладка на мониторе",
    # The monitor's own usable size, so a desk with two identical monitors can
    # still be told apart by position when the sizes match.
    "Monitor {number} — {width}×{height}": "Монитор {number} — {width}×{height}",
    "Which screen on which monitor": "Какой экран на каком мониторе",
    # Used for the third screen onwards, where "the second screen" is not what the
    # viewer closed. The two-screen case keeps §5's own wording.
    "Show {name}": "Показать {name}",

    # -- a window that will stay black ----------------------------------------
    # The player block holds the same two sentences about a *pane*, which is what
    # the stage shows. A separate window is not a pane, and a sentence telling the
    # viewer that "this pane" is black names nothing they can see, so these are
    # their own keys rather than a shared one with a vague noun in it.
    "{name}: no source of frames — this window will stay black.":
        "{name}: нет источника кадров — это окно останется чёрным.",
    "{name}: the source of frames has no videoFrameChanged signal "
    "— this window will stay black.":
        "{name}: источник кадров без сигнала videoFrameChanged "
        "— это окно останется чёрным.",

    # -- a screen put away, §5 ------------------------------------------------
    # The notice strip is a line of news, so it is a sentence rather than the
    # menu entry's own words; the entry itself is «Показать второй экран», which
    # the player block already holds.
    "{name} is hidden — the Arrange menu brings it back.":
        "{name} скрыт — вернуть его можно из меню «Расположение».",

    # -- §7's `S`, pressed while one of the two windows is away ---------------
    # The entry itself and the arrange button's tooltip are «Поменять местами
    # (S)» and «Расположение (L)», which the player_stage block holds: the stage
    # says the same words about the same two keys, and §6's keyboard table is
    # one table for the whole player. This sentence is the one thing only this
    # half can say, because only this half can have a window put away.
    "Both screens have to be open before they can change places.":
        "Поменять местами можно, когда открыты оба экрана.",

    # -- a monitor unplugged while watching, §6 -------------------------------
    "A monitor is gone — {names} moved to one that is still there.":
        "Монитора больше нет — {names} перенесены туда, где есть экран.",
    # One entry in that list, for the transport bar: it is a window like the
    # others and it is the one carrying every control, so it is named rather
    # than moved in silence.
    "the controls": "панель управления",

    # ======================================================================
    # docs/PLAYER_SOURCES.md. Every key below is a `pick()` key; the module
    # docstring says why, and what should replace them.
    # ======================================================================

    # -- §1: the Open button and its menu -------------------------------------
    "windows.open.button": "Открыть запись (Ctrl+O)",
    "windows.open.dialog": "Открыть…",
    "windows.open.dialog.title": "Открыть запись",
    # Two filters for two dialogs: «Открыть…» takes an index or a video, and
    # «Добавить второй экран…» takes only a video, because an index is not a
    # camera and pairing one with a file is not a thing the format can say.
    "windows.filter.any": "Записи (*{index} {patterns})",
    "windows.filter.video": "Видео ({patterns})",
    # A library row. The size is in whole megabytes - a row is read sideways,
    # and a tenth of a megabyte is a digit nobody is comparing.
    "windows.library.row": "{name} — {date}, {size} МБ",
    "windows.library.row.set": "{name} — {date}, экранов: {screens}, {size} МБ",
    # Carries an exception's own text, which stays English inside the brackets.
    "windows.open.failed": "{path} не открывается: {detail}",
    "windows.open.unshown": "Плеер не открылся: {reason}",

    # -- §1.1: adding the second camera ---------------------------------------
    "windows.open.second": "Добавить второй экран…",
    "windows.open.second.title": "Добавить второй экран",
    "windows.open.second.unknown":
        "Неизвестно, из какого файла идёт эта картинка, поэтому второй экран "
        "к ней не добавить.",

    # -- §1.2: which stream is heard ------------------------------------------
    # «Звук выключен» rather than «Выключен» on its own: the chip is read on a
    # bar beside the volume button, where a lone «Выключен» names neither the
    # thing that is off nor the thing it belongs to.
    "windows.audio.off": "Звук выключен",
    "windows.audio.hint": "Звук идёт с «{name}» — нажмите, чтобы сменить",
    "windows.audio.hint.off": "Звук выключен — нажмите, чтобы выбрать источник",

    # -- §1.3: saving the pair ------------------------------------------------
    "windows.open.save": "Сохранить набор",
    "windows.save.alone": "Открыто одно видео — набора из него не выйдет.",
    "windows.save.failed": "Ничего не сохранено.",
    "windows.save.done": "Сохранено как {name}",

    # -- §2.1: a stream the timeline has not reached yet ----------------------
    # «начинается позже», not «ещё не началась»: the recording did start, two
    # seconds after the one beside it, and the viewer is the one who said so.
    "windows.waiting": "{name} — эта запись начинается позже",

    # -- §2.2: the sync panel -------------------------------------------------
    # The unit is a word of its own here because it is glued to a number the
    # program formats (`window.format_offset`), and «ms» in a Russian panel is
    # the one place a reader would think the field was not translated.
    "windows.sync.ms": "мс",
    "windows.sync.frame.back": "−1 кадр",
    "windows.sync.frame.on": "+1 кадр",
    "windows.sync.coarse.back": "−100 мс",
    "windows.sync.coarse.on": "+100 мс",
    "windows.sync.reset": "Сбросить",
    "windows.sync.reset.hint": "Вернуть этот экран в общий ход",
    "windows.sync.picker": "Какой экран сдвигается",
    "windows.sync.frame.hint": "Один кадр опорного потока, {rate} кадр/с",
    # §2.1 in one sentence, beside the field, because a sign convention nobody
    # can remember is a control nobody can use.
    "windows.sync.sign":
        "Миллисекунды. Плюс — это видео идёт впереди, в него нужно промотать.",
    "windows.sync.moved": "{name} {offset}",
    "windows.sync.in.step": "оба идут синхронно",
    "windows.sync.button": "Свести экраны вручную",
    "windows.sync.button.moved": "Свести экраны — один сдвинут на {offset}",
}
