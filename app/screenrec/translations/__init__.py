"""The Russian catalogue, split by area so that two people never edit one file.

One dictionary would have been simpler to read and impossible to work on: every change
to the recorder's wording and every change to the player's wording would land in the
same place. Each area owns a file here, this module merges them, and `i18n` reads only
the merged result.

**A key may appear in exactly one file.** Two areas translating the same English
sentence differently is a real possibility - "Open" in a file dialog and "Open" on a
recording - and a silent last-one-wins merge would make that invisible. So the merge
refuses a duplicate out loud. A sentence genuinely shared by two areas belongs in
`common.py`; a word whose two senses differ belongs in `i18n.pick()`.
"""
from __future__ import annotations

from . import (annotate, chrome, common, player, player_chooser, player_engine,
               player_open, player_stage, player_windows, recorder, settings)

#: Every area's block, in a fixed order so a duplicate is always reported the same way.
BLOCKS: tuple[tuple[str, dict[str, str]], ...] = (
    ("common", common.RUSSIAN),
    ("recorder", recorder.RUSSIAN),
    ("chrome", chrome.RUSSIAN),
    ("settings", settings.RUSSIAN),
    ("annotate", annotate.RUSSIAN),
    ("player", player.RUSSIAN),
    ("player_chooser", player_chooser.RUSSIAN),
    ("player_engine", player_engine.RUSSIAN),
    ("player_open", player_open.RUSSIAN),
    ("player_stage", player_stage.RUSSIAN),
    ("player_windows", player_windows.RUSSIAN),
)


def merge() -> tuple[dict[str, str], list[str]]:
    """All the blocks as one dictionary, plus a complaint for every duplicated key."""
    merged: dict[str, str] = {}
    owners: dict[str, str] = {}
    problems: list[str] = []
    for area, block in BLOCKS:
        for key, text in block.items():
            if key in merged:
                problems.append(
                    f"{area} translates {key!r} again; {owners[key]} already does. "
                    "Move it to translations/common.py, or tell the two senses apart "
                    "with i18n.pick()."
                )
                continue
            merged[key] = text
            owners[key] = area
    return merged, problems


RUSSIAN, DUPLICATES = merge()
