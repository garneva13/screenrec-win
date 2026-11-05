# ScreenRec 0.1.0

*Paste this into the GitHub release. The hash below is of the installer this repository
built on 2026-11-05; recompute it if you build again - `Get-FileHash` - because a
release whose hash does not match its file is worse than one with no hash at all.*

---

A screen recorder for Windows, and a player for what it records. Two programs, one
installer.

**ScreenRec** records a monitor, a dragged-out region, one window, or every screen at
once, with the microphone and the system sound. It uses your graphics card to encode
when it can and your processor when it cannot, and it works out which by actually trying
them rather than by reading a list.

**ScreenRec Player** plays a recording back. When a take has several screens it plays
them in step, side by side or one per monitor, and it can line up two cameras that were
not started together.

No editor, no timeline, no account, no network. It records and it plays.

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
SHA-256  38f0ad1e4166b5c3ffda37f4339997f208ac801b49e31389e0f42d856e784f81
size     139,213,001 bytes
```

```powershell
Get-FileHash .\ScreenRec-0.1.0-setup.exe -Algorithm SHA256
```

If that does not match, do not run it.

## Installing

Run the installer. It asks for administrator rights because it installs into
`C:\Program Files\ScreenRec` for everyone on the machine, asks where you would like your
recordings kept, and puts **ScreenRec** and **ScreenRec Player** in the Start menu.
Uninstalling is in "Apps & features" as usual, and it leaves your settings and your
recordings alone.

## What it runs on

Windows 10 version 2004 or later, 64-bit. Nothing else is needed: Python, Qt and ffmpeg
are all inside.

**Hardware encoding is NVIDIA-tested only.** The code supports AMD (AMF), Intel (QSV)
and Windows' own Media Foundation encoders, written from their documented options and
guarded so that an unsupported path falls back to the processor rather than failing -
but the author has one NVIDIA card and no way to run the others. If your machine takes
the processor path when it should not, that is worth a bug report, and the program's
settings will tell you which encoder it chose and why.

## Licence

GPL v3 - see `LICENSE`. That follows from the bundled ffmpeg, which carries x264,
because x264 is the only encoder that exists on every machine and a recorder that must
run anywhere cannot do without it.

ffmpeg is a separate program here, run as a subprocess: a command line and a pipe,
nothing linked. Its source, for the exact build in this release, is the second asset of
this release.

| | |
|---|---|
| `ScreenRec-0.1.0-setup.exe` | the installer |
| `ffmpeg-source-ec887b13a8.tar.gz` | the corresponding source for the bundled ffmpeg (`N-128352-gec887b13a8-20261103`) |
| `SHA256SUMS.txt` | hashes of both |

`THIRD-PARTY-NOTICES.md` in the repository lists everything else and under what terms.


---

# ScreenRec 0.1.0 (по-русски)

Программа записи экрана для Windows и плеер для того, что она записала. Две программы,
один установщик.

**ScreenRec** записывает монитор, выделенную мышью область, отдельное окно или все
экраны сразу, вместе с микрофоном и системным звуком. Кодирует видеокартой, когда может,
и процессором, когда не может, — и выясняет это не по списку, а попыткой закодировать
кадр.

**ScreenRec Player** воспроизводит запись. Если в ней несколько экранов, играет их в
такт — рядом, друг над другом или по одному на монитор, — и умеет свести две камеры,
которые включили не одновременно.

Ни редактора, ни таймлайна, ни учётной записи, ни сети. Записывает и воспроизводит.

## Windows предупредит об этом файле. Вот почему и что делать

При запуске установщика Windows покажет синее окно:

> **Система Windows защитила ваш компьютер**
> Фильтр SmartScreen Защитника Windows предотвратил запуск неопознанного приложения.

и одну кнопку — **Не выполнять**. Чтобы всё-таки установить: нажмите **Подробнее**, затем
**Выполнить в любом случае**.

Это предупреждение честное, и игнорировать предупреждения вообще не стоит — поэтому
объясняю, что оно означает. SmartScreen задаёт о программе два вопроса: подписана ли она
и есть ли у подписи репутация. Этот установщик **не подписан**: сертификат для подписи
кода стоит несколько сотен долларов в год и требует аппаратного ключа, а это бесплатная
программа, которую отдаёт один человек. С файлом ничего не так — Windows просто никогда
его не видела и говорит об этом прямо.

Подпись планируется, через [SignPath Foundation](https://signpath.org/), который
подписывает проекты с открытым исходным кодом бесплатно. Пока её нет. Когда появится,
этот абзац изменится.

А до тех пор проверьте файл сами, не полагаясь на эту страницу:

```
SHA-256  38f0ad1e4166b5c3ffda37f4339997f208ac801b49e31389e0f42d856e784f81
размер   139 213 001 байт
```

```powershell
Get-FileHash .\ScreenRec-0.1.0-setup.exe -Algorithm SHA256
```

Если не совпало — не запускайте.

## Установка

Запустите установщик. Он попросит права администратора, потому что ставит в
`C:\Program Files\ScreenRec` для всех пользователей компьютера, спросит, где хранить
записи, и положит **ScreenRec** и **ScreenRec Player** в меню «Пуск». Удаление — как
обычно, через «Приложения и возможности»; ваши настройки и записи при этом не трогаются.

## На чём работает

Windows 10 версии 2004 или новее, 64 бита. Больше ничего не нужно: Python, Qt и ffmpeg
внутри.

**Аппаратное кодирование проверено только на NVIDIA.** Поддержка AMD (AMF), Intel (QSV)
и собственных кодировщиков Windows (Media Foundation) написана по их документации и
ограждена так, чтобы непроверенный путь уходил на процессор, а не падал, — но у автора
одна видеокарта NVIDIA и нет способа запустить остальные. Если ваша машина ушла на
процессор там, где не должна была, — это повод написать о проблеме; в настройках
программы видно, какой кодировщик она выбрала и почему.

## Лицензия

GPL v3 — см. `LICENSE`. Это следствие вложенного ffmpeg, в котором есть x264: x264 —
единственный кодировщик, который есть на любой машине, а программа записи, обязанная
работать везде, без него не обойдётся.

ffmpeg здесь — отдельная программа, запускаемая подпроцессом: командная строка и канал,
ничего не слинковано. Его исходный код ровно для этой сборки — второй файл этого релиза.
