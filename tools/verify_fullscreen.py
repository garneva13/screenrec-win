"""Does full screen really fill a monitor, on a real screen rather than offscreen?

The unit tests run under Qt's offscreen plugin, where `showFullScreen` sets a flag and
nothing has to agree with it. The three things only a real desktop can answer are
whether the window actually takes the whole monitor, whether the always-on-top
transport bar stays visible over it, and whether it is on the right screen afterwards.

The bar is also grabbed to a PNG in both states, because the report was "не увидел" -
a control that works and cannot be seen is still missing.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import multi, paths  # noqa: E402
from screenrec.player import engine as engine_mod, window as player_window  # noqa: E402
from screenrec.ui import theme  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "scratch")
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> bool:
    RESULTS.append((bool(ok), text))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {text}", flush=True)
    return bool(ok)


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    with paths.own_settings_folder():
        app = QtWidgets.QApplication(sys.argv)
        theme.apply(app)

        sets = multi.find_sets(ROOT / "scratch" / "twofinal")
        if not sets:
            print("no recording set to open")
            return 2
        recording, problem = engine_mod.Recording.load(sets[0])
        if recording is None:
            print(f"could not load: {problem}")
            return 2

        engine = engine_mod.SyncEngine(recording)
        windows = player_window.PlayerWindows(engine)
        windows.show()
        pump(app, 2.0)

        view = windows.views[0]
        before = QtCore.QRect(view.geometry())
        screen = QtGui.QGuiApplication.screenAt(view.geometry().center())
        monitor = screen.geometry() if screen else QtGui.QGuiApplication.primaryScreen().geometry()

        windows.transport.full_button.click()
        pump(app, 1.2)

        filled = QtCore.QRect(view.geometry())
        check(view.full_screen, "the view says it is full screen")
        check(
            filled == monitor,
            f"it really fills the monitor ({filled.width()}x{filled.height()} "
            f"against {monitor.width()}x{monitor.height()})",
        )
        check(
            windows.transport.isVisible() and not windows.transport.isHidden(),
            "the bar is still up",
        )
        bar = windows.transport.geometry()
        check(
            monitor.contains(bar.center()),
            f"the bar followed it onto that monitor (bar at {bar.x()},{bar.y()})",
        )
        check(
            windows.transport.full_button.kind == "fullscreen_exit",
            "the button now shows the way out",
        )
        windows.transport.grab().save(str(OUT / "bar_filled.png"))

        # Escape: out of the full screen, and the player still open.
        event = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            int(QtCore.Qt.Key.Key_F11),
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(windows.transport, event)
        pump(app, 1.2)

        check(not view.full_screen, "F11 brought it back")
        check(
            view.geometry() == before,
            f"and back to where it was ({view.geometry()} against {before})",
        )
        check(
            windows.transport.full_button.kind == "fullscreen",
            "the button points out again",
        )
        windows.transport.grab().save(str(OUT / "bar_normal.png"))

        # The defect this half shipped with for one afternoon: an arrangement applied
        # to a filled window. Qt drops the full screen as `setGeometry` reaches the
        # window, so the only question is whether anything noticed. Offscreen it did
        # not; this is the same question put to Windows.
        windows.toggle_full_screen()
        pump(app, 1.0)
        if not windows.full_screen_views():
            check(False, "could not fill a window a second time")
        else:
            windows.cycle_arrangement()
            pump(app, 1.0)
            check(
                not windows.full_screen_views(),
                "an arrangement really does end the full screen",
            )
            check(
                windows.transport.full_button.kind == "fullscreen",
                "and the button stopped offering the way out of it",
            )

        # The finding a one-window measurement could not see: the bar is placed from
        # `boxes[0]`, so before this change it followed `views[0]` whatever had been
        # filled - and on two monitors that left the only way out on the other screen.
        if len(windows.views) > 1:
            # One window per monitor first, or both views sit on the same screen and
            # "the bar followed it" is true however the placement works.
            spread = windows.apply_one_per_monitor()
            pump(app, 1.0)
            second = windows.views[1]
            apart = (
                QtGui.QGuiApplication.screenAt(windows.views[0].geometry().center())
                is not QtGui.QGuiApplication.screenAt(second.geometry().center())
            )
            check(bool(spread) and apart,
                  "the two views really are on different monitors for this check")
            second.set_full_screen(True)
            pump(app, 1.2)
            its_monitor = QtGui.QGuiApplication.screenAt(second.geometry().center())
            bar_monitor = QtGui.QGuiApplication.screenAt(
                windows.transport.geometry().center()
            )
            check(
                second.full_screen and its_monitor is not None,
                "the second view fills a monitor too",
            )
            check(
                its_monitor is not None and bar_monitor is its_monitor,
                "the bar followed the second view onto its monitor "
                f"(bar on {bar_monitor.name() if bar_monitor else None}, "
                f"picture on {its_monitor.name() if its_monitor else None})",
            )
            second.set_full_screen(False)
            pump(app, 0.8)

        windows.close_all()
        pump(app, 0.5)

    good = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n{good} of {len(RESULTS)} checks passed")
    print(
        "\nNot covered here: what the filled picture looks like to a person, and\n"
        "whether a second monitor keeps showing its own view while the first is full."
    )
    return 0 if good == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
