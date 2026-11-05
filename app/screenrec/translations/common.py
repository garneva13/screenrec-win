"""The Russian block for the common area. See `translations/__init__.py`.

The key is the English source text, exactly as it is written at the call site. A key
may appear in exactly one block; the merge says so out loud if it does not.
"""
from __future__ import annotations

RUSSIAN: dict[str, str] = {
    # The window titles of every top-level surface. They live together in the common
    # block rather than one per area because they are one family read in one place -
    # the taskbar and Alt+Tab, side by side - and a reader checking that they match
    # should not have to open five files. "ScreenRec - annotation text" stays in the
    # annotate block, where it was written; a key may live in only one block.
    "ScreenRec - annotations": "ScreenRec — примечания",
    "ScreenRec - select a region": "ScreenRec — выбор области",
    "ScreenRec - click highlight": "ScreenRec — подсветка кликов",
    "ScreenRec - capture frame": "ScreenRec — рамка записи",
    "ScreenRec player": "ScreenRec — плеер",
    # One player window per screen; `name` is that screen's own label.
    "ScreenRec - {name}": "ScreenRec — {name}",
}
