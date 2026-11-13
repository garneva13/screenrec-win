# ScreenRec 0.3.2

*Paste this into the GitHub release. The hash below is of the installer this repository
built on 2026-11-13; recompute it if you build again - `Get-FileHash` - because a
release whose hash does not match its file is worse than one with no hash at all.*

---

A screen recorder for Windows, and a player for what it records. Two programs, one
installer. This release is about one sentence: **a recording should happen.**

## The recording that refused to start

Press record, watch the countdown, and then nothing. The bar was saying

> No system-sound device available: [Errno -9996] Invalid device info

Windows had no playback device to offer for a moment - a headset switching off will do
it - and the program treated that as a reason not to record **at all**. The screen and
the microphone were both ready and would have recorded perfectly.

That was the wrong trade, and it is the main fix in this release. **An optional track
never refuses a mandatory one.** The system-sound track is dropped, the recording goes
ahead, and the bar says which track is missing and why - once as the take begins, while
there is still time to stop and fix it, and once more when the file is saved.

Measured both ways rather than reasoned about: with no playback endpoint at all the file
is still written and holds the picture; with a device that has gone, the current default
is recorded in its place and that substitution is reported too.

## A failed start no longer hides the program

While a recording runs, ScreenRec takes its own windows out of every screen capture on
the machine, so the bar and the camera frame do not end up in your recording. That flag
was set *before* the recording started and nothing took it back when the start failed -
so one refused start made the whole program invisible to every capture tool on the
machine for the rest of the session, the Snipping Tool included. The error on the bar
could be read and not photographed.

The flag now goes on only once a take has really begun.

## Your playback device is remembered by name

The chosen device was stored as a number - a position in the audio library's device
list. That list is built once, in the order the devices are found, so plugging in a
speaker renumbers everything after it. A headset that was number 21 yesterday is number
29 today, and the settings window sat on **"Device 21 (not found)"** with nothing being
recorded until a device was picked by hand.

Devices are remembered by **name** now, so a device stays the one you chose after the
computer's devices change. While it is away the default is recorded and the settings
window says which device is missing - and your choice is kept, so it is used again on
its own as soon as the device comes back.

## Messages in your own language

Parts of the program that run on the capture threads could not reach the translations,
which is how a Russian window came to show an English error number. Those failures now
carry a code the interface translates. Every one of them has a sentence in both
languages, and a test fails the build if one does not.

## A log, for when something goes wrong quietly

ScreenRec has no console, and the interface toolkit catches an error inside a button's
handler and carries on - so a failure could leave the program running, looking well and
doing nothing. Every unhandled error is now written to

```
%APPDATA%\ScreenRec\errors.log
```

with the time it happened, from the interface and from the capture and hotkey threads
alike. If the program is running and not doing what it should, that file is the first
place to look, and the right thing to attach to a bug report.

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
SHA-256  bdcc05feca1a80a7166ce3ea441326c7c75ef2564e6cf7a4fdcef567598de221
size     139,301,546 bytes
```

```powershell
Get-FileHash .\ScreenRec-0.3.2-setup.exe -Algorithm SHA256
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

**Devices plugged in after the program started are still a limit.** The audio library
enumerates devices once, so a headset connected while ScreenRec is running cannot be
recorded until ScreenRec is restarted. What has changed is that this no longer costs
you a recording: the default device is recorded instead and the program says so.

## Licence

GPL v3 - see `LICENSE`. That follows from the bundled ffmpeg, which carries x264,
because x264 is the only encoder that exists on every machine and a recorder that must
run anywhere cannot do without it.

ffmpeg is a separate program here, run as a subprocess: a command line and a pipe,
nothing linked. Its source, for the exact build in this release, is the second asset of
this release.

| | |
|---|---|
| `ScreenRec-0.3.2-setup.exe` | the installer |
| `ffmpeg-source-ec887b13a8.tar.gz` | the corresponding source for the bundled ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | hashes of both |

`THIRD-PARTY-NOTICES.md` in the repository lists everything else and under what terms.

---

# ScreenRec 0.3.2 (по-русски)

Программа записи экрана для Windows и плеер для того, что она записала. Две программы,
один установщик. Этот выпуск — про одну фразу: **запись должна состояться.**

## Запись, которая не начиналась

Нажимаешь «запись», идёт отсчёт — и ничего. На панели было написано:

> No system-sound device available: [Errno -9996] Invalid device info

У Windows в тот момент не нашлось ни одного устройства воспроизведения, с которого можно
писать, — так бывает, например, когда выключается гарнитура, — и программа считала это
причиной **не записывать вообще**. При том что экран и микрофон были готовы и записались
бы прекрасно.

Это был неверный размен, и его исправление — главное в этом выпуске. **Необязательная
дорожка никогда не отменяет обязательную.** Дорожка звука с компьютера отбрасывается,
запись идёт, а панель говорит, какой дорожки нет и почему: один раз в начале, пока ещё
можно остановиться и всё поправить, и ещё раз при сохранении файла.

Измерено в обе стороны, а не выведено рассуждением: когда устройства нет совсем, файл
всё равно пишется и картинка в нём есть; когда пропало выбранное устройство, вместо него
записывается текущее по умолчанию, и об этой подмене тоже сказано.

## Неудачный старт больше не прячет программу

Во время записи ScreenRec убирает собственные окна из любого захвата экрана на машине,
чтобы панель и рамка камеры не попали в вашу запись. Этот признак ставился **до** начала
записи, и при неудачном старте его никто не снимал — поэтому один сорвавшийся старт
делал всю программу невидимой для любых средств захвата до конца сеанса, включая
«Ножницы». Ошибку на панели можно было прочитать, но нельзя было сфотографировать.

Теперь признак включается только после того, как запись действительно началась.

## Устройство вывода запоминается по имени

Выбранное устройство хранилось числом — позицией в списке устройств звуковой библиотеки.
Этот список строится один раз, в том порядке, в каком устройства нашлись, поэтому
подключение колонки сдвигает нумерацию всего, что идёт дальше. Гарнитура, бывшая вчера
номером 21, сегодня номер 29 — и окно настроек показывало **«Устройство 21 (не
найдено)»**, а запись звука не велась, пока устройство не выберешь вручную.

Теперь устройства запоминаются **по имени** и остаются выбранными после того, как набор
устройств на компьютере изменился. Пока устройства нет, записывается то, что стоит по
умолчанию, а окно настроек говорит, какого устройства не хватает, — и ваш выбор
сохраняется, поэтому используется снова сам собой, как только устройство вернётся.

## Сообщения на вашем языке

Части программы, работающие в потоках захвата, не могли добраться до переводов — отсюда
английский номер ошибки в русском окне. Теперь такие отказы несут код, который
интерфейс переводит. У каждого из них есть фраза на обоих языках, и тест не даёт собрать
сборку, если у какого-то её нет.

## Журнал — на случай, если что-то сломается молча

У ScreenRec нет консоли, а библиотека интерфейса перехватывает ошибку внутри обработчика
кнопки и продолжает работу, — поэтому сбой мог оставить программу запущенной, на вид
здоровой и ничего не делающей. Теперь каждая необработанная ошибка записывается в

```
%APPDATA%\ScreenRec\errors.log
```

с отметкой времени — и из интерфейса, и из потоков захвата и горячих клавиш. Если
программа работает, но делает не то, начинать надо с этого файла; его же стоит
прикладывать к сообщению об ошибке.

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
SHA-256  bdcc05feca1a80a7166ce3ea441326c7c75ef2564e6cf7a4fdcef567598de221
размер   139 301 546 байт
```

```powershell
Get-FileHash .\ScreenRec-0.3.2-setup.exe -Algorithm SHA256
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

**Устройства, подключённые после запуска программы, — по-прежнему ограничение.**
Звуковая библиотека перечисляет устройства один раз, поэтому гарнитуру, подключённую при
уже работающем ScreenRec, нельзя записать до его перезапуска. Изменилось то, что это
больше не стоит вам записи: вместо неё записывается устройство по умолчанию, и программа
об этом говорит.

## Лицензия

GPL v3 — см. `LICENSE`. Это следствие встроенного ffmpeg, в котором есть x264: x264 —
единственный кодировщик, существующий на любой машине, а записывающей программе, которая
должна работать везде, без него нельзя.

ffmpeg здесь — отдельная программа, запускаемая как подпроцесс: командная строка и
канал, никакой компоновки. Его исходный код, для той самой сборки, что лежит в этом
выпуске, — второй файл этого выпуска.

| | |
|---|---|
| `ScreenRec-0.3.2-setup.exe` | установщик |
| `ffmpeg-source-ec887b13a8.tar.gz` | соответствующий исходный код встроенного ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | хеши обоих файлов |

`THIRD-PARTY-NOTICES.md` в репозитории перечисляет всё остальное и на каких условиях.
