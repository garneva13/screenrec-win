"""The Russian block for the chrome area. See `translations/__init__.py`.

The chrome is the recorder's own windows: the bar, the tray icon, the countdown ring
and the source picker. The key is the English source text, exactly as it is written at
the call site. A key may appear in exactly one block; the merge says so out loud if it
does not.

Keys with a dotted prefix are `i18n.pick()` keys. Three of them are here for a reason
worth stating: "Microphone", "System sound" and "Frame rate" are the bar's tooltips and
*also* the settings panel's row labels, and the panel's words live in another block. A
key cannot be in two blocks, so the bar asks for its own copy by key.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- the bar ---------------------------------------------------------------
    "Choose what to record": "Что записывать",
    "Choose what to record…": "Что записывать…",
    "Cursor and clicks": "Курсор и клики",
    "Microphone level": "Уровень микрофона",
    "Format and quality": "Формат и качество",
    "Settings": "Настройки",
    "Settings…": "Настройки…",
    "Open the output folder": "Открыть папку с записями",
    "No output folder set": "Папка не выбрана",
    "Last file": "Последний файл",
    "Nothing recorded yet": "Записей пока нет",
    "Watch the last recording": "Посмотреть последнюю запись",
    "Start or stop from anywhere": "Старт и стоп из любой программы",
    "Quit ScreenRec": "Выйти из ScreenRec",

    # -- recording -------------------------------------------------------------
    "Start recording": "Начать запись",
    "Stop recording": "Остановить запись",
    "Stop and discard": "Остановить и удалить",
    "Pause": "Пауза",
    "Carry on recording": "Продолжить запись",
    "Starting in {count}… click to cancel":
        "Старт через {count}… нажмите, чтобы отменить",
    "{size} MB · {fps} fps": "{size} МБ · {fps} к/с",
    "{count} dropped": "пропущено {count} {word}",
    "recording starts": "до начала записи",

    # -- the tray icon ---------------------------------------------------------
    "Show the bar": "Показать панель",
    "ScreenRec — ready": "ScreenRec — готов",
    "ScreenRec — recording": "ScreenRec — идёт запись",
    "ScreenRec — paused": "ScreenRec — пауза",
    "ScreenRec — recording {clock}, {size} MB":
        "ScreenRec — запись {clock}, {size} МБ",
    "ScreenRec — paused {clock}, {size} MB": "ScreenRec — пауза {clock}, {size} МБ",
    "There is no system tray on this desktop, so ScreenRec has no icon there; "
    "quit it from the bar's right-click menu.":
        "На этом рабочем столе нет системного лотка, значка ScreenRec там не будет; "
        "выйти можно из меню по правому клику на панели.",

    # -- the source picker -----------------------------------------------------
    "Region": "Область",
    "Window": "Окно",
    "Window {handle}": "Окно {handle}",
    "Look again for screens and windows": "Обновить список экранов и окон",
    "No screens were reported by DXGI.": "DXGI не сообщила ни об одном экране.",
    "No windows big enough to record.":
        "Нет окон, подходящих по размеру для записи.",
    "All {count} screens": "Все {count} {word}",
    "one file each · {total} Mpx in total":
        "по файлу на экран · {total} Мпикс",
    "primary": "основной",
    "rotated": "повёрнутый",
    "Select an area…": "Выбрать область…",
    "Drag a rectangle anywhere on the desktop. Esc cancels the drag.":
        "Растяните прямоугольник в любом месте экрана. Esc отменяет.",
    "Use it again": "Использовать снова",
    "Last area  {width} × {height} at {x}, {y}":
        "Прошлая область  {width} × {height} в точке {x}, {y}",
    "There is no remembered area to use.": "Сохранённой области нет.",

    # -- what the picker has to say when something is wrong --------------------
    "The screens could not be listed: {reason}":
        "Не удалось получить список экранов: {reason}",
    "The window list could not be read: {reason}":
        "Не удалось прочитать список окон: {reason}",
    "Screen {number} is no longer connected. The list has been refreshed.":
        "Экран {number} больше не подключён. Список обновлён.",
    "“{name}” has closed since the list was made. The list has been refreshed — "
    "pick another window.":
        "Окно «{name}» уже закрыто. Список обновлён — выберите другое.",
    "The region selector is unavailable ({reason}).":
        "Выбор области недоступен ({reason}).",
    "The region could not be selected: {reason}":
        "Не удалось выбрать область: {reason}",
    "Thumbnails are unavailable ({reason}); the cards show icons instead.":
        "Миниатюры недоступны ({reason}); на карточках будут значки.",
    "ui.thumbnails has no ThumbnailLoader; the cards show icons instead.":
        "В ui.thumbnails нет ThumbnailLoader; на карточках будут значки.",
    "The thumbnail loader could not start: {reason}":
        "Загрузчик миниатюр не запустился: {reason}",
    "The thumbnail loader has no 'ready' signal.":
        "У загрузчика миниатюр нет сигнала 'ready'.",
    "The thumbnail loader has no 'request' method.":
        "У загрузчика миниатюр нет метода 'request'.",

    # -- words the settings panel also uses, and words with two senses ---------
    "bar.microphone": "Микрофон",
    "bar.system_sound": "Звук системы",
    "bar.frame_rate": "Частота кадров",
    "source.screen": "Экран",
    "source.screen_number": "Экран {number}",

    # -- plural forms ----------------------------------------------------------
    "plural.source.screen.one": "экран",
    "plural.source.screen.few": "экрана",
    "plural.source.screen.many": "экранов",
    "plural.chrome.frame.one": "кадр",
    "plural.chrome.frame.few": "кадра",
    "plural.chrome.frame.many": "кадров",
}
