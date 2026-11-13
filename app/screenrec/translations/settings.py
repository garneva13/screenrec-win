"""The Russian block for the settings area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.

This block owns the words of `ui/settings_panel.py` **and** the user-facing words
defined in `config.py` - the four format labels, their notes and the four quality
levels. Those live in `config.py` as data and are translated where they are read, with
`_()`; anywhere else that shows a `config.Profile`'s label uses these same keys rather
than adding its own.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- the panel's title and its sections -----------------------------------
    "Recording settings": "Настройки записи",
    "Close": "Закрыть",
    "Language": "Язык",
    "Preset": "Пресет",
    "Video": "Видео",
    "Audio": "Звук",
    "More": "Ещё",
    "Output": "Куда сохранять",
    "Hotkey": "Горячие клавиши",

    # -- the row labels -------------------------------------------------------
    "Use": "Выбрать",
    "Frame rate": "Частота кадров",
    "Size": "Размер",
    # -- the camera -------------------------------------------------------
    "Camera": "Камера",
    "Camera in the corner": "Камера в углу",
    "The camera is drawn on the screen itself, so it reaches the recording "
    "the way the click rings do - and so you can see it too while you record.":
        "Камера рисуется прямо на экране, поэтому попадает в запись так же, как кольца "
        "от кликов, — и поэтому во время записи вы видите её тоже.",
    "Which camera": "Какая камера",
    "First one found": "Первая найденная",
    "Corner": "Угол",
    "Camera size": "Размер камеры",
    "Camera shape": "Форма камеры",
    "Rounded rectangle": "Прямоугольник со скруглением",
    "Circle": "Круг",
    "Camera resolution": "Разрешение камеры",
    "Choose for me": "Подобрать автоматически",
    # The camera's own mode: the frame rate is on the label because the biggest mode
    # is not always the best one - this camera's largest run at 2 fps.
    "{width}×{height} · {fps} fps": "{width}×{height} · {fps} к/с",
    "Top left": "Сверху слева",
    "Top right": "Сверху справа",
    "Bottom left": "Снизу слева",
    "Bottom right": "Снизу справа",
    "Tiny": "Крошечная",
    "Small": "Маленькая",
    "Medium": "Средняя",
    "Large": "Большая",
    "No camera was found on this machine.": "На этом компьютере камера не найдена.",
    "The camera list could not be read: {reason}":
        "Не удалось получить список камер: {reason}",
    "As on screen": "Как на экране",
    # A height, the way a person says it: «1080p». The number is substituted, so the
    # string is one entry rather than one per size.
    "{height}p": "{height}p",
    "This screen is {screen}p, so nothing is scaled and nothing is saved.":
        "Этот экран — {screen}p, поэтому ничего не уменьшается и ничего не экономится.",
    "Scaled down while recording. That costs a copy through system memory, "
    "and the frame rate held when it was measured.":
        "Уменьшается прямо при записи. Это стоит одной копии через оперативную память, "
        "и частота кадров при замере не просела.",
    "Format": "Формат",
    "Quality": "Качество",
    "Microphone": "Микрофон",
    "System sound": "Звук системы",
    "Tracks": "Дорожки",
    "Show the cursor": "Показывать курсор",
    "Highlight clicks": "Подсвечивать клики",
    "Capture frame": "Рамка записи",
    "Floating bar": "Плавающая панель",
    "Countdown": "Обратный отсчёт",
    "Stop after": "Остановить через",
    "When it ends": "После записи",
    "Start / stop": "Старт / стоп",
    "Pause / carry on": "Пауза / продолжить",
    "Draw / work": "Рисовать / работать",

    # -- what the lists offer -------------------------------------------------
    "Custom": "Свои настройки",
    "Off": "Выкл.",
    "Default playback device": "Устройство по умолчанию",
    "Default playback device ({name})": "Устройство по умолчанию ({name})",
    "{name} (not found)": "{name} (не найден)",
    "Device {index} (not found)": "Устройство {index} (не найдено)",
    # The playback device that was chosen is not on the machine right now. The choice
    # is kept, so it comes back on its own when the device does; meanwhile the
    # default is recorded, which is what this says.
    "“{name}” is not available, so the default playback device is being recorded.":
        "«{name}» недоступно, поэтому записывается устройство воспроизведения по "
        "умолчанию.",
    "Mixed into one": "Смешать в одну",
    "Two separate": "Две отдельные",
    "None": "Нет",
    "Never": "Никогда",
    " fps": " к/с",
    " s": " с",
    " min": " мин",

    # -- the formats and the quality levels, defined in `config.py` -----------
    "Standard — H.265": "Обычный — H.265",
    "Sharp text — H.265 4:4:4": "Чёткий текст — H.265 4:4:4",
    "Most compatible — H.264": "Самый совместимый — H.264",
    "No GPU — x264": "Без видеокарты — x264",
    "Plays in Windows, VLC and every editor. The default.":
        "Играется в Windows, VLC и любом редакторе. По умолчанию.",
    "Keeps every colour sample, so small red and blue text stays crisp, and is "
    "the smallest of the lot. Needs VLC, mpv or a video editor — Windows' "
    "own player does not decode 4:4:4.":
        "Сохраняет цвет целиком, поэтому мелкий красный и синий текст остаётся "
        "чётким, а файл получается самым лёгким. Нужен VLC, mpv или "
        "видеоредактор — встроенный плеер Windows 4:4:4 не открывает.",
    "Opens anywhere at all, and is the largest for the quality.":
        "Открывается где угодно, но при том же качестве весит больше всех.",
    "For a machine with no NVENC. Costs CPU that the recording may need.":
        "Для машины без NVENC. Съедает процессор, который может понадобиться "
        "самой записи.",
    "Best": "Лучшее",
    "High": "Высокое",
    "Balanced": "Среднее",
    "Small file": "Небольшой файл",

    # -- the lines under the controls -----------------------------------------
    "Everything changes at once. The player is a separate program and takes "
    "the new language the next time it opens.":
        "Всё меняется сразу. Плеер — отдельная программа, он возьмёт новый "
        "язык при следующем запуске.",
    "Two tracks need both the microphone and the system sound.":
        "Для двух дорожек нужны и микрофон, и звук системы.",
    "A device is remembered by name, so it is still the one chosen after the "
    "computer's devices change. While it is away the default is recorded, and a "
    "device that cannot be opened never stops a recording: the screen and the "
    "microphone are recorded anyway.":
        "Устройство запоминается по имени, поэтому остаётся выбранным и после того, "
        "как набор устройств на компьютере изменился. Пока его нет, записывается "
        "устройство по умолчанию, а устройство, которое не открылось, никогда не "
        "срывает запись: экран и микрофон всё равно записываются.",
    "No microphone was found on this machine.":
        "Микрофон на этом компьютере не найден.",
    "Estimated {rate} MB a minute at {width}×{height} ({gigabytes} GB an "
    "hour) on a busy screen - much less on a still one.":
        "Примерно {rate} МБ в минуту при {width}×{height} ({gigabytes} ГБ в час) "
        "на активном экране — на спокойном намного меньше.",
    # The same line once this machine has recorded something: `storage.size_estimate`
    # replaces the table's figure with the median of what was really written here, so
    # the sentence stops saying "estimated". {encoder} is ffmpeg's name and stays.
    "About {rate} MB a minute at {width}×{height} ({gigabytes} GB an "
    "hour) - measured on this machine, from what {encoder} has already "
    "written.":
        "Около {rate} МБ в минуту при {width}×{height} ({gigabytes} ГБ в час) — "
        "измерено на этом компьютере по тому, что уже записал {encoder}.",

    # -- tool tips and the folder dialog --------------------------------------
    "Choose where recordings are saved": "Выбрать папку для записей",
    "Where should recordings go?": "Куда сохранять записи?",
    "A thin red outline round what is being recorded, on every screen in "
    "the set. It stays on your screen and never reaches the file.":
        "Тонкая красная рамка вокруг того, что пишется, на каждом экране набора. "
        "Она видна только на экране и в файл не попадает.",
    "Stop on its own after this long - for a process you start and walk "
    "away from":
        "Остановится сама через это время — для дела, которое запустил и ушёл",
    "Switches between drawing on the screen and working with the application":
        "Переключает между рисованием поверх экрана и работой в программе",

    # -- what the panel says about a hotkey it cannot use ---------------------
    "“{text}” is not a shortcut this understands. "
    "Try something like {placeholder}.":
        "«{text}» — не похоже на сочетание клавиш. "
        "Попробуйте что-нибудь вроде {placeholder}.",
    "{shown} is not a hotkey. Use a modifier and a key, like "
    "Ctrl+Alt+R - Ctrl, Alt, Shift or Win plus a letter, a digit or "
    "F1-F24. Still using {hotkey}.":
        "{shown} — не горячая клавиша. Нужен модификатор и клавиша, например "
        "Ctrl+Alt+R: Ctrl, Alt, Shift или Win плюс буква, цифра или F1-F24. "
        "Остаётся {hotkey}.",
    "An empty box is not a hotkey. Use a modifier and a key, like "
    "Ctrl+Alt+R - Ctrl, Alt, Shift or Win plus a letter, a digit or "
    "F1-F24. Still using {hotkey}.":
        "Пустое поле — не горячая клавиша. Нужен модификатор и клавиша, например "
        "Ctrl+Alt+R: Ctrl, Alt, Shift или Win плюс буква, цифра или F1-F24. "
        "Остаётся {hotkey}.",

    # -- which encoding path is in force, and the probe table behind it -------
    #
    # The one line under the format picker names the class of path and the codec and
    # never the vendor; the vendor is named in the expander and only there. The cost
    # figures are measured - see `ui/settings_panel.py` for the runs they come from -
    # so the Russian must carry the same numbers rather than round them.
    "Recorded by the graphics card in {codec}. The frame never leaves it, and "
    "ffmpeg costs about a twentieth of a processor core.":
        "Пишет видеокарта, {codec}. Кадр с неё не уходит, а ffmpeg съедает "
        "примерно двадцатую часть ядра процессора.",
    # The span, not a middle: five runs of the same command on this machine spread
    # from 46% of a core to 154.5%, and `ui/settings_panel.py` records all of them.
    "Recorded by the graphics card in {codec}, but every frame is copied "
    "through system memory on the way - between half a processor core and "
    "one and a half at 2560×1440 and 60 fps.":
        "Пишет видеокарта, {codec}, но по дороге каждый кадр копируется "
        "через оперативную память — от половины ядра процессора до полутора "
        "при 2560×1440 и 60 к/с.",
    # Three sentences about the processor, not one. The middle one is the case that
    # was being told "your hardware encoder does not work" while it worked.
    "No hardware encoder works on this machine, so {codec} is encoded by "
    "the processor - processor time the program you are recording may "
    "need.":
        "Аппаратный кодировщик на этом компьютере не работает, "
        "поэтому {codec} кодирует процессор — а его время может "
        "понадобиться записываемой программе.",
    "This machine's hardware encoders cannot record this format, so "
    "{codec} is encoded by the processor - processor time the program "
    "you are recording may need.":
        "Аппаратные кодировщики этого компьютера не умеют записывать этот "
        "формат, поэтому {codec} кодирует процессор — а его время может "
        "понадобиться записываемой программе.",
    "This format asks for the processor, so {codec} is encoded there "
    "whatever the graphics card can do - processor time the program "
    "you are recording may need.":
        "Этот формат просит процессор, поэтому {codec} кодируется на нём, "
        "что бы ни умела видеокарта, — а время процессора может "
        "понадобиться записываемой программе.",
    "No encoder on this machine takes 4:4:4, so this format cannot be "
    "recorded here.":
        "Ни один кодировщик на этом компьютере не принимает 4:4:4, "
        "поэтому здесь этот формат записать нельзя.",
    "Details": "Подробнее",
    # `{reason}` is ffmpeg's own sentence and stays English: it is evidence, and a
    # translated error message cannot be searched for.
    "{name} · {vendor} — works": "{name} · {vendor} — работает",
    "{name} · {vendor} — did not work: {reason}":
        "{name} · {vendor} — не заработал: {reason}",
    "no reason given": "причина не названа",

    # ---------------------------------------------------------------------
    # The window as it was rebuilt on 2026-11-08: a sidebar, five pages, and a
    # footer. The headings above belong to the single column it replaced and are
    # kept because a settings file is not the only thing that outlives a redesign.
    # ---------------------------------------------------------------------
    # "Settings" is the gear's tool tip and lives in the chrome block; the window's
    # own title reads the same key from there.
    "Done": "Готово",
    # -- the sidebar, and each page's own two lines -------------------------
    "Recording": "Запись",
    "Video and where files are saved": "Видео и сохранение файлов",
    "Sound": "Звук",
    "What is recorded besides the picture": "Что записывается кроме картинки",
    "A picture on top of the recording": "Изображение поверх записи",
    "Control": "Управление",
    "What is on the screen, and what starts it":
        "Что видно на экране и чем это запускается",
    "General": "Общие",
    "The program itself": "Сама программа",
    "About": "О программе",
    "Project page": "Страница программы",
    "Source code": "Исходный код",
    "That link would not open: {url}": "Ссылка не открылась: {url}",
    # -- group headings -----------------------------------------------------
    "Video settings": "Параметры видео",
    "Saving": "Сохранение",
    "Where it sits": "Положение в кадре",
    "While recording": "Во время записи",
    "Timers": "Таймеры",
    "Hotkeys": "Горячие клавиши",
    # -- field captions -----------------------------------------------------
    "Profile": "Профиль",
    "Resolution": "Разрешение",
    "Folder for recordings": "Папка для записей",
    "Open the folder": "Открыть папку",
    "Recordings cannot be written there: {reason}":
        "Записи туда не сохранить: {reason}",
    "Device": "Устройство",
    "Source": "Источник",
    "In the file": "В файле",
    "Position": "Положение",
    "Record the microphone": "Записывать микрофон",
    "Record what the computer plays": "Записывать звук компьютера",
    "Show the camera": "Показывать камеру",
    "Start / stop recording": "Начать / остановить запись",
    # -- the (i) buttons ----------------------------------------------------
    "What this means": "Что это значит",
    "The screen's own size, or scaled down while recording. A smaller file, "
    "and a copy through system memory to make it.":
        "Как на экране или с уменьшением прямо во время записи. Файл меньше, "
        "но за это платится копией кадра через оперативную память.",
    "The codec and what it keeps of the colour. H.265 is smaller, H.264 plays "
    "everywhere, and 4:4:4 keeps text sharp at the cost of size.":
        "Кодек и то, сколько он оставляет от цвета. H.265 меньше по размеру, "
        "H.264 играется везде, 4:4:4 сохраняет резкость текста ценой размера.",
    "How hard the encoder tries. It changes the size of the file far more "
    "than it changes what you can see.":
        "Насколько старается кодировщик. На размер файла это влияет гораздо "
        "сильнее, чем на то, что видно глазом.",
    "The size the camera itself runs at. The picture is a few hundred pixels "
    "wide, so the biggest mode is waste - and on this camera it is also the "
    "slowest.":
        "В каком разрешении работает сама камера. Окошко шириной в несколько "
        "сотен точек, поэтому самый большой режим — это трата впустую, а на "
        "этой камере он ещё и самый медленный.",
    # -- the timers ---------------------------------------------------------
    # "{rate} fps" is the bar's own label and lives in the recorder block.
    "No countdown": "Без отсчёта",
    "{seconds} s": "{seconds} с",
    "No limit": "Без ограничения",
    "{minutes} min": "{minutes} мин",
    # -- the shortcut boxes -------------------------------------------------
    "Press a shortcut, Esc to cancel": "Нажмите сочетание, Esc — отмена",
    "Click a box and press the keys. Esc leaves it as it was.":
        "Щёлкните по полю и нажмите клавиши. Esc оставляет как было.",
    "{shortcut} is already {name}. The shortcut is unchanged.":
        "{shortcut} уже занято: {name}. Сочетание осталось прежним.",
    "start and stop": "начать и остановить",
    "pause and carry on": "пауза и продолжить",
    "draw and work": "рисовать и работать",
    # -- the camera diagram -------------------------------------------------
    "Where the camera sits": "Где стоит камера",
    # -- the footer ---------------------------------------------------------
    "Changes are saved as you make them.": "Изменения сохраняются сразу.",
    "A recording is running. The picture and sound settings will be used by "
    "the next one.":
        "Идёт запись. Настройки картинки и звука вступят в силу со следующей.",
    # -- the estimate -------------------------------------------------------
    "≈ {rate} MB a minute at {width} × {height}":
        "≈ {rate} МБ в минуту при {width} × {height}",
    "{gigabytes} GB an hour. Measured on this machine, from what {encoder} "
    "has already written.":
        "{gigabytes} ГБ в час. Измерено на этой машине по тому, что уже "
        "записал {encoder}.",
    "{gigabytes} GB an hour on a busy screen - much less on a still one.":
        "{gigabytes} ГБ в час на активном экране — на спокойном намного меньше.",
}
