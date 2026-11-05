"""Binary lookup and the fixed locations.

`_find` is the only place in the app that decides which ffmpeg runs, and its failure
message is the only thing a user gets when the bundle is missing, so both are pinned here.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from screenrec import paths


class FindTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="screenrec-test-")
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def make(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ")  # not a real exe; _find only ever stats it
        return path

    def test_an_override_file_wins_over_the_bundle(self) -> None:
        override = self.make("custom", "ffmpeg.exe")
        self.assertEqual(paths._find("ffmpeg.exe", override), override)

    def test_an_override_directory_is_joined_with_the_name(self) -> None:
        override = self.make("custom", "ffmpeg.exe")
        found = paths._find("ffmpeg.exe", override.parent)
        self.assertEqual(found, override)

    def test_an_override_that_does_not_exist_falls_through_to_the_bundle(self) -> None:
        bundled = paths.TOOLS_DIR / "ffmpeg.exe"
        if not bundled.is_file():
            self.skipTest(f"no bundled ffmpeg at {bundled}")
        found = paths._find("ffmpeg.exe", self.root / "nowhere" / "ffmpeg.exe")
        self.assertEqual(found, bundled)

    def test_an_empty_override_is_ignored(self) -> None:
        bundled = paths.TOOLS_DIR / "ffmpeg.exe"
        if not bundled.is_file():
            self.skipTest(f"no bundled ffmpeg at {bundled}")
        self.assertEqual(paths._find("ffmpeg.exe", ""), bundled)
        self.assertEqual(paths._find("ffmpeg.exe", None), bundled)

    def test_the_bundled_binaries_are_what_ffmpeg_exe_returns(self) -> None:
        bundled = paths.TOOLS_DIR / "ffmpeg.exe"
        if not bundled.is_file():
            self.skipTest(f"no bundled ffmpeg at {bundled}")
        self.assertEqual(paths.ffmpeg_exe(), bundled)
        self.assertTrue(paths.ffmpeg_exe().is_absolute())

    def test_ffprobe_is_looked_up_by_its_own_name(self) -> None:
        override = self.make("custom", "ffprobe.exe")
        self.assertEqual(paths.ffprobe_exe(override.parent), override)

    def test_missing_binary_names_every_path_it_tried(self) -> None:
        override = self.root / "nowhere" / "nosuchtool.exe"
        with self.assertRaises(paths.BinaryNotFound) as caught:
            paths._find("nosuchtool.exe", override)
        message = str(caught.exception)
        self.assertIn("nosuchtool.exe not found", message)
        self.assertIn(str(override), message)
        self.assertIn(str(paths.TOOLS_DIR / "nosuchtool.exe"), message)
        self.assertIn("PATH", message)

    def test_binary_not_found_is_a_runtime_error(self) -> None:
        self.assertTrue(issubclass(paths.BinaryNotFound, RuntimeError))


class LocationTest(unittest.TestCase):
    def test_project_root_holds_the_app_package(self) -> None:
        self.assertTrue((paths.PROJECT_ROOT / "app" / "screenrec" / "paths.py").is_file())

    def test_tools_dir_is_under_the_project(self) -> None:
        self.assertEqual(paths.TOOLS_DIR, paths.PROJECT_ROOT / "tools" / "ffmpeg")

    def test_config_path_is_absolute_and_named_settings_json(self) -> None:
        path = paths.config_path()
        self.assertTrue(path.is_absolute(), path)
        self.assertEqual(path.name, "settings.json")
        self.assertEqual(path.parent, paths.config_dir())

    def test_config_dir_is_under_appdata_and_carries_the_app_name(self) -> None:
        directory = paths.config_dir()
        self.assertTrue(directory.is_absolute(), directory)
        self.assertEqual(directory.name, paths.APP_NAME)
        appdata = os.environ.get("APPDATA")
        if appdata:
            self.assertEqual(directory.parent, Path(appdata))

    def test_default_output_dir_is_absolute_and_carries_the_app_name(self) -> None:
        directory = paths.default_output_dir()
        self.assertTrue(directory.is_absolute(), directory)
        self.assertEqual(directory.name, paths.APP_NAME)
        self.assertIn(directory.parent, (Path.home() / "Videos", Path.home()))


if __name__ == "__main__":
    unittest.main()
