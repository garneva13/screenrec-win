# ScreenRec 0.2.0

*Paste this into the GitHub release. The hash below is of the installer this repository
built on 2026-11-09; recompute it if you build again - `Get-FileHash` - because a
release whose hash does not match its file is worse than one with no hash at all.*

---

A screen recorder for Windows, and a player for what it records. Two programs, one
installer. This release adds the camera, a way to record a smaller file, and a settings
window you can find things in.

## New in 0.2.0

**Your camera in a corner of the recording.** Four corners, four sizes, and a rounded
rectangle, a circle or a plain rectangle. The camera is drawn on the desktop rather than
mixed in by the encoder, which has two consequences worth knowing: the recording picks
it up for free, and **you can see it on your own screen while you record**.

That design has one more consequence, and it is the useful one: the camera can be
**switched off in the middle of a recording**, from a button on the bar, when it ends up
on top of something that matters. The recording carries on untouched. Measured on a real
take: before the switch, 11,745 of 11,890 sampled pixels in the camera's rectangle were
the camera's; after it, 0.

**The camera runs at a resolution that suits the little window**, not at 1080p. The
automatic choice weighs frame rate before size, which is not pedantry - on the camera
this was written against, the two largest modes run at 2 frames a second. You can pick a
mode by hand; the list says what frame rate each one can really do.

**A smaller file.** The recording can be scaled down to 1440p, 1080p or 720p instead of
the screen's own size. Measured on a 1440p screen: 1080p is a quarter smaller, 720p 59 %
smaller, and the frame rate held. It only ever shrinks - a screen already smaller than
the chosen height is left alone, and the settings say so instead of letting you wonder.

**The settings window was one column 1375 pixels tall**, which was taller than the
screen it had to open on. It is now five pages with a sidebar: Recording, Sound, Camera,
Control, General. The camera page draws where the camera will sit, so you pick a corner
by looking at it. Nothing was dropped on the way.

**The shortcut boxes read the keys you press** instead of asking you to type
"Ctrl+Alt+R". Escape cancels, and a combination another command already has is refused
with the reason.

**The program's own windows can be photographed again.** They carried a Windows flag
that hides a window from every screen capture on the machine - the Snipping Tool
included - so you could not take a picture of your own settings to ask anybody about
them. The flag is now on only while a recording is running, which is the only time it
does anything.

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
SHA-256  d23dd37bbc4d4614ab2a6cb068c6dc6d8faa4be0325d4db18e97952386c00425
size     139,255,712 bytes
```

```powershell
Get-FileHash .\ScreenRec-0.2.0-setup.exe -Algorithm SHA256
```

If that does not match, do not run it.

## Installing

Run the installer. It asks for administrator rights because it installs into
`C:\Program Files\ScreenRec` for everyone on the machine, asks where you would like your
recordings kept, and puts **ScreenRec** and **ScreenRec Player** in the Start menu.
Installing over 0.1.0 replaces it in place and leaves your settings and your recordings
alone. Uninstalling is in "Apps & features" as usual.

## What it runs on

Windows 10 version 2004 or later, 64-bit. Nothing else is needed: Python, Qt and ffmpeg
are all inside.

**Hardware encoding is NVIDIA-tested only.** The code supports AMD (AMF), Intel (QSV)
and Windows' own Media Foundation encoders, written from their documented options and
guarded so that an unsupported path falls back to the processor rather than failing -
but the author has one NVIDIA card and no way to run the others. If your machine takes
the processor path when it should not, that is worth a bug report, and the program's
settings will tell you which encoder it chose and why.

**The camera needs a camera Windows can see.** If there is none, the setting says so
rather than failing quietly, and a camera that will not open never stops a recording:
the reason goes on the bar and the screen is recorded without it.

## Licence

GPL v3 - see `LICENSE`. That follows from the bundled ffmpeg, which carries x264,
because x264 is the only encoder that exists on every machine and a recorder that must
run anywhere cannot do without it.

ffmpeg is a separate program here, run as a subprocess: a command line and a pipe,
nothing linked. Its source, for the exact build in this release, is the second asset of
this release.

| | |
|---|---|
| `ScreenRec-0.2.0-setup.exe` | the installer |
| `ffmpeg-source-ec887b13a8.tar.gz` | the corresponding source for the bundled ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | hashes of both |

`THIRD-PARTY-NOTICES.md` in the repository lists everything else and under what terms.

---

# ScreenRec 0.2.0 (по-русски)

Программа записи экрана для Windows и плеер для того, что она записала. Две программы,
один установщик. В этом выпуске появилась камера, возможность писать файл поменьше и
окно настроек, в котором можно что-то найти.

## Что нового в 0.2.0

**Камера в углу записи.** Четыре угла, четыре размера, прямоугольник со скруглением,
круг или обычный прямоугольник. Камера рисуется прямо на рабочем столе, а не
подмешивается кодировщиком, и из этого следуют две вещи: запись подхватывает её
бесплатно, и **вы видите её на своём экране, пока пишете**.

Из того же устройства следует и третье, самое полезное: камеру можно **выключить прямо
во время записи** — кнопкой на панели, если она закрыла что-то важное. Запись при этом
продолжается как ни в чём не бывало. Измерено на настоящем дубле: до выключения в
прямоугольнике камеры 11 745 из 11 890 проверенных точек принадлежали камере, после —
ноль.

**Камера работает в разрешении под размер окошка**, а не в 1080p. Автовыбор смотрит на
частоту кадров раньше, чем на размер, и это не придирка: у камеры, на которой это
написано, два самых больших режима выдают два кадра в секунду. Режим можно выбрать и
руками — в списке у каждого написано, сколько кадров он на самом деле даёт.

**Файл поменьше.** Запись можно уменьшать до 1440p, 1080p или 720p вместо размера
экрана. Измерено на экране 1440p: 1080p меньше на четверть, 720p — на 59 %, частота
кадров при этом удержалась. Уменьшает только вниз: экран, который и так меньше
выбранной высоты, остаётся как есть, и настройки об этом прямо говорят.

**Окно настроек было колонкой высотой 1375 точек** — выше экрана, на котором должно было
открываться. Теперь это пять разделов с боковой навигацией: «Запись», «Звук», «Камера»,
«Управление», «Общие». На странице камеры нарисовано, где она окажется, так что угол
выбирается глазами. По дороге ничего не потерялось.

**Поля горячих клавиш читают нажатие**, а не просят набрать «Ctrl+Alt+R». Esc отменяет,
а сочетание, уже занятое другой командой, отклоняется с объяснением.

**Окна самой программы снова можно сфотографировать.** На них стоял флаг Windows,
который прячет окно от любого захвата экрана, включая «Ножницы», — и снять собственные
настройки, чтобы кого-нибудь о них спросить, было нельзя. Теперь флаг включается только
на время записи, когда он что-то и значит.

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
одного человека. С файлом всё в порядке — Windows просто видит его впервые и говорит
об этом.

Подпись запланирована через [SignPath Foundation](https://signpath.org/), который
подписывает проекты с открытым исходным кодом бесплатно. Пока не сделано.

А до тех пор проверьте файл сами, не веря этой странице на слово:

```
SHA-256  d23dd37bbc4d4614ab2a6cb068c6dc6d8faa4be0325d4db18e97952386c00425
размер   139,255,712 байт
```

```powershell
Get-FileHash .\ScreenRec-0.2.0-setup.exe -Algorithm SHA256
```

Если не совпало — не запускайте.

## Установка

Запустите установщик. Он попросит права администратора, потому что ставит программу в
`C:\Program Files\ScreenRec` для всех пользователей машины, спросит, куда складывать
записи, и положит **ScreenRec** и **ScreenRec Player** в меню «Пуск». Установка поверх
0.1.0 заменяет её на месте и не трогает ни ваши настройки, ни ваши записи. Удаление —
как обычно, через «Приложения и возможности».

## На чём работает

Windows 10 версии 2004 или новее, 64 бита. Больше ничего не нужно: Python, Qt и ffmpeg
уже внутри.

**Аппаратное кодирование проверено только на NVIDIA.** Код поддерживает AMD (AMF),
Intel (QSV) и собственный Media Foundation от Windows — написано по их документации и
ограждено так, что неподдерживаемый путь откатывается на процессор, а не падает, — но
у автора одна видеокарта NVIDIA и нет возможности проверить остальные. Если ваша машина
уходит на процессор там, где не должна, об этом стоит сообщить; настройки программы
покажут, какой кодировщик выбран и почему.

**Камере нужна камера, которую видит Windows.** Если её нет, настройка так и скажет, а
не промолчит; и камера, которая не открылась, никогда не срывает запись — причина
появляется на панели, а экран пишется без неё.

## Лицензия

GPL v3 — см. `LICENSE`. Это следствие встроенного ffmpeg, в котором есть x264: x264 —
единственный кодировщик, который есть на любой машине, и программа, обязанная работать
везде, без него не обойдётся.

ffmpeg здесь — отдельная программа, запускаемая как подпроцесс: командная строка и
канал, ничего не слинковано. Его исходный код для ровно той сборки, что внутри, — второй
файл этого выпуска.

| | |
|---|---|
| `ScreenRec-0.2.0-setup.exe` | установщик |
| `ffmpeg-source-ec887b13a8.tar.gz` | соответствующий исходный код встроенного ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | хеши обоих файлов |

`THIRD-PARTY-NOTICES.md` в репозитории перечисляет всё остальное и на каких условиях.
