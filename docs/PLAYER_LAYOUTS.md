# How two screens are shown: the player's layouts

The recorder writes one MP4 per screen plus a `.screenrec.json` index (`multi.py`), and
`player/engine.py` plays them in step from one clock. What was missing is the other half
of the question the user actually asked: **a viewer with one monitor has two pictures and
one screen to put them on.** This document is the contract for that half.

Nothing here touches the files. A layout is a way of *looking*; the streams are never
composited into one file, never re-encoded, and switching layouts never rewrites
anything. See §8.

---

## 1. The vocabulary

Five layouts, by stable id. The id is what is stored and what every module passes around;
the Russian title is the user's and lives only in the window.

| id | title | what it is | needs |
|---|---|---|---|
| `side_by_side` | Рядом | both streams, left and right, one host window | — |
| `stacked` | Друг над другом | both streams, top and bottom, one host window | — |
| `focus` | Фокус на одном | one stream fills the stage, a switch picks which | — |
| `separate` | Отдельные окна | one borderless window per stream, shared transport | — |
| `one_per_monitor` | По одному экрану на монитор | one window per stream, one monitor each | ≥ 2 monitors |

`side_by_side`, `stacked` and `focus` are **stage layouts**: one host window, one
transport, one timeline. `separate` and `one_per_monitor` are **window layouts**: several
top-level windows plus the floating transport — which is what the player does today, so
that path is kept and extended rather than rewritten.

A recording with **one** stream never asks anything: it opens exactly as it does now.
This is the first thing to check after any change here.

---

## 2. Choosing, and remembering

### 2.1 The dialog

Shown **before playback starts**, only when the recording has two or more streams. Cards
with drawn thumbnails of each layout, the recommended one marked and preselected, a line
under the title saying how many monitors are available, and a **Запомнить мой выбор**
checkbox. `one_per_monitor` is shown always but disabled with the reason written on it
when there is only one monitor — a disabled card that says why teaches more than a card
that is not there.

`separate` is offered on one monitor too. Two free-floating windows on a single screen is
a real way to watch this, and the brief says so.

### 2.2 The recommendation

It is computed, not decreed, and the dialog **says the reason in one line** under the
recommended card. No layout is universally best: side by side is for comparing, one
screen per monitor is for reading both at their own size.

For a usable area `A` (the target monitor's `availableGeometry`, so the taskbar is
already gone) and streams of native size `w_i × h_i`:

```
scale(stream i in box B) = min(B.w / w_i, B.h / h_i)      # fit, never crop, never upscale past 1.0
```

* `side_by_side`: each box is `A.w/2 × A.h`; score = geometric mean of the two scales.
* `stacked`: each box is `A.w × A.h/2`; score = geometric mean.
* `focus`: the main box is `A`, the other is not visible; **score = 0** — always
  offered, never recommended. See the paragraph below.
* `one_per_monitor`: each stream gets its own monitor's area; score = geometric mean.
  Only computed when there are enough monitors. **Which stream gets which monitor is
  the best of the arrangements, not the order the two lists happen to arrive in** — the
  screens come from Qt and the streams from the sidecar, and nothing lines those up. On
  the desk below, pairing them by index scores 0.5625 where the other pairing scores
  1.0. Once the user changes the mapping (§4), the score and the reason line are
  recomputed from *their* mapping, so the card never promises a size the windows will
  not have.
* `separate` and `focus` are never *recommended* — each is a deliberate choice, not a
  default — but both are always offered, with a card and a reason line of their own.

**Why `focus` scores 0, which it did not at first.** The recommendation is the answer to
"how do I watch what I just recorded", and a layout that shows one stream and hides the
other is not an answer to that question. The first version made it `scale(main) * 0.9`,
reading the hidden stream as a penalty to be traded off, and on a single 2560×1440 — the
commonest desk there is, and the one this whole feature exists for — that came to 0.900,
ahead of every layout that shows both. So the player's advice on an ordinary machine was
to watch half of the recording, which is exactly what `separate` is kept out of the
running for. A penalty small enough to leave the hidden stream in front is not a penalty,
and no number between 0.9 and 0 would have been anything but a guess at where the two
curves cross. `focus` is still a good way to *watch* — that is why it has a card, a
switch and a place in §3 — it is simply never the advice.

With that, a single 2560×1440 recommends **`side_by_side` at 0.530** (half the width
each, 50 % and 56 % of natural size), and the user's two-monitor desk still recommends
**`one_per_monitor` at 1.000**. Both numbers are pinned in
`tests/test_player_layouts.py`.

The geometric mean, not the arithmetic one: a layout that shows one screen at full size
and the other at a tenth must not score the same as one that shows both at half.

Ties go to `side_by_side`.

The reason line is built from the same numbers, e.g. "оба экрана целиком, по 48 % от
натуральной величины" or "каждый экран почти в натуральную величину". It is built when
the card is drawn, through `i18n._()`, so it is in whichever language is in force — the
English is "both screens in full, 48% of natural size each". Nothing may cache it.

### 2.3 Remembering

Stored in `config.Settings` (the player standalone reads the same file):

```python
player_layout: str            # a layout id, "" = ask every time
player_display_signature: str # the displays this choice was made on
player_swapped: bool          # which stream is the main one
player_split: float           # the splitter's fraction, 0.15..0.85
player_tiling: str            # the separate-windows arrangement, §4
```

The **display signature** is the one thing that makes "remember" safe. It is built from
every screen's `availableGeometry` and device pixel ratio, sorted, so moving a monitor,
unplugging one or changing the scaling all change it:

```
"2560x1440+0+0@1.0|1440x2560+2560+0@1.0"
```

* signature matches → apply the saved layout, no dialog, playback starts straight away;
* signature differs → show the dialog, preselect the recommendation **for the new
  configuration**, and say in one line that the displays changed since;
* saved layout no longer possible (`one_per_monitor` with one monitor) → same, and the
  impossible card is disabled.

Geometry remembered inside a layout (the splitter fraction) is restored only when the
signature matches. A fraction is safe to carry across configurations; a pixel rectangle
is not, and none is stored.

---

## 3. One window, two pictures

`side_by_side`, `stacked` and `focus` share one host window — the **stage** — with one
timeline, one play/pause, one seek, one speed, one volume, a layout switch and
**Поменять экраны местами**.

* **Aspect ratio is kept, nothing is cropped and nothing is stretched.** Letterboxing on
  black is the default and the only behaviour this round ships. Fill-and-crop may exist
  later as an explicit setting; it must never be the default and must never be silent.
* Each pane carries its stream's name — **Экран 1** / **Экран 2** — and the name follows
  the *stream*, not the position. After a swap, Экран 1 is still Экран 1, now on the
  right. Anything else makes the swap unreadable.
* `side_by_side` and `stacked` have a **draggable splitter** with a minimum of
  `MIN_PANE` px per side, a double-click to return to equal halves, and a visible
  **Выровнять** action that does the same for anyone who does not know the double-click.
* `focus` shows a compact two-button switch (**1** | **2**) that is reachable from the
  keyboard: `1` and `2` select, `Tab` moves to it, `Space` still plays.

### Where the picture comes from

`engine.SyncEngine` owns one `QMediaPlayer` and one `QVideoSink` per stream. A view does
not own a player: it receives `videoFrameChanged` and hands the frame to its own widget's
sink (`ScreenView._connect_sink` already does exactly this). Panes in the stage do the
same, so **changing layout does not touch the players at all** — no reload, no seek, no
position lost, which is what §4 demands. Moving a stream from one pane to another is
reconnecting a signal.

---

## 4. Separate windows

This is today's `PlayerWindows`, kept, plus the arrangement menu from the reference
picture — four schemes, drawn, for **two** windows only:

| id | arrangement |
|---|---|
| `equal_lr` | two equal halves, left and right |
| `big_left` | two thirds left, one third right |
| `big_right` | one third left, two thirds right |
| `equal_tb` | two equal halves, top and bottom |

Applied inside the chosen monitor's `availableGeometry` — the taskbar is already excluded
there, and no other application's windows are touched, ever.

* The user may then move and resize the windows freely.
* **Вернуть раскладку** re-applies the last chosen scheme.
* Closing the **second** window hides it and playback continues; the transport then
  offers **Показать второй экран**. Closing the **first** ends the session and closes
  everything — one window has to be the one that means "stop", and it is the main one.
* `one_per_monitor` is the same machinery with one window per monitor, each maximised to
  that monitor's usable area, and a mapping of stream → monitor the user can change.

---

## 5. The clock, the sound, the displays

* One clock for both streams: `SyncEngine` already is that, and none of this may create a
  second one. Play, pause, seek, speed and frame-step go to the engine, never to a pane.
* **Changing the layout, swapping the main stream or re-tiling must not seek, reload or
  restart anything.** Verified by reading the position before and after and finding it
  unchanged.
* **Seeking shows a loading state.** Until both players report they have landed, the
  panes are dimmed with a quiet indicator. Showing frames from two different moments as
  if they were synchronised is worse than showing nothing for 200 ms.
* **Unequal lengths.** The transport's duration is the longest stream. When the shorter
  one ends it holds its last frame with a small badge — «Экран 2 — запись закончилась» —
  and the other carries on. Different frame rates are already handled by the engine's
  per-stream `framerate`; frame-step uses the main stream's.
* **Sound is played once.** The engine gives an audio output to the master only, and the
  master is the file the sidecar names in `"audio"`. **Swapping the main picture must not
  change the audio master** — the sound does not move when the picture does, and nothing
  in the UI implies it does.
* **Displays.** Negative coordinates, mixed DPI and a monitor unplugged mid-playback are
  all real. Every window is clamped into a real screen's `availableGeometry` before it is
  shown (`transport_spot()` is the pattern: never place from the bounding box of several
  screens). On `screenRemoved`, any window on the lost screen is moved to the primary one
  and a notice says so. No window may ever end up where it cannot be reached.

---

## 6. Keyboard

Unchanged where it exists, plus:

| key | does |
|---|---|
| `Space` | play / pause |
| `←` `→` | skip |
| `,` `.` | frame step |
| `Home` `End` | the start, the end |
| `Ctrl`+`O` | open something else |
| `1` `2` | make that stream the main one (stage layouts) |
| `S` | swap the two streams |
| `L` | cycle the layout |
| `F11` | fill a monitor with one picture, and back out again |
| `Esc` | leave full screen if a monitor is filled, otherwise close the player |

`F11` and the bar's button do the same thing, and so does a double click on the picture.
Which window fills: the one under the pointer, else the one with focus, else the first.
Coming back out wins over going in whenever anything is already filled, so one key and
one button are enough to get out of it from anywhere.

**An arrangement ends a full screen.** `S`, `L`, the schemes and one-screen-per-monitor
are statements about where the windows go, so a filled window comes out first and is
laid out with the rest. This is not a choice so much as a fact being owned: Qt drops the
full screen the moment an arrangement sets that window's geometry, and the thing worth
writing down is that the player now notices - it used to go on offering the way out of a
full screen nobody was in, and taking that offer went *in*.

This half of the player had none of it until 2026-11-05 - it was written down as
deliberate, on the grounds that a frameless window has nothing to go full screen out of
- and the person using it said plainly that they could not find it. A frameless window
filling half a monitor is not full screen either.

---

## 7. The source files stay source files

The layout is a way of watching. ScreenRec does not join the streams, does not re-encode
them and does not alter the recording when the layout changes. Both MP4s remain exactly
what the encoder wrote, and both can be dropped into any editor that reads H.264/H.265 in
MP4.

What an editor cannot read is the **sidecar**: the per-stream start offset and which file
carries the audio live in `.screenrec.json`, not in the files. So a two-screen recording
taken into an editor must be lined up by hand unless the editor is told the offset. That
is a limitation to state plainly, not to paper over; an "export the streams with the
offset baked in" command is a separate job and is **not** part of this one.

---

## 8. What must be checked

* a two-stream recording opened on one monitor, and on two;
* every layout, and the swap, in each;
* swapping the main stream while playing **and** while paused — the position must not
  move and playback must not restart;
* dragging the splitter to each end and back, and the double-click that evens it up;
* seeking and changing speed with both streams staying together;
* the sound heard once, at one volume, with the master unchanged by a swap;
* the second monitor unplugged mid-playback;
* a layout restored under a *different* display configuration;
* **a one-stream recording — its behaviour must not get worse.**

Anything needing hardware this machine does not have (a third monitor, a scaled display,
a monitor physically unplugged) is reported as untested, by name. It is not claimed.
