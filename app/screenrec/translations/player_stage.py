"""The Russian block for the player_stage area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.

**This block is deliberately short, and that is the point.** The stage is one of the
two ways the player shows a two-screen recording, and nearly everything it says the
separate-windows mode says too: «Поменять местами», «Выровнять», «Показать второй
экран», «Перемотка», «Во весь экран», the seven transport tooltips, the volume
sentence, «Синхронное воспроизведение», «{count} видеопотока», the layout names
themselves. Every one of those already lives in `translations/player.py`, so the stage
calls `_()` with the key that block owns rather than writing a second spelling here -
a duplicate the merge would refuse, and rightly, because two wordings of «Выровнять»
in one window is the drift the one-key-one-block rule exists to stop.

What is left below is the handful of sentences only the stage says. Four of them are
the same control with a different shortcut on it: the stage's frame step is `,` and `.`
where the floating transport's is Left and Right, and its layout menu and swap button
carry `L` and `S`, which the separate-windows chrome has no equivalent of. The keys are
written out with the shortcut inside them rather than built from a translated sentence
plus a bracketed letter, because the bracket's position is not the same in every
language and gluing it on in Python would leave the translator no way to move it.

**It stopped being short with `docs/PLAYER_SOURCES.md`.** Opening your own files,
choosing which of them the sound comes from and sliding one against the other are
three things no other window in this program does, so there was nothing to borrow: the
second half of this block is all of it. What is still borrowed rather than respelled is
every word that already existed somewhere - «Открыть» from the player block, «Выкл.»
from the settings, «Записей пока нет» from the chrome - because the one-key-one-block
rule is about the *word*, not about which window happens to say it.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- the header row -------------------------------------------------------
    # The menu itself is «Расположение» in the player block; this is the chip that
    # opens it, which carries the keyboard shortcut as well.
    "Arrange (L)": "Расположение (L)",
    "Swap the screens (S)": "Поменять местами (S)",
    "Give both screens the same room": "Дать обоим экранам поровну",
    # `focus`'s 1 | 2 switch. The cells say "1" and "2", which are the same in both
    # languages; this is the sentence that explains what they do.
    # The two keys are named on the control they move: §6 of
    # docs/PLAYER_LAYOUTS.md gives `1` and `2` to the stage layouts, and the
    # separate-windows half has no main stream for them to choose.
    "Which screen fills the window (1, 2)": "Какой экран занимает окно (1, 2)",

    # -- the docked transport -------------------------------------------------
    # §7 gives the stage comma and full stop for the frame step, where the floating
    # bar has Left and Right. Different keys because they are different sentences,
    # not because the wording drifted.
    "One frame back (,)": "Кадр назад (,)",
    "One frame on (.)": "Кадр вперёд (.)",
    # «Во весь экран» is in the player block; only the way back out is new.
    "Leave full screen": "Выйти из полноэкранного режима",

    # -- §6: a monitor unplugged while watching -------------------------------
    # `player_windows` has the plural form of this, which names the windows that
    # moved. A stage is one window and has no list to put in it, so the sentence is
    # its own rather than that template with «это окно» pushed through the hole.
    "A monitor is gone — this window moved to one that is still there.":
        "Монитор отключён — окно перенесено на оставшийся.",

    # -- the two counted nouns ------------------------------------------------
    # Russian needs three forms where English needs one, so these are `pick()` keys
    # chosen by `i18n.plural` rather than one template with a hole. The *few* form of
    # the stream count is not here: it is `"{count} video streams"` in the player
    # block, already spelled «{count} видеопотока», and a key lives in one block only.
    "stage.screens.one": "{count} экран",
    "stage.screens.few": "{count} экрана",
    "stage.screens.many": "{count} экранов",
    "stage.streams.one": "{count} видеопоток",
    "stage.streams.many": "{count} видеопотоков",

    # -- the two sentences the constructor puts on the notice strip -----------
    # They reach the viewer, not the log, which is why they are here at all: as
    # f-strings they were the only English left in a Russian stage. `{screens}` is
    # already a counted noun from the keys above, and `{names}` holds stream titles.
    "This recording has {screens} and this window shows two. "
    "Not on it: {names}. Separate windows show them all.":
        "В этой записи {screens}, а это окно показывает два. "
        "Не показаны: {names}. Отдельные окна покажут все.",
    "This window shows two screens and this recording has {screens}; "
    "the second pane will stay black.":
        "Это окно показывает два экрана, а в записи {screens}; "
        "вторая панель останется чёрной.",

    # == docs/PLAYER_SOURCES.md: five sentences, and five only ===============
    # §1 and §2 gave the player two transports with the same controls on them -
    # the floating bar of `window.py` and this docked one - and every word on
    # those controls is already in the `player_windows` block, under the
    # `windows.*` keys their own `pick()` calls use: «Открыть…», «Добавить второй
    # экран…», «Сохранить набор», the whole sync panel, the sound chip, the badge
    # over a stream that has not started, the library rows and the file filters.
    # The stage reads *those* keys rather than spelling the same buttons again,
    # because two Russian wordings of one button is exactly the drift the
    # one-key-one-block rule exists to stop - and a viewer who uses both halves
    # of the player would see both wordings.
    #
    # What is left here is what only this window can say.

    # The submenu's title. `window.PlayerWindows` puts its library rows straight
    # into the Open menu, so it has no title to translate and this does.
    "stage.library": "Библиотека",

    # Three refusals. The separate-windows half cannot reach any of them: it
    # rebuilds a whole player per recording, where this one keeps its window and
    # therefore has to answer for the clock it already has.
    "stage.second.already": "В этой записи уже два экрана.",
    "stage.audio.fixed": "Звук этой записи нельзя перенести, пока она открыта.",
    "stage.sync.cannot": "Часы этого плеера не умеют сводить два видео.",

    # Three files dropped at once. Both holes carry file names, which stay as
    # they are in either language.
    "stage.drop.two": "Одновременно открываются два видео: {first} и {second}.",
}
