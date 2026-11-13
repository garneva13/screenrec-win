"""The Russian block for the recorder area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.

The strings here are the odd ones out: they are not written at a call site at all.
`presets.BUILTIN` and `shellops.ACTIONS` are data tables, and the settings panel
translates them with `_(preset.name)` and `_(label)` - a variable, not a literal. So
`tools/check_translations.py` cannot see them, and nothing but
`tests/test_i18n_tables.py` keeps them in step with the tables.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- the first run: where recordings go --------------------------------------
    "Where should ScreenRec keep your recordings?":
        "\u0413\u0434\u0435 ScreenRec \u0431\u0443\u0434\u0435\u0442 \u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0432\u0430\u0448\u0438 \u0437\u0430\u043f\u0438\u0441\u0438?",
    "Recordings go to {folder}. Change it in the settings.":
        "\u0417\u0430\u043f\u0438\u0441\u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u044f\u044e\u0442\u0441\u044f \u0432 {folder}. \u041f\u043e\u043c\u0435\u043d\u044f\u0442\u044c \u043c\u043e\u0436\u043d\u043e \u0432 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430\u0445.",
    # -- presets.BUILTIN: the name and the note the panel shows under the combo ---
    "Lesson with voice": "Урок с голосом",
    "Microphone and system sound, cursor and clicks shown, three-second countdown.":
        "Микрофон и звук системы, курсор и клики видно, отсчёт три секунды.",
    "Silent demonstration": "Показ без звука",
    "No sound at all, no click rings - just the screen, and a small file.":
        "Совсем без звука, без кругов от кликов — только экран, и файл небольшой.",
    "Maximum quality": "Максимальное качество",
    (
        "H.265 4:4:4 at the best setting: sharp coloured text and the smallest file "
        "for it, but Windows' own player cannot open 4:4:4 — use VLC, mpv or an "
        "editor."
    ):
        "H.265 4:4:4 на лучшей настройке: чёткий цветной текст и самый маленький "
        "файл при этом, но стандартный проигрыватель Windows не открывает 4:4:4 — "
        "смотрите в VLC, mpv или в монтажной программе.",
    # -- shellops.ACTIONS: the "When it ends" combo ------------------------------
    "Do nothing": "Ничего не делать",
    "Open the video": "Открыть видео",
    "Show it in the folder": "Показать в папке",
    "Copy the file, ready to paste": "Скопировать файл — можно вставить",

    # -- ui/app.py: the controller's own words -----------------------------------
    # The bar's chip, the status line, its menus and its four dialogs. The chip and
    # the status line are re-worded on a language change by `Application._retranslate`
    # and `Application._say`; nothing else here outlives the click that shows it.
    "Monitors could not be listed: {reason}":
        "Не удалось получить список мониторов: {reason}",
    "No encoder on this machine could encode a test frame; recording "
    "will fail until that is sorted out.":
        "Ни один кодировщик на этой машине не смог закодировать пробный кадр; "
        "запись не пойдёт, пока это не исправить.",
    # The other half of the same answer: ffmpeg listed nothing usable, so no candidate
    # was even tried. Until 2026-11-05 that machine was told nothing at all.
    "ffmpeg named no encoder this program can use. It may be the wrong build, "
    "or it may not have run at all; recording will fail until that is sorted out.":
        "ffmpeg не назвал ни одного кодировщика, который эта программа умеет "
        "использовать. Возможно, это не та сборка, а возможно, он вообще не "
        "запустился; запись не пойдёт, пока это не исправить.",
    # Said when Quit is pressed during the probe. The click cannot be acted on there -
    # see `Application.quit` - so the program says that it heard it.
    "Closing as soon as the encoder check finishes.":
        "Закроюсь, как только закончится проверка кодировщиков.",
    # The probe's own three lines. It runs after the bar is on screen and takes
    # seconds on a machine it has not seen before, so it says where it has got to.
    # {name} is an ffmpeg encoder name and stays as ffmpeg spells it.
    "Checking what this machine can encode: {name} ({done} of {total})":
        "Проверяю, чем эта машина умеет кодировать: {name} ({done} из {total})",
    "Still checking what this machine can encode - one moment.":
        "Ещё проверяю, чем эта машина умеет кодировать — одну секунду.",
    # {reason} is `capabilities.choose`'s own English clause: it is built at run time
    # out of family names, so it cannot be a catalogue key. Only the frame around it
    # is translated, and that is deliberate - see `Application._note_encoder`.
    "Recording with {encoder} — {reason}":
        "Пишу кодировщиком {encoder} — {reason}",
    "Unfinished recording": "Незавершённая запись",
    # The Russian counts with a colon rather than with the noun, which is why it has
    # no {word}: "Незавершённых записей: 1" needs no agreement and "1 запись не
    # завершилась" would need three forms of the verb as well as of the noun.
    "{count} {word} did not finish - the segments are still in {folder}."
    "\n\nAssemble them into playable files now?":
        "Незавершённых записей: {count}. Сегменты остались в {folder}."
        "\n\nСобрать их в готовые файлы?",
    "Recovered {count} {word}.": "Восстановлено {count} {word}.",
    "The {name} hotkey would not stop: {reason}":
        "Горячая клавиша «{name}» не отключилась: {reason}",
    "The {name} hotkey was not registered: {reason}":
        "Горячая клавиша «{name}» не зарегистрирована: {reason}",

    # -- the source chip ---------------------------------------------------------
    "Region {width}×{height}": "Область {width}×{height}",
    "Choose a region": "Выбрать область",
    "Choose a window": "Выбрать окно",
    "No screen": "Нет экрана",

    # -- the quick pills and their menus -----------------------------------------
    # One unit for frames a second everywhere it is written: the settings panel's
    # spin box, this pill, its menu and the strip shown while recording.
    "{rate} fps": "{rate} к/с",
    "No microphone found": "Микрофон не найден",
    "{name}: {count} {word} changed.": "{name}: изменено {count} {word}.",

    # -- the status line ---------------------------------------------------------
    "Countdown cancelled.": "Отсчёт отменён.",
    "Choose something to record first.": "Сначала выберите, что записывать.",
    "Recording to {name}": "Пишем в {name}",
    "There is only one screen to record.": "Экран для записи всего один.",
    "Recording {count} {word} to {pattern}": "Пишем {count} {word} в {pattern}",
    "Saved {count} {word} — {size} MB. "
    "Click the file name below to watch them together.":
        "Сохранено {count} {word} — {size} МБ. "
        "Нажмите имя файла внизу, чтобы посмотреть их вместе.",
    "Recording again.": "Запись продолжается.",
    "Paused - the clock has stopped.": "Пауза — часы остановлены.",
    "Stopped automatically after {count} {word}.":
        "Остановлено автоматически через {count} {word}.",
    "Saved {name} — {size} MB": "Сохранено: {name} — {size} МБ",
    "Recording discarded.": "Запись удалена.",
    "Recording stopped on its own.": "Запись остановилась сама.",
    # The three lines of ffmpeg's log that follow it, each given its turn on the bar by
    # `Application._say_each`. The line itself is ffmpeg's English and stays as ffmpeg
    # wrote it - like an encoder name; the frame around it says whose English it is.
    "ffmpeg: {line}": "ffmpeg: {line}",
    # Said when the rectangle is below the chosen encoder's floor - see
    # `config.MIN_FRAME` for the measurement and `Application._encoder_for_frame` for
    # why it is recorded on the processor instead of being refused. {encoder} is an
    # ffmpeg encoder name and stays as ffmpeg spells it.
    "{width}×{height} is smaller than {encoder} will encode — "
    "recording it on the processor instead.":
        "{width}×{height} меньше, чем возьмёт {encoder}, — "
        "записываю на процессоре.",
    # And the same when there is no working processor encoder to move it to.
    "{width}×{height} is smaller than {encoder} will encode, and no "
    "encoder here can take it. Choose a bigger region.":
        "{width}×{height} меньше, чем возьмёт {encoder}, и на этой машине нет "
        "кодировщика, который возьмёт такой кадр. Выберите область побольше.",
    "There is nothing recorded in that folder yet.":
        "В этой папке пока ничего не записано.",
    "That recording could not be opened.": "Эту запись не удалось открыть.",
    "The player would not open: {reason}": "Плеер не открылся: {reason}",
    "Nothing recorded yet.": "Записей пока нет.",
    "The capture frame would not close: {reason}":
        "Рамка записи не закрылась: {reason}",
    "Click highlighting is off: {reason}":
        "Подсветка кликов выключена: {reason}",
    "The click overlay would not shut down: {reason}":
        "Слой подсветки кликов не выключился: {reason}",
    "The drawing tools are unavailable: {reason}":
        "Инструменты рисования недоступны: {reason}",
    "The drawing tools could not start: {reason}":
        "Инструменты рисования не запустились: {reason}",
    "The drawing tools would not close: {reason}":
        "Инструменты рисования не закрылись: {reason}",

    # -- the dialogs -------------------------------------------------------------
    "Throw it away?": "Удалить запись?",
    "Delete this recording and every screen in it?":
        "Удалить эту запись вместе со всеми экранами?",
    "Delete this recording and everything in it?":
        "Удалить эту запись целиком?",
    "Still recording": "Идёт запись",
    "A recording is running. Stop it and quit?":
        "Запись ещё идёт. Остановить её и выйти?",

    # -- overlay.py: the only sentence on a dimmed desktop -----------------------
    "Drag to choose a region    •    Enter accepts    •    Esc cancels":
        "Потяните, чтобы выбрать область    •    Enter — принять    •    Esc — отмена",

    # -- storage.py: how long the disk will last ---------------------------------
    "{free} GB free — about {duration}": "Свободно {free} ГБ — примерно {duration}",
    "an unknown amount of recording": "неизвестно сколько записи",
    "more than a day of recording": "больше суток записи",
    "less than a minute of recording": "меньше минуты записи",
    "{hours} h {minutes} min of recording": "{hours} ч {minutes} мин записи",
    "{hours} h of recording": "{hours} ч записи",
    "{minutes} min of recording": "{minutes} мин записи",
    "Free space on {path} is unreadable: {reason}":
        "Не удалось узнать, сколько свободно на {path}: {reason}",
    "Only {free} GB left — about {duration}. "
    "The recording will stop when the disk fills.":
        "Осталось всего {free} ГБ — примерно {duration}. "
        "Запись остановится, когда диск заполнится.",
    "{free} GB left — about {duration}.": "Осталось {free} ГБ — примерно {duration}.",

    # -- recorder.py: what the engine refuses ------------------------------------
    "A recording is already running.": "Запись уже идёт.",
    "That recording is already running.": "Эта запись уже идёт.",
    "That recording was not paused.": "Эта запись не была на паузе.",
    "Nothing is recording.": "Сейчас ничего не записывается.",
    "Output folder could not be created: {reason}":
        "Не удалось создать папку для записи: {reason}",
    "ffmpeg did not stop on request and had to be killed; the file may "
    "be unplayable.":
        "ffmpeg не остановился по запросу, пришлось его снять; файл может "
        "не открыться.",
    "ffmpeg exited with code {code}.": "ffmpeg завершился с кодом {code}.",
    "Some of the recording could not be deleted - {details}":
        "Часть записи удалить не удалось — {details}",
    "The recording could not be set up: {reason}":
        "Не удалось настроить запись: {reason}",
    "The recording could not be set up.": "Не удалось настроить запись.",
    "System sound was recorded, but it was silent from start to finish - "
    "check which playback device is being recorded.":
        "Звук системы записан, но от начала до конца он оказался тишиной — "
        "проверьте, с какого устройства воспроизведения идёт запись.",
    "No system sound has arrived yet. Check which playback device is "
    "being recorded - the recording is otherwise fine.":
        "Звук системы пока не пришёл. Проверьте, с какого устройства "
        "воспроизведения идёт запись — в остальном запись в порядке.",
    # Where the sound went while a recording was running.
    "Sound moved to “{name}”; the recording moved with it.":
        "Звук перешёл на «{name}» — запись перешла следом.",
    "Sound moved to “{name}”, which this recording cannot follow. "
    "Restart ScreenRec to record it.":
        "Звук перешёл на «{name}», а за ним эта запись пойти не может. "
        "Перезапустите ScreenRec, чтобы записывать с него.",
    "The sound is going to “{where}”, but “{here}” is being recorded.":
        "Звук идёт в «{where}», а записывается «{here}».",
    "System sound was on but no audio arrived.":
        "Звук системы был включён, но звука не пришло.",

    # -- system sound that could not be had at all ------------------------------
    # The take goes ahead without it rather than not happening; these sentences say
    # so at the start and again when the file is saved. They come from `audio.py`,
    # which is Qt-free and cannot translate itself, so `ui/app.py` matches them by
    # an `audio.REASON_*` code - see `Application._audio_reason`.
    "Windows is not offering a playback device that can be recorded, so this "
    "recording has no system sound - the picture and the microphone are not "
    "affected. A playback device has usually just gone away, a headset switching "
    "off for instance. Choose one in Settings, Sound.":
        "Windows не предлагает ни одного устройства воспроизведения, с которого "
        "можно записывать, поэтому в этой записи нет звука системы — картинка и "
        "микрофон не затронуты. Обычно это значит, что устройство только что "
        "пропало: например, выключилась гарнитура. Выберите устройство в "
        "настройках, в разделе «Звук».",
    "System sound cannot be captured: the sound library is missing from this copy "
    "of ScreenRec. The picture and the microphone are not affected.":
        "Звук системы записать нечем: в этой копии ScreenRec нет звуковой "
        "библиотеки. Картинка и микрофон не затронуты.",
    "System sound did not start in time, so this recording has no system sound - "
    "the picture and the microphone are not affected. Start the recording again, "
    "or switch system sound off.":
        "Звук системы не успел запуститься, поэтому в этой записи его нет — "
        "картинка и микрофон не затронуты. Запустите запись заново или выключите "
        "звук системы.",
    "The channel ScreenRec uses for system sound is held by something else, so "
    "this recording has no system sound. Close any other copy of ScreenRec; if "
    "there is none, restart ScreenRec.":
        "Канал, через который ScreenRec берёт звук системы, занят чем-то другим, "
        "поэтому в этой записи звука системы нет. Закройте другую копию "
        "ScreenRec; если её нет — перезапустите ScreenRec.",
    "The channel for system sound could not be opened, so this recording has no "
    "system sound. Restart ScreenRec, or record with system sound switched off.":
        "Канал для звука системы не открылся, поэтому в этой записи его нет. "
        "Перезапустите ScreenRec или пишите с выключенным звуком системы.",
    "Capturing system sound failed, so this recording has no system sound - the "
    "picture and the microphone are not affected.":
        "Захват звука системы не удался, поэтому в этой записи его нет — картинка "
        "и микрофон не затронуты.",
    "The playback device chosen for system sound was not available, so the "
    "current default one was recorded instead.":
        "Выбранное для звука системы устройство воспроизведения оказалось "
        "недоступно, поэтому записано текущее устройство по умолчанию.",
    # A session that claimed to be recording with nothing behind it. It used to
    # swallow every press of the record button in silence - see `_clear_the_way`.
    "The last recording did not end cleanly. Starting a new one.":
        "Прошлая запись завершилась некорректно. Начинаю новую.",
    # The settings changed while the recording was paused, so the segments cannot be
    # joined. The whole sentence is the key: it used to be written in English into
    # "The recording could not be set up: {reason}" above, which left a Russian
    # reader a Russian clause and then the only advice in the message in English.
    "This recording is {current} and cannot continue as {wanted}. Stop it to save "
    "what has been recorded, then start a new one with the new settings.":
        "Эта запись — {current}. Продолжить её как {wanted} нельзя. Остановите её, "
        "чтобы сохранить записанное, и начните новую с новыми настройками.",
    # What fills {current} and {wanted}: the encoder name, the frame size and the
    # pixel format stay as ffmpeg writes them.
    "{video} with one sound track": "{video}, одна звуковая дорожка",
    "{video} with {count} sound tracks": "{video}, звуковых дорожек: {count}",
    "{video} with no sound": "{video}, без звука",

    # -- takes.py: the join, and the recovery of a session that died --------------
    "Nothing was recorded - no segment reached the disk.":
        "Записывать нечего — на диск не попал ни один фрагмент.",
    "Part of the recording could not be read, and joining it would quietly leave "
    "that part out: {names}. Every segment is still in {folder}, and the ones that "
    "are whole still play.":
        "Часть записи не читается, и при склейке она молча пропала бы: {names}. "
        "Все фрагменты остались в {folder}, и целые из них открываются.",
    "The recording's settings changed while it was paused, so its parts cannot be "
    "joined without re-encoding it: {details}. Every segment is still in {folder} "
    "and holds what was recorded.":
        "Настройки записи изменились во время паузы, поэтому её части нельзя "
        "склеить без перекодирования: {details}. Все фрагменты остались в {folder} "
        "и содержат то, что было записано.",
    "The recording could not be assembled: {reason}. The segments are still in "
    "{folder} and can be joined by hand.":
        "Собрать запись не удалось: {reason}. Фрагменты остались в {folder} — их "
        "можно склеить вручную.",
    "There is nothing to recover.": "Восстанавливать нечего.",

    # -- multi.py: one ffmpeg per screen -----------------------------------------
    "screen {index}: {problem}": "экран {index}: {problem}",
    "No screen could be recorded.": "Не удалось записать ни одного экрана.",
    "The recording's index could not be written: {reason}":
        "Не удалось сохранить список файлов записи: {reason}",
    "{name} is not a ScreenRec recording index.":
        "{name} — это не индекс записи ScreenRec.",
    "{name} lists no screens.": "В {name} не перечислено ни одного экрана.",
    "These files are named in the index but are not beside it: {files}":
        "Эти файлы указаны в индексе, но рядом с ним их нет: {files}",

    # -- ui/captureframe.py: why there is no red outline -------------------------
    "No monitor was given, so there is nothing to draw a frame on.":
        "Монитор не передан — рамку рисовать не на чем.",
    "The rectangle {rect} is not four numbers: {reason}":
        "В прямоугольнике {rect} не четыре числа: {reason}",
    "The rectangle {rect} does not overlap monitor {index} ({screen}), "
    "so there is nothing to frame.":
        "Прямоугольник {rect} не пересекается с монитором {index} ({screen}) — "
        "обводить нечего.",
    "Monitor {index} is being recorded whole, and this build of Windows "
    "will not keep the frame out of the capture (it needs Windows 10 "
    "version 2004), so no frame is drawn - one would be in every frame "
    "of the recording.":
        "Монитор {index} пишется целиком, а эта сборка Windows не умеет прятать "
        "рамку от захвата (нужна Windows 10 версии 2004), поэтому рамка не "
        "рисуется — иначе она была бы в каждом кадре записи.",

    # -- words with two senses, and the plural forms -----------------------------
    # The three hotkeys are named in the warnings above; the English falls back to
    # the internal name, which is the word the English sentences already used.
    "hotkey.record": "старт/стоп",
    "hotkey.pause": "пауза",
    "hotkey.annotate": "рисование",
    "plural.recording.one": "запись",
    "plural.recording.few": "записи",
    "plural.recording.many": "записей",
    # Accusative: the only sentence using these is "через {count} {word}", and
    # Russian makes that "через 1 минуту" - the dictionary form is wrong there.
    "plural.minute.one": "минуту",
    "plural.minute.few": "минуты",
    "plural.minute.many": "минут",
    "plural.setting.one": "настройка",
    "plural.setting.few": "настройки",
    "plural.setting.many": "настроек",
}
