# ScreenRec 0.3.3

*Paste this into the GitHub release. The hash below is of the installer this repository
built on 2026-11-16; recompute it if you build again - `Get-FileHash` - because a
release whose hash does not match its file is worse than one with no hash at all.*

---

A screen recorder for Windows, and a player for what it records. Two programs, one
installer. This release is about one number: **7.5**.

## Recording at a smaller size was losing three frames in four

A user recorded their work in Unreal Engine at 1080p from a 1440p screen. The file came
back at **7.5 frames a second** where 30 had been asked for, and another at **8.4**
where 60 had. Three frames in four, and six in seven, were never captured at all.

Nothing in the program said a word, and that was not an oversight so much as a blind
spot: ffmpeg's dropped-frame counter counts frames it was handed and threw away. A
capture that is *starved* is handed nothing, throws nothing away, and reports a clean
recording. Every counter read zero.

The cause is the one thing that setting did. Capturing a whole screen, the picture
never leaves the graphics card: it goes from the capture straight into the encoder,
which is why recording costs so little. Asking for a smaller picture forced every
single frame **off** the card and into system memory, because no GPU scaler in this
build will accept those frames - and on a card busy drawing a 3D application, that copy
has to queue behind the application's own work.

**So it is not done during the recording any more.** The screen is captured at its own
size, untouched, and the file is made smaller afterwards in a pass of its own - on the
graphics card where that is possible. Measured: 1794 frames in, **1794 frames out**,
2560x1440 down to 1920x1080, taking 4.7 seconds for 30 seconds of recording. The whole
point is the asymmetry: a pass over a file that is too slow merely takes longer, while
a capture that is too slow loses what it could not keep up with.

You will see a line at the end of a recording saying it is being made smaller, with a
clock. A two-minute recording takes about twenty seconds. The record button waits until
it is done.

## The program now tells you when frames are missing

Two new lines with the saved file, because a figure that only exists while you are
recording is a figure nobody reads - the bar sits in a corner and is hidden from screen
capture, and "Saved…" wipes it the moment the take ends.

- **How many frames did not make it**, and what share of the recording that is.
- **What rate the recording really ran at**, when that is well below what was asked
  for. This is the one that would have caught the fault above, and it does not depend
  on any counter: it is the frames in the file against the seconds they cover.

If you ever see either line, the file is still whole and still plays - it is telling
you the machine could not keep up, not that something broke.

## Counters that survive a pause

A recording that was paused and resumed reported only the last part's lost frames. The
seconds and the megabytes beside them were sums over the whole take; these two were not,
so pausing quietly reset them to zero. They are sums now.

## Windows will warn you about this file. Here is why, and what to do

When you run the installer, Windows will show a blue screen saying

> **Windows protected your PC**
> Microsoft Defender SmartScreen prevented an unrecognised app from starting.

with one button, **Don't run**. To install anyway: click **More info**, then **Run
anyway**.

That warning is honest and you should not ignore warnings in general, so here is exactly
what it means. SmartScreen asks two questions about a program: is it signed, and does
that signature have a reputation. This installer is **not signed** - a code-signing
certificate costs a few hundred dollars a year and needs a hardware token, and this is a
free program given away by one person. Nothing is wrong with the file; Windows simply
has never seen it before and says so.

Signing is planned, through the [SignPath Foundation](https://signpath.org/), which signs
open-source projects without charge. It is not done yet. When it is, this paragraph will
say so.

Until then, check the file yourself rather than trusting this page:

```
SHA-256  01b65a9c730451508f7a007a0b367c70681d944fc017a69bb5a4af2ceda26511
size     139,306,590 bytes
```

```powershell
Get-FileHash .\ScreenRec-0.3.3-setup.exe -Algorithm SHA256
```

If that does not match, do not run it.

## Installing

Run the installer. It asks for administrator rights because it installs into
`C:\Program Files\ScreenRec` for everyone on the machine, asks where you would like your
recordings kept, and puts **ScreenRec** and **ScreenRec Player** in the Start menu.
Installing over an earlier version replaces it in place and leaves your settings and
your recordings alone. Uninstalling is in "Apps & features" as usual.

## What it runs on

Windows 10 version 2004 or later, 64-bit. Nothing else is needed: Python, Qt and ffmpeg
are all inside.

**Hardware encoding is NVIDIA-tested only.** The code supports AMD (AMF), Intel (QSV)
and Windows' own Media Foundation encoders, written from their documented options and
guarded so that an unsupported path falls back to the processor rather than failing -
but the author has one NVIDIA card and no way to run the others. If your machine takes
the processor path when it should not, that is worth a bug report, and the program's
settings will tell you which encoder it chose and why.

**The pass that makes a recording smaller stays on the card only on NVIDIA.** Elsewhere
it runs on the processor, which is slower and perfectly safe - it is a file by then, and
a slow pass cannot lose a frame.

**Devices plugged in after the program started are still a limit.** The audio library
enumerates devices once, so a headset connected while ScreenRec is running cannot be
recorded until ScreenRec is restarted. It no longer costs you a recording: the default
device is recorded instead and the program says so.

## Licence

GPL v3 - see `LICENSE`. That follows from the bundled ffmpeg, which carries x264,
because x264 is the only encoder that exists on every machine and a recorder that must
run anywhere cannot do without it.

ffmpeg is a separate program here, run as a subprocess: a command line and a pipe,
nothing linked. Its source, for the exact build in this release, is the second asset of
this release.

| | |
|---|---|
| `ScreenRec-0.3.3-setup.exe` | the installer |
| `ffmpeg-source-ec887b13a8.tar.gz` | the corresponding source for the bundled ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | hashes of both |

`THIRD-PARTY-NOTICES.md` in the repository lists everything else and under what terms.

---

# ScreenRec 0.3.3 (по-русски)

Программа записи экрана для Windows и плеер для того, что она записала. Две программы,
один установщик. Этот выпуск — про одно число: **7,5**.

## Запись в уменьшенном размере теряла три кадра из четырёх

Пользователь записывал свою работу в Unreal Engine в 1080p с экрана 1440p. В файле
оказалось **7,5 кадра в секунду** там, где просили 30, и **8,4** там, где просили 60.
Три кадра из четырёх и шесть из семи не были сняты вовсе.

Программа при этом молчала — и не по недосмотру, а по слепому пятну: счётчик
потерянных кадров у ffmpeg считает те кадры, которые ему дали и которые он выбросил.
Захвату, который **голодает**, не дают ничего, выбрасывать ему нечего, и он сообщает о
безупречной записи. Все счётчики читались нулями.

Причина — ровно в том, что делала эта настройка. При записи экрана целиком картинка не
покидает видеокарту: она идёт с захвата прямо в кодировщик, поэтому запись и стоит так
дёшево. Просьба об уменьшенной картинке заставляла **каждый** кадр уходить с карты в
оперативную память, потому что ни один масштабатор на видеокарте в этой сборке такие
кадры не принимает, — а на карте, занятой отрисовкой трёхмерного приложения, этот вывоз
встаёт в очередь за его работой.

**Поэтому во время записи этого больше не происходит.** Экран снимается в своём
размере, нетронутым, а файл уменьшается потом, отдельным проходом — на видеокарте там,
где это возможно. Измерено: 1794 кадра вошло, **1794 вышло**, 2560×1440 → 1920×1080,
4,7 секунды на 30 секунд записи. Вся суть в несимметричности: слишком медленный проход
по файлу просто идёт дольше, а слишком медленный захват теряет то, за чем не успел.

В конце записи вы увидите строку о том, что она уменьшается, и часы рядом. На
двухминутную запись уходит секунд двадцать. Кнопка записи на это время ждёт.

## Программа говорит, когда кадров не хватает

Две новые строки вместе с сохранённым файлом — потому что цифра, которая существует
только пока вы пишете, это цифра, которую никто не читает: панель сидит в углу, спрятана
от захвата экрана, и строка «Сохранено…» стирает её в тот же миг, когда запись кончается.

- **Сколько кадров не попало** в запись и какая это от неё доля.
- **С какой частотой запись шла на самом деле**, если она сильно ниже запрошенной.
  Именно эта строка поймала бы описанную выше поломку, и она не зависит ни от одного
  счётчика: это кадры в файле против секунд, которые они покрывают.

Если вы увидите любую из них — файл всё равно цел и воспроизводится. Она говорит, что
машина не успевала, а не что что-то сломалось.

## Счётчики переживают паузу

Запись, которую поставили на паузу и продолжили, сообщала потери только последней части.
Секунды и мегабайты рядом с ними были суммами по всей записи, а эти два — нет, поэтому
пауза молча обнуляла их. Теперь это тоже суммы.

## Windows предупредит об этом файле. Почему и что делать

При запуске установщика Windows покажет синее окно:

> **Система Windows защитила ваш компьютер**
> Фильтр SmartScreen Защитника Windows предотвратил запуск неопознанного приложения.

с единственной кнопкой **Не выполнять**. Чтобы всё-таки установить: нажмите
**Подробнее**, затем **Выполнить в любом случае**.

Предупреждение честное, и игнорировать такие вообще не стоит — поэтому вот что оно
означает. SmartScreen задаёт о программе два вопроса: подписана ли она и есть ли у
подписи репутация. Этот установщик **не подписан**: сертификат для подписи кода стоит
несколько сотен долларов в год и требует аппаратного ключа, а это бесплатная программа
одного человека. С файлом всё в порядке — Windows просто видит его впервые.

Подпись запланирована через [SignPath Foundation](https://signpath.org/), который
подписывает проекты с открытым исходным кодом бесплатно. Пока не сделано.

А до тех пор проверьте файл сами, не веря этой странице на слово:

```
SHA-256  01b65a9c730451508f7a007a0b367c70681d944fc017a69bb5a4af2ceda26511
размер   139 306 590 байт
```

```powershell
Get-FileHash .\ScreenRec-0.3.3-setup.exe -Algorithm SHA256
```

Не совпало — не запускайте.

## Установка

Запустите установщик. Он просит права администратора, потому что ставит программу в
`C:\Program Files\ScreenRec` для всех пользователей машины, спрашивает, где держать
записи, и кладёт **ScreenRec** и **ScreenRec Player** в меню «Пуск». Установка поверх
прежней версии заменяет её на месте и не трогает ни ваши настройки, ни ваши записи.
Удаление — как обычно, в «Приложениях и возможностях».

## На чём работает

Windows 10 версии 2004 или новее, 64 бита. Больше ничего не нужно: Python, Qt и ffmpeg
внутри.

**Аппаратное кодирование проверено только на NVIDIA.** Код поддерживает AMD (AMF),
Intel (QSV) и собственные кодировщики Windows (Media Foundation) — написано по их
документации и ограждено так, что неподдерживаемый путь откатывается на процессор, а не
падает, — но у автора одна карта NVIDIA и нет возможности проверить остальные. Если ваша
машина уходит на процессор там, где не должна, об этом стоит сообщить; настройки
программы покажут, какой кодировщик выбран и почему.

**Проход, уменьшающий запись, остаётся на карте только на NVIDIA.** На остальных он идёт
на процессоре — медленнее и совершенно безопасно: к этому моменту это уже файл, а
медленный проход кадр потерять не может.

**Устройства, подключённые после запуска программы, — по-прежнему ограничение.**
Звуковая библиотека перечисляет устройства один раз, поэтому гарнитуру, подключённую при
уже работающем ScreenRec, нельзя записать до его перезапуска. Записи это больше не
стоит: вместо неё записывается устройство по умолчанию, и программа об этом говорит.

## Лицензия

GPL v3 — см. `LICENSE`. Это следствие встроенного ffmpeg, в котором есть x264: x264 —
единственный кодировщик, существующий на любой машине, а записывающей программе, которая
должна работать везде, без него нельзя.

ffmpeg здесь — отдельная программа, запускаемая как подпроцесс: командная строка и
канал, никакой компоновки. Его исходный код, для той самой сборки, что лежит в этом
выпуске, — второй файл этого выпуска.

| | |
|---|---|
| `ScreenRec-0.3.3-setup.exe` | установщик |
| `ffmpeg-source-ec887b13a8.tar.gz` | соответствующий исходный код встроенного ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | хеши обоих файлов |

`THIRD-PARTY-NOTICES.md` в репозитории перечисляет всё остальное и на каких условиях.
