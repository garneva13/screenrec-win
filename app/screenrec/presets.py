"""Named bundles of settings, so a different kind of recording is one click.

The three that ship are the three recordings this is actually used for, named for the
job rather than the settings: a tutorial with a voice over it, a silent demonstration,
and the best the machine can do. A preset is only a set of fields written into the live
`Settings`; it never touches the source, the output folder, the hotkey or the bar's
position, because those are the user's and not the preset's business.

Custom presets are saved beside the settings file. The built-in three cannot be
overwritten - a "Restore" that cannot restore is not worth having - so saving over one
makes a copy with a new name instead, and says so.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, paths

#: The only fields a preset may carry. Anything else - the source, the folder, the
#: hotkey, where the bar sits - belongs to the user, not to a preset.
FIELDS: tuple[str, ...] = (
    "framerate",
    "profile",
    "quality_level",
    "draw_mouse",
    "click_highlight",
    "capture_frame",
    "mic_enabled",
    "system_audio_enabled",
    "audio_mode",
    "countdown",
)


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    note: str
    values: dict[str, Any] = field(default_factory=dict)
    builtin: bool = False

    def apply_to(self, settings: config.Settings) -> list[str]:
        """Write the preset in. Returns the names of the fields it changed."""
        changed: list[str] = []
        for name, value in self.values.items():
            if name not in FIELDS or not hasattr(settings, name):
                continue
            if getattr(settings, name) != value:
                setattr(settings, name, value)
                changed.append(name)
        return changed

    def matches(self, settings: config.Settings) -> bool:
        return all(
            getattr(settings, name, None) == value
            for name, value in self.values.items()
            if name in FIELDS
        )


BUILTIN: tuple[Preset, ...] = (
    Preset(
        key="lesson",
        name="Lesson with voice",
        note="Microphone and system sound, cursor and clicks shown, three-second countdown.",
        builtin=True,
        values={
            "framerate": 60,
            "profile": "hevc420",
            "quality_level": "balanced",
            "draw_mouse": True,
            "click_highlight": True,
            "capture_frame": True,
            "mic_enabled": True,
            "system_audio_enabled": True,
            "audio_mode": config.AUDIO_MIX,
            "countdown": 3,
        },
    ),
    Preset(
        key="silent",
        name="Silent demonstration",
        note="No sound at all, no click rings - just the screen, and a small file.",
        builtin=True,
        values={
            "framerate": 60,
            "profile": "hevc420",
            "quality_level": "balanced",
            "draw_mouse": True,
            "click_highlight": False,
            "capture_frame": True,
            "mic_enabled": False,
            "system_audio_enabled": False,
            "countdown": 0,
        },
    ),
    Preset(
        key="maximum",
        name="Maximum quality",
        note=(
            "H.265 4:4:4 at the best setting: sharp coloured text and the smallest file "
            "for it, but Windows' own player cannot open 4:4:4 — use VLC, mpv or an "
            "editor."
        ),
        builtin=True,
        values={
            "framerate": 60,
            "profile": "hevc444",
            "quality_level": "best",
            "draw_mouse": True,
            "click_highlight": True,
            "capture_frame": True,
            "mic_enabled": True,
            "system_audio_enabled": True,
            "audio_mode": config.AUDIO_SEPARATE,
            "countdown": 0,
        },
    ),
)


def store_path() -> Path:
    return paths.config_dir() / "presets.json"


def load_custom(path: Path | None = None) -> tuple[list[Preset], str]:
    """The user's own presets. Never raises; a broken file is reported and ignored."""
    target = path or store_path()
    if not target.is_file():
        return [], ""
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"Your presets could not be read ({exc}); only the built-in ones."
    if not isinstance(raw, list):
        return [], "The presets file is not a list; only the built-in ones."
    found: list[Preset] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key", "")).strip()
        name = str(row.get("name", "")).strip()
        values = row.get("values")
        if not key or not name or not isinstance(values, dict):
            continue
        clean = {k: v for k, v in values.items() if k in FIELDS}
        found.append(
            Preset(key=key, name=name, note=str(row.get("note", "")), values=clean)
        )
    return found, ""


def save_custom(presets: list[Preset], path: Path | None = None) -> str:
    target = path or store_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(
                [
                    {"key": p.key, "name": p.name, "note": p.note, "values": p.values}
                    for p in presets
                    if not p.builtin
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp.replace(target)
    except OSError as exc:
        return f"Your presets could not be saved: {exc}"
    return ""


def all_presets(path: Path | None = None) -> tuple[list[Preset], str]:
    custom, problem = load_custom(path)
    return list(BUILTIN) + custom, problem


def capture(settings: config.Settings, key: str, name: str, note: str = "") -> Preset:
    """A preset made from the settings as they stand."""
    return Preset(
        key=key,
        name=name,
        note=note,
        values={field: getattr(settings, field) for field in FIELDS
                if hasattr(settings, field)},
    )


def unique_key(wanted: str, existing: list[Preset]) -> str:
    """A key no preset holds yet, so saving never silently replaces a built-in one."""
    taken = {p.key for p in existing}
    if wanted not in taken:
        return wanted
    index = 2
    while f"{wanted}-{index}" in taken:
        index += 1
    return f"{wanted}-{index}"


def current(settings: config.Settings, path: Path | None = None) -> Preset | None:
    """Which preset the settings currently are, if any. Drives the UI's tick mark."""
    presets, _ = all_presets(path)
    for preset in presets:
        if preset.matches(settings):
            return preset
    return None
