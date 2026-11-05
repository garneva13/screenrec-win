"""The Russian block for the player_open area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.

This is §1 of `docs/PLAYER_SOURCES.md` - opening files the recorder never paired, and
writing the pairing back out. The sentences about a path that is *wrong* are older than
this feature and already live in the `player` block: «Файла {path} нет.», «Запись не
указана.», «{path} не открывается.». They are used from `open.py` by those keys rather
than copied here, because a key lives in one block and one block only.

What is new is everything a viewer can only provoke by choosing files themselves: a
file in a pair that is not a video, a pair where nothing opened at all, the four ways
«Сохранить набор» can come back with nothing saved, and the one way it comes back with
the set saved and only its date wrong. Every one of them is a whole
sentence - these are read in a message box beside a Save button, where «не папка» on
its own would tell the reader nothing about what did or did not happen to their work.

`library.py` shares this block and puts nothing in it: it returns rows, and every word
on a row is either a file's own name or a number the interface agent formats.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # -- building a set out of whatever was dropped on the window -------------
    # "left out" rather than "refused": the other camera still opens, which is
    # the whole point of naming the file instead of failing the gesture.
    "{name} is not a video file, so it was left out.":
        "{name} — не видеофайл, поэтому он не открыт.",
    "None of those files could be opened.":
        "Ни один из этих файлов открыть не удалось.",

    # -- §1.3, saving the pair ------------------------------------------------
    # The viewer pressed Save and nothing was written; each of these says which
    # of the four reasons it was, because "не сохранено" alone sends somebody
    # looking for a file that was never created.
    "There is nothing to save: none of those files are there.":
        "Сохранять нечего: ни одного из этих файлов нет на месте.",
    "{name} is not there, so it was left out of the set.":
        "{name} нет на месте, поэтому он не вошёл в набор.",
    "{folder} is not a folder, so the set was not saved.":
        "{folder} — не папка, поэтому набор не сохранён.",
    "A name could not be found for the set beside {name}; every one is taken.":
        "Рядом с {name} не нашлось свободного имени для набора — все заняты.",
    # Carries an OSError's own text, which stays English inside the brackets;
    # the sentence around it is what the viewer actually reads.
    "The set could not be saved: {reason}":
        "Набор не сохранён: {reason}",
    # The half-success: the index is on disk and only its date is wrong - the
    # moment Save was pressed instead of the shoot's. «Набор сохранён» comes
    # first because that is the part the viewer has to believe before the rest
    # of the sentence means anything.
    "The set was saved, but its date could not be set: {reason}":
        "Набор сохранён, но проставить его дату не удалось: {reason}",
    # `multi.write_set` is the player's saver (§1.3) even though it lives in the
    # recorder's module, so its one refusal is a viewer's sentence and belongs in
    # this block rather than in `recorder.py`. The recorder's own messages from
    # that file - the sidecar written at the end of a take - stay where they are.
    "There is nothing to save: this set has no files.":
        "Сохранять нечего: в этом наборе нет файлов.",

    # -- what ffprobe could not say about a file ------------------------------
    # These arrive in the box beside a window that did open: the file plays, and
    # the sentence says which of its two measurements is a stand-in. The text in
    # brackets is ffprobe's or the operating system's own and stays English, like
    # every other {reason} here; "ffprobe" is a program's name and is not a word.
    "The video's size could not be read: {reason}":
        "Размер кадра прочитать не удалось: {reason}",
    "{name}: ffprobe could not be run ({reason}).":
        "{name}: не удалось запустить ffprobe ({reason}).",
    "{name}: the frame size could not be read ({reason}); the window is "
    "sized {width}x{height}.":
        "{name}: размер кадра прочитать не удалось ({reason}); окно открыто "
        "размером {width}x{height}.",
    "{name}: the frame rate could not be read; a frame step uses {rate} fps.":
        "{name}: частоту кадров прочитать не удалось; шаг на кадр считается "
        "по {rate} кадр/с.",
}
