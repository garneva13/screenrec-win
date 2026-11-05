# Opening your own videos, and lining them up by hand

The player was built for one thing: a `.screenrec.json` set that this recorder wrote, two
screens that started 17 ms apart because one process started them. The user asked for the
other case, and it is a different problem:

> у нас есть два видео снятых с двух камер, это синхронные видео … нужна возможность
> подстроить тайминг видео если они сняты с расхождением по времени

Two cameras are not two screens. Nothing paired the files, nothing wrote an index, and
nobody pressed one button to start them — so they begin at different instants, and the
amount is whatever it is. This document is the contract for both halves: **choosing the
files**, and **sliding one against the other until they match**.

It does not turn the player into an editor. Nothing is re-encoded, nothing is written over
a source, and the offset is a number the player reads — never a cut.

---

## 1. Opening

Three ways in, all of them reaching the same place:

* **Open…** — a file dialog, from the transport's menu and from `Ctrl+O`. It takes a
  `.screenrec.json` index, a plain video, or a folder (whose newest recording opens),
  exactly as `open.load` already does on the command line.
* **Drag and drop** onto any player window. One file opens it; **two files at once open
  them as a pair**, which is the two-camera case in a single gesture.
* **The library** — a list of what is in the recorder's output folder: every
  `.screenrec.json` set and every loose video, newest first, with its name, date, length
  and size. Clicking one opens it. It is a list, not a media manager: no tags, no
  renaming, no deleting. Those belong to Explorer and it is one click away.

### 1.1 A video with no pair

A single video opens with one view, as it always has, and gains one action:

**Добавить второй экран… / Add a second video…** — a file dialog, and the two are then
played as a set of two streams. Everything the layouts do applies from that moment: the
chooser is not re-shown (the viewer has just made a choice by adding a file), the layout
is whatever is in force, and `side_by_side` is used if none is.

Adding a file **does** rebuild the clock, because a `QMediaPlayer` cannot grow a second
file. That is the one place in this player where a rebuild is allowed, and it is allowed
because the viewer asked for it by name. The position must be carried across and the
paused state kept: adding the second camera must not throw away where you were.

### 1.2 Which file the sound comes from

Two cameras both recorded sound, and one of them probably recorded it better. So:

* exactly one stream is the **audio source** — §5 of `PLAYER_LAYOUTS.md` is unchanged and
  two copies of one voice must never reach the speakers;
* the transport offers **Звук: Экран 1 / Экран 2 / Выключен**, and the choice is the
  viewer's, not the file order's;
* the default is the first stream, and for a recorder-made set it is whatever the sidecar
  names in `"audio"` — the existing behaviour, untouched;
* **changing the audio source is not the same as swapping the picture.** Swapping which
  screen is on the left must not move the sound, and this control must not move the
  picture. They are two questions and they get two controls.

### 1.3 Saving the pair

Two files, an audio choice and an offset are work; losing them on close is the thing that
makes people stop using a feature. So: **Сохранить набор / Save this set** writes a
`.screenrec.json` beside the first file, and opening that file later restores the pair,
the audio choice and the offsets with nothing to redo.

The format is the recorder's, extended, and the extension is what makes it usable here:

```json
{
  "version": 2,
  "recorded": "2026-11-04T18:20:00",
  "audio": "camera_a.mp4",
  "screens": [
    {"file": "camera_a.mp4", "monitor": 0, "size": [1920, 1080], "offset_ms": 0},
    {"file": "D:/shoot/camera_b.mp4", "monitor": 1, "size": [1920, 1080],
     "offset_ms": -1480}
  ]
}
```

* `version` becomes **2**. A version 1 index still reads: `offset_ms` missing means 0, and
  every file named in it is resolved beside the index as it always was.
* `"file"` may now be an **absolute path**, because two cameras are not guaranteed to live
  in one folder. A bare name still resolves beside the index, so nothing the recorder
  writes changes meaning.
* A set the player saved is an ordinary index. The recorder never writes `offset_ms`,
  because its own screens start together and a field that is always 0 is a field that
  rots.

---

## 2. Lining them up

### 2.1 What the number means

Each stream has an **offset in milliseconds**, and the rule is one line:

```
position inside stream i  =  timeline position + offset_ms[i]
```

So a camera that started recording **earlier** than the other has content the timeline has
not reached yet, and its offset is **positive** — wind it forward. A camera that started
**later** gets a negative offset. The reference stream is simply the one left at 0; there
is no privileged stream, and moving one is the same as moving the other the other way.

Written as the viewer experiences it: **positive means "this video runs ahead, skip into
it"**. The control says that in words, because a sign convention nobody can remember is a
control nobody can use.

Consequences that must be got right rather than discovered:

* **The timeline covers what is coverable.** `duration = max(0, max_i(duration_i - offset_ms[i]))`.
  A stream wound forward by 2 s has 2 s less to give at the end — *that stream* does.
  The timeline is the **longest** of those spans and not the shortest, because a stream
  that has run out does not end the recording: §5 of `PLAYER_LAYOUTS.md` holds its last
  frame while the other one plays on. So on a pair, sliding one file usually does not
  shorten the timeline at all — the file left at 0 still has all of its own length to
  give, and that is what the max returns. The timeline moves only when the stream that
  was the longest is the one that moved, and then by what *it* lost, which is the
  offset only when it was the sole stream or the two are the same length. Nothing here
  may be re-tuned into "`duration` shrinks by the offset": that would cut a one-stream
  recording and a both-slid pair wrong, and it would hide the tail of the file that
  did not move.
* **Before a stream's content starts** — timeline position `+ offset_ms[i] < 0` — that
  stream has nothing to show. It holds black with a quiet badge saying the recording has
  not started yet, exactly as §5 of `PLAYER_LAYOUTS.md` holds the last frame of a stream
  that has ended. Showing a frozen first frame as if it were live is the thing to avoid.
* **The sync correction works on the offset target, not on equality.** The engine's
  existing 80 ms threshold compares a follower against the master; with an offset in
  force, the target is `master position + offset_i`. Getting this wrong makes the player
  fight the viewer's own adjustment, pulling the streams back together every tick.
* **Frame stepping** steps the timeline, so both streams move one frame of the stream
  whose frame rate is being used. Two cameras at 25 and 30 fps will not step in lockstep
  and nothing can make them; the step uses the reference stream's rate and says so.

### 2.2 The control

A **Синхронизация / Sync** panel in the transport, open only when there are two streams:

* a stream picker — which one is being moved, the other being the reference;
* **−1 кадр / +1 кадр** and **−100 мс / +100 мс** buttons, and the keyboard: `Shift+←`
  and `Shift+→` for a frame, `Alt+←` and `Alt+→` for 100 ms;
* a number field in milliseconds, typed directly, with the sign explained beside it;
* **Сбросить / Reset** back to 0;
* the current value shown at all times, because an offset that is silently non-zero is a
  recording that looks broken for a reason nobody can see.

**Every adjustment is live.** The moved stream reseeks to its new place and the picture
updates while paused, which is the only way to line two clapperboards up by eye. While
playing, the adjustment applies without stopping and without a visible restart.

The adjustment must **not** move the timeline position: the viewer is sliding one video
against the other, not scrubbing. Read the position before and after and assert it.

### 2.3 Finding the offset is the viewer's job, not the program's

Automatic alignment — by audio correlation, by a flash, by timecode — is **out of scope**
and is not to be half-built. What is here is a control that makes doing it by hand quick:
pause on the clap in one, nudge the other a frame at a time until it matches. That is the
whole feature, and it is honest about being the whole feature.

If automatic alignment is ever wanted, it is a separate job with its own measurement — the
audio of both files, cross-correlated — and it needs the sound of both, which this player
deliberately does not decode twice.

---

## 3. What must be checked

* a plain video opened from the dialog, from a drop, and from the library;
* two files dropped together opening as a pair;
* **Добавить второй экран** on a single video: the position is carried over and the paused
  state is kept;
* the audio source control: exactly one audio output at any moment, and swapping the
  picture not moving it;
* an offset typed, nudged, and reset; the timeline position unchanged by all three;
* a negative offset at the start of the timeline: black with a badge, not a frozen frame;
* the duration following the offsets as §2.1 describes them: winding the **longest**
  stream forward shortens the timeline by what that stream loses, sliding a stream late
  lengthens it, and moving a stream that was not the longest changes nothing. A single
  video is the only case where the timeline shrinks by the offset itself;
* saving a set and reopening it: pair, audio choice and offsets all restored;
* a version 1 index still opening, with every offset 0;
* an absolute path in an index, and a file that has been moved away since — the player
  says which file is missing and opens what is left;
* **a recorder-made two-screen set, unchanged in every way.** This is the regression that
  matters most: none of the above may make the ordinary case worse.
