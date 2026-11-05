"""The Russian block for the player area. See `translations/__init__.py`.

Kept apart from `i18n.py` so that the machinery and the words are two files: the
machinery is read once and the words are edited constantly. The key is the English
source text, exactly as it is written at the call site - see `i18n`'s docstring for why
the text and not an identifier.

Keys with a dotted prefix are `i18n.pick()` keys: an English word whose two senses need
two different Russian words.

`tests/test_i18n.py` walks the source for every `_("...")` literal and fails when one is
missing from here, so this file cannot quietly fall behind the interface.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- the player's layouts -------------------------------------------------
    "Side by side": "Рядом",
    "Stacked": "Друг над другом",
    "Focus on one": "Фокус на одном",
    "Separate windows": "Отдельные окна",
    "One screen per monitor": "По одному экрану на монитор",

    "Both screens in one window, left and right.":
        "Оба экрана в одном окне, слева и справа.",
    "Both screens in one window, one above the other.":
        "Оба экрана в одном окне, один над другим.",
    "One screen fills the window; a switch picks which.":
        "Один экран во всё окно, переключатель выбирает какой.",
    "Each screen in its own window, which you can move.":
        "Каждый экран в своём окне, окна можно двигать.",
    "Each screen on its own monitor, filling it.":
        "Каждый экран на отдельном мониторе, во всю его площадь.",

    "Equal halves, left and right": "Поровну, слева и справа",
    "Bigger on the left": "Больше слева",
    "Bigger on the right": "Больше справа",
    "Equal halves, top and bottom": "Поровну, сверху и снизу",

    # -- the recommendation, in words -----------------------------------------
    # `layouts._reason` builds one line out of these at the moment the card is
    # drawn, so the whole line follows a language switch. The percentage carries
    # its own entry because Russian puts a space before the sign and English
    # does not.
    "{percent}%": "{percent} %",
    "each screen in its own window — you choose the size":
        "каждый экран в своём окне — размер выбираете вы",
    "there is nothing here to measure": "размеры посчитать не из чего",
    "only {name} is visible, {percent} of natural size":
        "виден только {name}, {percent} от натуральной величины",
    "each screen on its own monitor": "каждый экран на своём мониторе",
    "both screens in full": "оба экрана целиком",
    "all the screens in full": "все экраны целиком",
    "{what}, at natural size": "{what}, в натуральную величину",
    "{what}, almost at natural size": "{what}, почти в натуральную величину",
    "{what}, {percent} of natural size each":
        "{what}, по {percent} от натуральной величины",
    "{name} — {percent}": "{name} — {percent}",
    "{what}: {sizes} of natural size": "{what}: {sizes} от натуральной величины",
    "the screen sizes are not known yet — the default layout":
        "размеры экранов пока не известны — раскладка по умолчанию",
    "no layout fitted these screens — the default layout":
        "ни одна раскладка не подошла по размеру — раскладка по умолчанию",

    # -- a remembered choice, §2.3 --------------------------------------------
    "\"{saved}\" does not fit now — \"{suggested}\" is recommended: {why}":
        "«{saved}» сейчас не подходит — рекомендуем «{suggested}»: {why}",
    "The displays have changed since last time — "
    "\"{suggested}\" is recommended: {why}":
        "Мониторы изменились с прошлого раза — рекомендуем «{suggested}»: {why}",
    "Your saved choice, \"{saved}\": {why}":
        "Сохранённый выбор «{saved}»: {why}",

    # -- a pane that will stay black ------------------------------------------
    "{name}: no source of frames — this pane will stay black.":
        "{name}: нет источника кадров — эта панель останется чёрной.",
    "{name}: the source of frames has no videoFrameChanged signal "
    "— this pane will stay black.":
        "{name}: источник кадров без сигнала videoFrameChanged "
        "— эта панель останется чёрной.",

    # -- the opening dialog ---------------------------------------------------
    "This recording has two screens": "В записи два экрана",
    "This recording has {count} screens": "В записи {count} {word}",
    "How should they be shown?": "Как их показать?",
    "Recommended": "Рекомендуется",
    "{count} monitor available": "Доступен {count} монитор",
    "{count} monitors available": "Доступно {count} монитора",
    "Remember my choice": "Запомнить мой выбор",
    "You can change this while watching": "Можно изменить во время просмотра",
    "Cancel": "Отмена",
    "Open": "Открыть",
    "Needs a second monitor": "Нужен второй монитор",
    "Your displays have changed since this choice was made.":
        "Конфигурация мониторов изменилась с момента последнего выбора.",

    # -- the player's own chrome ----------------------------------------------
    "Arrange": "Расположение",
    "Swap the screens": "Поменять местами",
    "Main": "Основной",
    "Screen {number}": "Экран {number}",
    "{count} video streams": "{count} видеопотока",
    "Playing in step": "Синхронное воспроизведение",
    "Lined up by hand": "Сведены вручную",
    "Even them up": "Выровнять",
    "Show the second screen": "Показать второй экран",
    "Restore the arrangement": "Вернуть раскладку",
    "Seeking…": "Перемотка…",
    "{name} — the recording ends here": "{name} — запись закончилась",

    "Play (Space)": "Воспроизведение (пробел)",
    "Pause (Space)": "Пауза (пробел)",
    "One frame back (Left)": "Кадр назад (влево)",
    "One frame on (Right)": "Кадр вперёд (вправо)",
    "Playback speed - click to cycle": "Скорость — нажмите, чтобы сменить",
    "Close the player (Esc)": "Закрыть плеер (Esc)",
    "Mute": "Выключить звук",
    "Full screen": "Во весь экран",
    "Scrub": "Перемотка",
    "Screen {number} - primary": "Экран {number} — основной",
    # The volume button says the whole sentence in one entry per state, not a verb
    # glued to a tail: Russian would not let the two halves be ordered freely. The
    # space before the sign is the Russian rule, as in "{percent}%" above.
    "Mute - volume {percent}%, scroll here to change it":
        "Выключить звук — громкость {percent} %, меняется колесом",
    "Unmute - volume {percent}%, scroll here to change it":
        "Включить звук — громкость {percent} %, меняется колесом",

    # -- opening a recording: what `player/open.py` says when it cannot --------
    # Every one of these is shown in a message box under the shipped launcher,
    # which has no console - see that module's docstring for why the faults it
    # reports about itself are deliberately left in English.
    "ScreenRec Player - plays a multi-screen recording as one thing.":
        "Плеер ScreenRec — показывает запись нескольких экранов как одно целое.",
    "a .screenrec.json index, a video file, or a folder.":
        "индекс .screenrec.json, видеофайл или папка.",
    "With no path, the newest recording in the output folder is opened.":
        "Без пути открывается самая свежая запись в папке вывода.",
    "There is no recording in {folder}.": "В {folder} нет записей.",
    "There is no file at {path}.": "Файла {path} нет.",
    "{path} is not a file.": "{path} — это не файл.",
    "{name} could not be read: {detail}": "{name} не читается: {detail}",
    "{name} is not something the player can open. It opens a ScreenRec "
    "index ({index}) or a video file ({videos}).":
        "{name} плеер открыть не может. Он открывает индекс ScreenRec "
        "({index}) или видеофайл ({videos}).",
    "No recording was named.": "Запись не указана.",
    "{path} could not be opened.": "{path} не открывается.",
    "{path} could not be opened: {detail}": "{path} не открывается: {detail}",
    "ScreenRec Player takes a path, not {flags}.":
        "Плееру ScreenRec нужен путь, а не {flags}.",
    "Only the first path is opened; ignoring {paths}":
        "Открывается только первый путь; остальные пропущены: {paths}",

    # -- words with two senses ------------------------------------------------
    "layout.screen": "экран",
    "monitor.screen": "монитор",
    "plural.screen.one": "экран",
    "plural.screen.few": "экрана",
    "plural.screen.many": "экранов",
    "plural.monitor.one": "монитор",
    "plural.monitor.few": "монитора",
    "plural.monitor.many": "мониторов",
}
