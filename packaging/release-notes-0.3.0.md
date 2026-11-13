# ScreenRec 0.3.0

*Paste this into the GitHub release. The hash below is of the installer this repository
built on 2026-11-13; recompute it if you build again - `Get-FileHash` - because a
release whose hash does not match its file is worse than one with no hash at all.*

---

A screen recorder for Windows, and a player for what it records. Two programs, one
installer. This release is about one question: **is the sound actually being recorded?**

## What happened, and what changed

A call was recorded with this program and came back with the user's own voice and
nothing from the other end. Three things had to be wrong at once for that to happen
without a word of warning, and all three were.

**There was one level meter, and it showed the loudest of the two sources.** The user's
own voice moved the needle for the whole call, so the one thing on screen that could
have reported the silence was reporting the microphone. There are **two meters** now,
one for the microphone and one for the system sound, and they are on the recording row
as well - the row that is on screen for the whole take, which is exactly when a sound
device changing would otherwise go unnoticed.

**"Default playback device" did not say which device that was.** On the machine this
was found on there are two endpoints whose names differ by one word, and sound playing
to the other one records as silence. The list now names the device it means.

**The check after a recording asked whether any audio had arrived.** It had: the device
was alive and carrying digital silence, which is what recording the wrong output sounds
like. Now the program says so **25 seconds into the recording**, while there is still
something to be done about it, and again when the recording ends.

## The recording follows the sound

If the source is "Default playback device", the recording now **follows Windows while
it runs**. A wireless headset running out of battery moves every sound on the machine to
the speakers, and the recording moves with it, without a gap and without stopping.

A device you picked **by name** is never moved - that is your decision. If it falls
silent while something else is playing, the bar says where the sound is going instead.

Measured on real hardware rather than in a test: capturing an endpoint carrying a tone,
139680 audible frames of 140217; after switching to a silent one, **0 audible frames of
120000 new ones**, with the recording never pausing.

## The meter reads in decibels

The old scale was linear, so a tone at 27% of full scale - loud to listen to - lit one
bar of five, and so did everything else anyone records at. It is now -45 dBFS across
seven segments: that tone lights six, ordinary speech lights four. Anything above the
floor lights at least one segment, because these meters answer "is anything arriving"
before they answer "how much". The top segment turns amber near full scale, where a
microphone is about to clip.

## Windows will warn you about this file. Here is why, and what to do

When you run the installer, Windows will show a blue screen saying

> **Система Windows защитила ваш компьютер**
> Фильтр SmartScreen Защитника Windows предотвратил запуск неопознанного приложения.

with one button, **Не выполнять**. To install anyway: click **Подробнее**, then
**Выполнить в любом случае**.

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
SHA-256  faf807c5d80ce51535a5a6d3a81a7e5b1dcbe4fe26ef1ed2efab279e2b87bd64
size     139,281,037 bytes
```

```powershell
Get-FileHash .\ScreenRec-0.3.0-setup.exe -Algorithm SHA256
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

**Following the playback device has one limit.** A device that is plugged in *after* the
program started cannot be recorded until the program is restarted - the audio library
enumerates devices once. The program says so rather than quietly recording the old one.

## Licence

GPL v3 - see `LICENSE`. That follows from the bundled ffmpeg, which carries x264,
because x264 is the only encoder that exists on every machine and a recorder that must
run anywhere cannot do without it.

ffmpeg is a separate program here, run as a subprocess: a command line and a pipe,
nothing linked. Its source, for the exact build in this release, is the second asset of
this release.

| | |
|---|---|
| `ScreenRec-0.3.0-setup.exe` | the installer |
| `ffmpeg-source-ec887b13a8.tar.gz` | the corresponding source for the bundled ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | hashes of both |

`THIRD-PARTY-NOTICES.md` in the repository lists everything else and under what terms.

---

# ScreenRec 0.3.0 (по-русски)

Программа записи экрана для Windows и плеер для того, что она записала. Две программы,
один установщик. Этот выпуск — про один вопрос: **а звук точно записывается?**

## Что случилось и что изменилось

Этой программой записали созвон, и в записи оказался только собственный голос, без
собеседников. Чтобы это произошло молча, должны были совпасть три вещи — и они совпали.

**Индикатор уровня был один и показывал громчайший из двух источников.** Собственный
голос двигал стрелку весь созвон, поэтому единственное, что могло сообщить о тишине,
сообщало о микрофоне. Теперь индикаторов **два** — отдельно микрофон, отдельно звук
системы, — и они есть на строке записи тоже: именно там видно, если выход переключился
посреди записи.

**«Устройство по умолчанию» не говорило, какое именно.** На машине, где это нашлось,
два выхода с именами, отличающимися одним словом, и звук, идущий в другой из них,
записывается как тишина. Теперь список называет устройство, которое имеет в виду.

**Проверка после записи спрашивала, пришёл ли хоть один кадр.** Он приходил: выход был
жив и нёс цифровую тишину — именно так звучит запись не того устройства. Теперь
программа говорит об этом **на 25-й секунде записи**, пока ещё можно что-то сделать, и
повторяет это по её окончании.

## Запись идёт за звуком

Если выбрано «Устройство по умолчанию», запись теперь **следует за Windows на ходу**.
Сели беспроводные наушники — Windows увела весь звук в колонки — запись перешла следом,
без разрыва и без остановки.

Устройство, выбранное **по имени**, не трогается: это ваше решение. Но если оно молчит,
а звук идёт в другое, панель скажет, куда именно.

Измерено на настоящем железе, а не в тесте: при записи выхода с тоном — 139680 звучащих
кадров из 140217; после перехода на молчащий выход — **0 звучащих из 120000 новых**, и
запись при этом ни разу не прервалась.

## Индикатор читает в децибелах

Прежняя шкала была линейной: тон в 27 % от полной шкалы — громкий на слух — зажигал
одно деление из пяти, и ровно то же делало всё остальное, на чём записывают. Теперь
−45 дБ на семь делений: этот тон даёт шесть, обычная речь — четыре. Всё, что выше
порога, зажигает хотя бы одно деление: эти индикаторы сначала отвечают на вопрос
«приходит ли вообще хоть что-то». Верхнее деление желтеет у самого предела, где
микрофон вот-вот начнёт обрезать.

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
SHA-256  faf807c5d80ce51535a5a6d3a81a7e5b1dcbe4fe26ef1ed2efab279e2b87bd64
размер   139 281 037 байт
```

```powershell
Get-FileHash .\ScreenRec-0.3.0-setup.exe -Algorithm SHA256
```

Если не совпало — не запускайте.

## Установка

Запустите установщик. Он попросит права администратора, потому что ставит программу в
`C:\Program Files\ScreenRec` для всех пользователей машины, спросит, куда складывать
записи, и положит **ScreenRec** и **ScreenRec Player** в меню «Пуск». Установка поверх
прежней версии заменяет её на месте и не трогает ни ваши настройки, ни ваши записи.
Удаление — как обычно, через «Приложения и возможности».

## На чём работает

Windows 10 версии 2004 или новее, 64 бита. Больше ничего не нужно: Python, Qt и ffmpeg
уже внутри.

**Аппаратное кодирование проверено только на NVIDIA.** Код поддерживает AMD (AMF),
Intel (QSV) и собственный Media Foundation от Windows — написано по их документации и
ограждено так, что неподдерживаемый путь откатывается на процессор, а не падает, — но
у автора одна видеокарта NVIDIA и нет возможности проверить остальные. Если ваша машина
уходит на процессор там, где не должна, об этом стоит сообщить; настройки программы
покажут, какой кодировщик выбран и почему.

**У слежения за устройством вывода есть один предел.** Устройство, подключённое **после**
запуска программы, записать нельзя до её перезапуска — звуковая библиотека
перечисляет устройства один раз. Программа об этом скажет, а не продолжит молча писать
прежнее.

## Лицензия

GPL v3 — см. `LICENSE`. Это следствие встроенного ffmpeg, в котором есть x264: x264 —
единственный кодировщик, который есть на любой машине, и программа, обязанная работать
везде, без него не обойдётся.

ffmpeg здесь — отдельная программа, запускаемая как подпроцесс: командная строка и
канал, ничего не слинковано. Его исходный код для ровно той сборки, что внутри, — второй
файл этого выпуска.

| | |
|---|---|
| `ScreenRec-0.3.0-setup.exe` | установщик |
| `ffmpeg-source-ec887b13a8.tar.gz` | соответствующий исходный код встроенного ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | хеши обоих файлов |

`THIRD-PARTY-NOTICES.md` в репозитории перечисляет всё остальное и на каких условиях.
