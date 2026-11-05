# Recording every screen at once

Asked for on 2026-11-03:

> "Запись мониторов это как запись разных звуковых дорожек — одновременный синхронный
> захват двух экранов. Смотрящий сможет просто переводить глаза на другой монитор…
> это больше нужно для написания видеоуроков, когда не нужно умещать всё на одном экране."

Not two screens squeezed into one frame. Two recordings at full resolution, watched the
way the screens were used, which needs a player that runs them as one.

**Built and verified on 2026-11-04.** The recording half is done; see §4.

---

## 1. The architecture, and the two measurements that decided it

The first pass at this concluded that one ffmpeg process with several `ddagrab` inputs
was the right answer — one clock, one thing to stop — and that two processes would drift
half a second apart. **Both halves of that were wrong**, and it is worth writing down
why, because each mistake is an easy one to repeat.

**The frame rate.** The original numbers (13.8 and 11.5 fps, "the GA104 has one NVENC
chip") were measured with the heavy encoder tuning that was later measured to cost 14% of
the frames on a *single* screen and was removed. Measured again with the settings the
application ships:

| shape | fps achieved |
|---|---|
| one process, two inputs, two files, 60 fps | 53.4, 59.2 |
| one process, 30 fps | 27.7, 29.7 |
| one process, cheaper quality | 53.5, 59.3 |
| one process, H.264 | 53.8, 58.9 |
| **two processes, 60 fps** | **59.7, 59.1** |
| side by side, composited into one file | 48.7 |

NVENC was never the bottleneck. Two encoder sessions run at full speed; one ffmpeg
juggling two inputs does not.

**The offset.** The "half a second apart" came from comparing how *long* the two files
were — and they were stopped one after the other, so of course they differed. That is
not the question. The question is whether a moment on screen one lands at the same time
in both files, and the way to answer it is to flash a white square on both screens at the
same instant and find it again in each:

| shape | where the mark lands |
|---|---|
| one process, two inputs | **50 ms** apart |
| two processes | **17 ms** apart — one frame |

So two processes are better on *both* counts, and the architecture is the simple one:
**one ffmpeg per screen, started in a tight loop.** In the finished application the mark
now lands **0 ms apart** — the same frame time in both files.

**Audio goes in exactly one file.** Recording the microphone into every screen would give
as many copies of the voice as there are monitors and a player that has to mute all but
one. The first screen carries it; the sidecar says which.

## 2. The sidecar

Every set is written with a `.screenrec.json` beside the files:

```json
{
  "version": 1,
  "recorded": "2026-11-04T01:28:13",
  "audio": "ScreenRec_2026-11-04_01-28-13_screen0.mp4",
  "screens": [
    {"file": "…_screen0.mp4", "monitor": 0, "desktop": [0, 0, 2560, 1440],
     "size": [2560, 1440], "rotation": 1, "primary": true},
    {"file": "…_screen1.mp4", "monitor": 1, "desktop": [2560, 0, 1440, 2560],
     "size": [1440, 2560], "rotation": 2, "primary": false}
  ]
}
```

The desktop rectangles are the point: they let the player put the windows where the
screens really were, so a viewer moves their eyes the same way the person recording did.

## 3. Using it

The **Screen** page of the source picker leads with an **All N screens** card, before the
individual monitors. It only appears when there is more than one screen — offering it on
a laptop would be a lie. Everything else behaves as usual: pause joins each screen's
segments separately, crash safety applies per screen, and `discard` throws the whole set
away.

The capture frame is not drawn in this mode and the application says so: every screen is
being recorded, so there is no "outside" left to draw it in.

## 4. Verified

`tools/verify_two_screens.py` records both screens through the application and then
judges the files — **12 of 12**:

```
2 screens, slowest 57.4 fps, 0 dropped
  …_screen0.mp4: 2560x1440, 613 frames / 10.25s = 59.8 fps, 1.8 MB
  …_screen1.mp4: 1440x2560, 608 frames / 10.25s = 59.3 fps, 2.2 MB
the screens start together (0 ms apart)
```

Both at their own full size, the rotated screen the right way up, no dropped frames, no
segments left behind, and the sidecar reads back cleanly.

Re-run the measurements with `tools/measure_two_screens.py` and
`tools/measure_two_sync.py` if the encoder settings ever change again — that is exactly
what went stale the first time.

## 5. The player

A multi-screen recording is only half useful without something that plays the files as
one. Measured first, as usual: two `QMediaPlayer` instances playing the same file stay a
**constant 33 ms apart over 45 seconds, worst sample 67 ms, and the drift does not grow**.
There is no cumulative drift to fight, so the player corrects a follower only when it
exceeds a threshold worth correcting rather than nudging on every tick.

`app/screenrec/player/` holds it: `engine.py` (the master clock and the followers),
`window.py` (one frameless view per screen plus a transport bar), `open.py` and
`__main__.py`. `ScreenRecPlayer.cmd` launches it; with no argument it opens the most
recent recording in the output folder.

The transport bar carries play/pause, a scrubber, frame-step, speed, volume and close,
and it goes at the bottom of **one real screen** — `transport_spot()` picks the screen
showing most of the first view and clamps the bar inside it. The obvious placement, the
bottom centre of the area the views span, is wrong on any desk that is not a single row:
with one screen above and to the left of the other, that point is in the empty corner
and the bar lands where no monitor is. That is exactly what happened here — every
control present, 760×52 at 1620,2480, on no screen at all, which reads to the user as a
player with no controls and no way to close it.

## 6. Not done

* **Composited layouts.** `recorder.build_command` knows `side_by_side`, `stacked` and
  `pip`, and they are tested, but nothing in the interface offers them. Side by side
  measured 48.7 fps because compositing happens on the CPU; it is the compatibility
  option for when the result has to play anywhere, not the good one.
* **More than two screens** is implemented and untested — this machine has two.
* **Scaled displays.** Both screens here run at 100%.
