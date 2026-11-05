"""The Russian block for the player's clock. See `translations/__init__.py`.

`player/engine.py` is the one part of the player that had no Russian at all. It is not
a window and it shows nothing itself, which is how it was missed: every sentence here
leaves it on the `problem` signal, and `PlayerStage._engine_said` and
`PlayerWindows._report` put that sentence straight onto the message strip a Russian
viewer is reading. `check_translations.py` could not see the gap either, because it
follows `_()` and `pick()` and there were none in that file.

Two families, and they read differently on purpose.

**Reading an index.** These are returned as notes, not shown as failures: a set whose
second file has been deleted still opens, with the screens that are there, and the note
says which file is gone. So they name the file and say what was done about it, in that
order - «{name} нет на месте, поэтому…» rather than «ошибка».

**While playing.** These are the engine giving up on something: a file it cannot keep
in step, a clock that has stopped moving, a sound output that is not there. Each one
says what is happening *now* and what the player is doing instead, because the picture
on screen does not explain itself and a viewer who is told nothing assumes the program
has hung.

Two sentences carry text that is not ours: `{detail}` in «{name}: {detail}» and in
«{name} не воспроизводится ({detail})» is Qt's own error text, which Qt writes in the
language Windows is in. Wrapping them is still worth it - the half of the sentence that
is ours is translated and the punctuation is ours too.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- reading a `.screenrec.json` -----------------------------------------
    # "{name} lists no screens." is not here: `player_open` already translates
    # that sentence, and a key lives in exactly one block.
    "{name} is not a readable ScreenRec index: {detail}":
        "{name} — не читаемый индекс ScreenRec: {detail}",
    "{name} could not be read.": "{name} не читается.",
    "Named in the index but not there, so left out: {files}":
        "Указаны в индексе, но отсутствуют, поэтому не открыты: {files}",
    "None of the files named in {name} are there.":
        "Ни одного из файлов, указанных в {name}, нет на месте.",
    "The file carrying the sound ({named}) is not among the screens that loaded; "
    "{heir} will drive the others.":
        "Файла со звуком ({named}) нет среди открывшихся экранов; остальные "
        "поведёт {heir}.",

    # -- one entry of an index that is wrong ---------------------------------
    "Entry {position} in the index is not a screen and was ignored.":
        "Запись {position} в индексе — не экран, она пропущена.",
    "Entry {position} in the index names no file and was ignored.":
        "Запись {position} в индексе не называет файл, она пропущена.",
    "{name} has no usable desktop rectangle in the index and was ignored.":
        "У {name} в индексе нет пригодного прямоугольника рабочего стола, файл "
        "пропущен.",

    # -- §2.1, the offsets ---------------------------------------------------
    "{name} has an unreadable offset in the index; it is being played in step "
    "with the others.":
        "У {name} в индексе нечитаемое смещение; файл воспроизводится синхронно "
        "с остальными.",
    "{name} is slid {wanted} ms in the index, which is further than two "
    "recordings can be lined up by; {clamped} ms is being used.":
        "В индексе {name} сдвинут на {wanted} мс — дальше, чем можно свести две "
        "записи; используется {clamped} мс.",
    "{value} is not an offset in milliseconds.":
        "{value} — не смещение в миллисекундах.",
    "An offset of {wanted} ms is outside {least} - {most} ms; using {used} ms.":
        "Смещение {wanted} мс выходит за пределы {least} — {most} мс; "
        "используется {used} мс.",
    "There is no screen {index} in this recording to line up.":
        "В этой записи нет экрана {index}, который можно было бы подвинуть.",

    # -- opening the files ---------------------------------------------------
    "{name} is not there, so screen {monitor} will not play.":
        "{name} нет на месте, поэтому экран {monitor} не воспроизводится.",
    "The file carrying the sound could not be opened; screen {monitor} will "
    "drive the others and there will be no sound.":
        "Файл со звуком открыть не удалось; остальные поведёт экран {monitor}, "
        "звука не будет.",
    "Not one file of this recording could be opened.":
        "Ни один файл этой записи открыть не удалось.",

    # -- the transport -------------------------------------------------------
    "There is nothing to play: no file of this set opened.":
        "Воспроизводить нечего: ни один файл набора не открылся.",
    "There is nothing to seek: no file of this set opened.":
        "Перематывать нечего: ни один файл набора не открылся.",
    # §13.9's third: both files slid past the end leave a timeline 0 ms long.
    # The second sentence is the way out, and it is the half that matters.
    "Every file of this set is slid past the end of the timeline, so there is "
    "nothing to play. Reset the offsets to bring them back.":
        "Все файлы набора сдвинуты за конец шкалы, воспроизводить нечего. "
        "Обнулите смещения, чтобы вернуть их.",
    "{value} is not a playback speed.": "{value} — не скорость воспроизведения.",
    "A speed of {wanted}x is outside {least}x - {most}x; using {used}x.":
        "Скорость {wanted}× выходит за пределы {least}× — {most}×; используется "
        "{used}×.",

    # -- §1.2, which stream is heard -----------------------------------------
    "{value} is not a screen to take the sound from.":
        "{value} — не экран, с которого можно взять звук.",
    "There is no screen {index} in this recording to take the sound from.":
        "В этой записи нет экрана {index}, с которого можно взять звук.",
    "{name} did not open, so the sound cannot come from it.":
        "{name} не открылся, поэтому звук с него взять нельзя.",
    "There is no sound output on this machine to play the recording through.":
        "На этой машине нет звукового выхода, чтобы воспроизвести запись.",
    "This recording has no sound to mute.":
        "В этой записи нет звука, который можно приглушить.",
    "This recording has no sound to set the volume of.":
        "В этой записи нет звука, громкость которого можно менять.",
    "{value} is not a volume.": "{value} — не громкость.",

    # -- the clock giving up on something ------------------------------------
    "{name} is not playing: it has not moved in {seconds} s. The other screens "
    "are being left to run on their own.":
        "{name} не воспроизводится: не двигается {seconds} с. Остальные экраны "
        "идут сами по себе.",
    "{name} will not stay in step (it is {distance} ms from the sound after "
    "{tries} corrections); it is being left to run on its own.":
        "{name} не держит синхронность (после {tries} поправок он в {distance} мс "
        "от звука); файл оставлен идти сам по себе.",
    # Qt's own error text, in whatever language Windows is in, after our colon.
    "{name}: {detail}": "{name}: {detail}",
    "{name} could not be played ({detail}).":
        "{name} не воспроизводится ({detail}).",
    "{name} is not something this machine can play.":
        "{name} — не то, что эта машина умеет воспроизводить.",
    # `{why}` is one of the two sentences above, already translated, and this
    # adds what is being done about it.
    "{why} There is nothing else in this set to play.":
        "{why} Больше в этом наборе воспроизводить нечего.",
    "{why} Screen {monitor} will drive the others and there will be no sound.":
        "{why} Остальные поведёт экран {monitor}, звука не будет.",
}
