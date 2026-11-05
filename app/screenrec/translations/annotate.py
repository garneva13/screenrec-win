"""The Russian block for the annotate area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.

Three keys here translate to themselves - `"{value} px"`, `"{value} %"` and
`"{value} pt"`. They are in the catalogue all the same, because they are built from a
number at run time and the rule for that is a format call on a translated template: the
unit happens to be written the same way in both languages, and the day it is not, this
is where it changes.

Keys with a dotted prefix are `i18n.plural()` / `i18n.pick()` keys: Russian counts in
three forms, so "3 рисунка" and "5 рисунков" are two different words.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- the tools, as the toolbar and the shape popover name them ------------
    "Pen": "Карандаш",
    "Marker": "Маркер",
    "Line": "Линия",
    "Arrow": "Стрелка",
    "Rectangle": "Прямоугольник",
    "Ellipse": "Эллипс",
    "Text": "Текст",
    "Eraser": "Ластик",

    # -- the panel's own controls ---------------------------------------------
    "Drag to move the toolbar": "Потяните, чтобы переместить панель",
    "Colour": "Цвет",
    "Thickness": "Толщина",
    "Undo": "Отменить",
    "Redo": "Вернуть",
    "Erase everything": "Стереть всё",
    "Hide the drawings": "Скрыть рисунки",
    "Show the drawings": "Показать рисунки",
    "Collapse the toolbar": "Свернуть панель",
    "Expand the panel": "Развернуть панель",
    "Drawing - click to work with the application":
        "Рисование — нажмите, чтобы работать с программой",
    "Pointer - click to draw": "Курсор — нажмите, чтобы рисовать",

    # -- the popovers ----------------------------------------------------------
    "Shape": "Фигура",
    "Opacity": "Непрозрачность",
    "Text size": "Размер текста",
    "{value} px": "{value} px",
    "{value} %": "{value} %",
    "{value} pt": "{value} pt",

    # -- the text box ----------------------------------------------------------
    "ScreenRec - annotation text": "ScreenRec — текст примечания",
    "The monitor list could not be read ({error}), so the text box cannot be kept on "
    "screen and will open wherever it was asked to.":
        "Не удалось получить список мониторов ({error}), поэтому поле ввода нельзя "
        "удержать на экране — оно откроется там, где его попросили.",
    "The monitor list could not be re-read: {error}":
        "Не удалось перечитать список мониторов: {error}",
    "The monitor list is empty, so the text box cannot be kept on screen and will open "
    "wherever it was asked to.":
        "Список мониторов пуст, поэтому поле ввода нельзя удержать на экране — оно "
        "откроется там, где его попросили.",
    "There is no QApplication, so a click outside the text box cannot be noticed and "
    "will not commit it.":
        "Нет QApplication, поэтому щелчок вне поля ввода останется незамеченным и не "
        "завершит заметку.",
    "This build of Windows does not support WDA_EXCLUDEFROMCAPTURE (it needs Windows 10 "
    "2004 or newer), so the text box and its caret WILL appear in the recording while "
    "the note is being typed.":
        "Эта сборка Windows не поддерживает WDA_EXCLUDEFROMCAPTURE (нужна Windows 10 "
        "2004 или новее), поэтому поле ввода и курсор ПОПАДУТ в запись, пока "
        "набирается текст.",
    "{colour} is not a colour the text box understands; the note is being shown in "
    "white.":
        "{colour} — не тот цвет, который понимает поле ввода; заметка показана белым.",
    "Monitor {index} could not be paired with a Qt screen, so the text box is placed in "
    "physical pixels and will be misplaced on a scaled desktop.":
        "Монитор {index} не удалось сопоставить с экраном Qt, поэтому поле ввода "
        "размещено в физических пикселях и на масштабированном рабочем столе встанет "
        "не на место.",

    # -- what the controller says ----------------------------------------------
    "Drawing. The hotkey switches back to working with the application.":
        "Рисование. Горячая клавиша возвращает к работе с программой.",
    "Working with the application; the drawings stay on screen.":
        "Работа с программой; рисунки остаются на экране.",
    "There is no {tool} tool.": "Инструмента {tool} нет.",
    "Cleared {count} {word} - undo brings them back.":
        "Удалено {count} {word} — отмена вернёт всё обратно.",
    "Drawings hidden; they are not deleted.": "Рисунки скрыты, но не удалены.",
    "Drawings shown again.": "Рисунки снова видны.",
    "The pointer position is unreadable: {error}":
        "Положение курсора не читается: {error}",
    "This Windows version cannot hide a window from screen capture, so the drawing "
    "panel will appear in the recording. Collapse it, or move it to another screen.":
        "Эта версия Windows не умеет скрывать окно от захвата экрана, поэтому панель "
        "рисования попадёт в запись. Сверните её или перенесите на другой экран.",
    "The drawing layer would not release the mouse: {error}":
        "Слой рисования не отпустил мышь: {error}",
    "A drawing window would not close: {error}":
        "Окно рисования не закрылось: {error}",

    # -- one word, three Russian forms -----------------------------------------
    "plural.drawing.one": "рисунок",
    "plural.drawing.few": "рисунка",
    "plural.drawing.many": "рисунков",
}
