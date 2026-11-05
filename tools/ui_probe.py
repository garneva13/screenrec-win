"""Drive the real interface with real clicks and report what does not work.

Offscreen tests grab widgets and call handlers; they cannot see a window that refuses
focus, a popover that dismisses itself, a slider that will not take a drag, or a panel
that cannot be dragged. Every one of those was found by the user and not by the 458
tests, which is the whole reason this exists.

It uses SendInput, so the pointer really moves and the clicks really land. Run it with
nothing important under the mouse.

    .venv\\Scripts\\python.exe tools\\ui_probe.py [--only canvas|toolbar|panels|all]
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import config, winapi  # noqa: E402
from screenrec.annotate import model as amodel  # noqa: E402
from screenrec.annotate.canvas import Canvas  # noqa: E402
from screenrec.annotate.toolbar import Toolbar  # noqa: E402
from screenrec.ui import theme  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WDA_EXCLUDEFROMCAPTURE = 0x11


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetWindowLongPtrW.restype = ctypes.c_longlong
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_ESCAPE = 0x1B

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def _absolute(x: int, y: int) -> tuple[int, int]:
    """Desktop pixels to the 0..65535 space SendInput wants, across all monitors."""
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    return (
        int(round((x - left) * 65535 / width)),
        int(round((y - top) * 65535 / height)),
    )


def _send(items: list[INPUT]) -> int:
    array = (INPUT * len(items))(*items)
    return user32.SendInput(len(items), array, ctypes.sizeof(INPUT))


_warmed = False


def warm_up() -> None:
    """Nudge the pointer once before anything is measured.

    The FIRST absolute SendInput move in a process is sometimes swallowed, so a probe
    whose opening action is a click lands nowhere and reports a defect that is the
    harness's own. Two agents hit this; a throwaway move costs nothing and removes it.
    """
    global _warmed
    if _warmed:
        return
    _warmed = True
    where = winapi.cursor_pos()
    move_to(where[0] + 2, where[1] + 2)
    time.sleep(0.05)
    move_to(*where)
    time.sleep(0.05)


def move_to(x: int, y: int) -> None:
    ax, ay = _absolute(x, y)
    item = INPUT()
    item.type = 0
    item.mi = MOUSEINPUT(
        ax, ay, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        0, None,
    )
    _send([item])


def click_at(app: QtWidgets.QApplication, x: int, y: int, settle: float = 0.12) -> None:
    move_to(x, y)
    pump(app, 0.06)
    down, up = INPUT(), INPUT()
    down.type = up.type = 0
    down.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    up.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    _send([down])
    pump(app, 0.05)
    _send([up])
    pump(app, settle)


def drag(app: QtWidgets.QApplication, x1: int, y1: int, x2: int, y2: int,
         steps: int = 14) -> None:
    move_to(x1, y1)
    pump(app, 0.08)
    down = INPUT()
    down.type = 0
    down.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    _send([down])
    pump(app, 0.06)
    for step in range(1, steps + 1):
        move_to(
            int(x1 + (x2 - x1) * step / steps), int(y1 + (y2 - y1) * step / steps)
        )
        pump(app, 0.02)
    up = INPUT()
    up.type = 0
    up.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    _send([up])
    pump(app, 0.15)


def press_key(app: QtWidgets.QApplication, vk: int, settle: float = 0.2) -> None:
    """A real key press, through the keyboard queue, to whatever has the focus.

    Our chrome never activates (`WS_EX_NOACTIVATE`), so this goes to whichever window
    *does* have the focus - usually the console that started the probe. That is the
    honest shape of the thing, and the Esc check below says so rather than pretending.
    """
    down, up = INPUT(), INPUT()
    down.type = up.type = INPUT_KEYBOARD
    down.ki = KEYBDINPUT(vk, 0, 0, 0, None)
    up.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)
    _send([down])
    pump(app, 0.05)
    _send([up])
    pump(app, settle)


def deliver_key(
    app: QtWidgets.QApplication, target: QtCore.QObject, key: QtCore.Qt.Key
) -> None:
    """Put a key event into Qt's own delivery, for what the keyboard cannot reach.

    Not a substitute for a real press - it is used only where a real press provably
    cannot arrive, and the check that uses it says which of the two it is reporting.
    """
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress, int(key), QtCore.Qt.KeyboardModifier.NoModifier
    )
    app.sendEvent(target, event)
    pump(app, 0.15)


def slider_handle(slider: QtWidgets.QSlider) -> QtCore.QPoint:
    """The global centre of the slider's handle, so a drag grabs it and not the groove.

    Pressing the groove only pages the value and leaves the handle behind, which is not
    the gesture the user made.
    """
    option = QtWidgets.QStyleOptionSlider()
    length = 16
    try:
        slider.initStyleOption(option)
        length = slider.style().pixelMetric(
            QtWidgets.QStyle.PixelMetric.PM_SliderLength, option, slider
        )
        rect = slider.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            option,
            QtWidgets.QStyle.SubControl.SC_SliderHandle,
            slider,
        )
        if rect.isValid() and rect.width() > 0:
            return slider.mapToGlobal(rect.center())
    except (AttributeError, TypeError) as exc:
        # Some styles/bindings do not expose the sub-control geometry; fall back to the
        # documented linear mapping rather than guess silently.
        print(f"   (handle geometry unavailable: {exc!r}; using the linear mapping)")
    span = max(1, slider.width() - length)
    offset = QtWidgets.QStyle.sliderPositionFromValue(
        slider.minimum(), slider.maximum(), slider.value(), span
    )
    return slider.mapToGlobal(
        QtCore.QPoint(int(offset + length / 2), slider.height() // 2)
    )


def pump(app: QtWidgets.QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.004)


def styles_of(widget: QtWidgets.QWidget) -> dict[str, bool]:
    handle = int(widget.winId())
    value = user32.GetWindowLongPtrW(wintypes.HWND(handle), GWL_EXSTYLE)
    affinity = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(wintypes.HWND(handle), ctypes.byref(affinity))
    return {
        "noactivate": bool(value & WS_EX_NOACTIVATE),
        "transparent": bool(value & WS_EX_TRANSPARENT),
        "layered": bool(value & WS_EX_LAYERED),
        "toolwindow": bool(value & WS_EX_TOOLWINDOW),
        "excluded": affinity.value == WDA_EXCLUDEFROMCAPTURE,
    }


def window_at(x: int, y: int) -> int:
    return int(user32.WindowFromPoint(wintypes.POINT(x, y)))


def centre(widget: QtWidgets.QWidget) -> tuple[int, int]:
    geometry = widget.frameGeometry()
    return geometry.center().x(), geometry.center().y()


RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> bool:
    RESULTS.append((ok, text))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {text}")
    return ok


# ---------------------------------------------------------------------- probes


def probe_canvas(app: QtWidgets.QApplication) -> None:
    print("\n=== the drawing canvas ===")
    document = amodel.Document()
    pen = document.settings_for(amodel.TOOL_PEN)
    pen.colour, pen.width = "#00FF66", 10
    canvas = Canvas(document)
    canvas.set_tool(amodel.TOOL_PEN)
    canvas.show()
    pump(app, 0.8)

    passthrough = styles_of(canvas)
    print(f"   passthrough styles: {passthrough}")
    check(passthrough["transparent"], "passthrough mode sets WS_EX_TRANSPARENT")
    check(not passthrough["excluded"], "the canvas is NOT hidden from capture")

    canvas.set_mode(Canvas.MODE_DRAW)
    pump(app, 0.4)
    drawing = styles_of(canvas)
    print(f"   draw styles:        {drawing}")
    check(
        not drawing["transparent"],
        "draw mode CLEARS WS_EX_TRANSPARENT (without this, clicks fall through "
        "and nothing is ever drawn)",
    )

    monitor = winapi.list_monitors()[0]
    y = monitor.y + monitor.height // 2
    x1, x2 = monitor.x + 400, monitor.x + 900
    under = window_at((x1 + x2) // 2, y)
    check(
        under == int(canvas.winId()),
        f"the canvas is the window under the pointer in draw mode "
        f"(found {under}, canvas is {int(canvas.winId())})",
    )

    before = len(document.items)
    drag(app, x1, y, x2, y, steps=18)
    pump(app, 0.3)
    after = len(document.items)
    check(after > before, f"a drag draws a stroke (items {before} -> {after})")
    if after > before:
        item = document.items[-1]
        check(len(item.points) > 3, f"the stroke has real points ({len(item.points)})")

    canvas.set_mode(Canvas.MODE_PASSTHROUGH)
    pump(app, 0.3)
    back = styles_of(canvas)
    check(back["transparent"], "leaving draw mode puts WS_EX_TRANSPARENT back")
    canvas.close()
    pump(app, 0.2)


def probe_toolbar(app: QtWidgets.QApplication) -> None:
    print("\n=== the drawing toolbar ===")
    document = amodel.Document()
    toolbar = Toolbar(document)
    monitor = winapi.list_monitors()[0]
    toolbar.place(monitor.x + 300, monitor.y + 200)
    toolbar.show()
    pump(app, 0.8)

    print(f"   styles: {styles_of(toolbar)}")
    check(styles_of(toolbar)["excluded"], "the toolbar IS hidden from capture")

    start = toolbar.pos()
    geometry = toolbar.frameGeometry()
    grip_x = geometry.x() + 18
    grip_y = geometry.center().y()
    drag(app, grip_x, grip_y, grip_x + 180, grip_y + 90)
    pump(app, 0.3)
    moved = toolbar.pos()
    check(
        (moved - start).manhattanLength() > 40,
        f"the toolbar can be dragged ({start.x()},{start.y()} -> "
        f"{moved.x()},{moved.y()})",
    )

    full_width = toolbar.frameGeometry().width()

    # The colour popover: open it, and see whether it survives being used.
    swatch = getattr(toolbar, "colour_button", None)
    popover = getattr(toolbar, "settings_popover", None)
    if swatch is None or popover is None:
        check(False, "the toolbar exposes a colour button and a settings popover")
    else:
        click_at(app, *swatch.mapToGlobal(swatch.rect().center()).toTuple())
        pump(app, 0.4)
        check(popover.isVisible(), "clicking the colour swatch opens the popover")

        # 1. A press inside it. The title strip: nothing there reacts, so if the popover
        #    goes away it is the popover closing itself and nothing else.
        if popover.isVisible():
            inside = popover.mapToGlobal(
                QtCore.QPoint(popover.width() // 2, 14)
            )
            click_at(app, inside.x(), inside.y())
            pump(app, 0.3)
            check(
                popover.isVisible(),
                "the popover survives a press INSIDE it (the bug: it vanished as soon "
                "as you reached for the slider)",
            )

        # 2. The gesture the user could not make: grab the thickness handle and pull.
        if popover.isVisible():
            slider = popover.width_slider
            before_value = slider.value()
            before_width = popover.document.settings_for(popover.tool()).width
            handle = slider_handle(slider)
            target_x = slider.mapToGlobal(
                QtCore.QPoint(slider.width() - 6, slider.height() // 2)
            ).x()
            drag(app, handle.x(), handle.y(), target_x, handle.y(), steps=16)
            pump(app, 0.3)
            after_value = slider.value()
            after_width = popover.document.settings_for(popover.tool()).width
            check(
                popover.isVisible(),
                "the popover is still open after DRAGGING the thickness slider",
            )
            check(
                after_value != before_value,
                f"the drag moved the thickness slider ({before_value} -> {after_value})",
            )
            check(
                after_width != before_width,
                f"the drag wrote the new thickness into the document "
                f"({before_width} -> {after_width})",
            )
        else:
            check(False, "the popover was still open to drag the thickness slider")

        # 3. Esc. A real key press goes to whatever has the focus, and this panel
        #    deliberately never takes it, so the real press is reported for what it is
        #    and the close path itself is then driven through Qt's own delivery.
        if not popover.isVisible():
            click_at(app, *swatch.mapToGlobal(swatch.rect().center()).toTuple())
            pump(app, 0.3)
        if popover.isVisible():
            press_key(app, VK_ESCAPE)
            real_escape_closed = not popover.isVisible()
            if real_escape_closed:
                print("   a real Esc through the keyboard queue closed the popover")
            else:
                print(
                    "   a real Esc through the keyboard queue did NOT reach us - "
                    "expected: the panel never takes focus, so the key went to "
                    "whichever window does have it"
                )
            if not real_escape_closed:
                deliver_key(app, popover, QtCore.Qt.Key.Key_Escape)
            check(
                not popover.isVisible(),
                "Esc closes the popover (through Qt delivery where the keyboard queue "
                "cannot reach a window that never activates)",
            )

        # 4. A press somewhere else entirely: another top-level window of ours, which is
        #    the case the broken filter could not tell from a press on the popover.
        click_at(app, *swatch.mapToGlobal(swatch.rect().center()).toTuple())
        pump(app, 0.3)
        if not popover.isVisible():
            check(False, "the popover reopens for the press-elsewhere check")
        else:
            decoy = QtWidgets.QWidget()
            decoy.setWindowTitle("ui_probe decoy")
            decoy.setWindowFlags(
                QtCore.Qt.WindowType.Tool
                | QtCore.Qt.WindowType.FramelessWindowHint
                | QtCore.Qt.WindowType.WindowStaysOnTopHint
            )
            decoy.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True
            )
            decoy.setStyleSheet("background: #303030;")
            decoy.resize(120, 60)
            decoy.move(monitor.x + 60, monitor.y + monitor.height - 220)
            decoy.show()
            pump(app, 0.4)
            point = decoy.mapToGlobal(decoy.rect().center())
            click_at(app, point.x(), point.y())
            pump(app, 0.3)
            check(
                not popover.isVisible(),
                "a press in another window closes the popover",
            )
            decoy.close()
            pump(app, 0.2)

    # Collapse, then try to get back - the way a person would, by looking for a control
    # that says it expands the panel rather than by knowing which pixel to guess at.
    toolbar.set_collapsed(True)
    pump(app, 0.4)
    check(toolbar.isVisible(), "the collapsed toolbar is still on screen")
    collapsed_geometry = toolbar.frameGeometry()
    print(
        f"   collapsed size: {collapsed_geometry.width()}x"
        f"{collapsed_geometry.height()} (full bar was {full_width} wide)"
    )
    check(
        collapsed_geometry.width() * 2 < full_width,
        f"the pill stays narrow ({collapsed_geometry.width()} px, under half of "
        f"{full_width} px)",
    )
    expander = None
    for widget in toolbar.findChildren(QtWidgets.QAbstractButton):
        if widget.isVisible() and widget.toolTip().startswith("Expand the panel"):
            expander = widget
            break
    if expander is None:
        tips = sorted(
            {
                w.toolTip()
                for w in toolbar.findChildren(QtWidgets.QAbstractButton)
                if w.isVisible() and w.toolTip()
            }
        )
        print(f"   visible tooltips on the pill: {tips}")
        check(False, "the collapsed pill offers a control whose tooltip says it expands")
    else:
        check(True, f"the pill offers {expander.toolTip()!r}")
        point = expander.mapToGlobal(expander.rect().center())
        click_at(app, point.x(), point.y())
        pump(app, 0.4)
        check(not toolbar.is_collapsed(), "clicking it expands the toolbar again")
        # Not exactly the width it started at: the thickness chip now reads "32 px"
        # instead of "4 px" because the drag above really did change the value, and the
        # chip sizes itself to its text. A few pixels of that is the bar working.
        width_now = toolbar.frameGeometry().width()
        check(
            abs(width_now - full_width) <= 24,
            f"the expanded bar is full size again ({width_now} px against the "
            f"{full_width} px it opened at; the thickness chip's text grew)",
        )
    toolbar.close()
    pump(app, 0.2)


def bare_header_point(
    widget: QtWidgets.QWidget, control: QtWidgets.QWidget
) -> tuple[int, int]:
    """A point on the header that is chrome and not a control, in desktop pixels.

    Measured from the real geometry rather than typed in, so a header that gets relaid
    out does not quietly turn this into a press on a button.
    """
    header = widget.header
    left_of_control = control.mapTo(widget, QtCore.QPoint(0, 0)).x() - 20
    point = QtCore.QPoint(left_of_control, header.geometry().center().y())
    glob = widget.mapToGlobal(point)
    return glob.x(), glob.y()


def probe_panels(app: QtWidgets.QApplication) -> None:
    print("\n=== the source picker and the settings panel ===")
    from screenrec.ui.bar import MARGIN, SNAP
    from screenrec.ui.settings_panel import SettingsPanel
    from screenrec.ui.sources import SourcePicker

    settings = config.Settings()
    monitor = winapi.list_monitors()[0]

    picker = SourcePicker(settings)
    panel = SettingsPanel(settings)
    for name, widget, control, control_name in (
        ("source picker", picker, picker.refresh_button, "refresh"),
        ("settings panel", panel, panel.close_button, "close"),
    ):
        fired: list[int] = []
        control.clicked.connect(lambda _checked=False, f=fired: f.append(1))
        widget.move(monitor.x + 500, monitor.y + 250)
        widget.show()
        pump(app, 0.7)
        styles = styles_of(widget)
        print(f"   {name} styles: {styles}")
        check(styles["excluded"], f"the {name} is hidden from capture")

        # 1. a drag by the header moves the window.
        start = widget.pos()
        grab_x, grab_y = bare_header_point(widget, control)
        drag(app, grab_x, grab_y, grab_x + 150, grab_y + 120)
        pump(app, 0.3)
        moved = widget.pos()
        check(
            (moved - start).manhattanLength() > 40,
            f"the {name} can be dragged by its header "
            f"({start.x()},{start.y()} -> {moved.x()},{moved.y()})",
        )

        # 2. a drag towards a corner snaps to it, and the window stays on the screen.
        widget.move(monitor.x + 320, monitor.y + 220)
        pump(app, 0.25)
        grab_x, grab_y = bare_header_point(widget, control)
        inside = 6  # well within SNAP of the MARGIN the bar parks on
        drag(
            app, grab_x, grab_y,
            grab_x + (monitor.x + inside - widget.x()),
            grab_y + (monitor.y + inside - widget.y()),
        )
        pump(app, 0.3)
        parked = widget.frameGeometry()
        check(
            (parked.x(), parked.y()) == (monitor.x + MARGIN, monitor.y + MARGIN),
            f"the {name} snaps to the corner within {SNAP} px "
            f"(at {parked.x()},{parked.y()}, wanted "
            f"{monitor.x + MARGIN},{monitor.y + MARGIN})",
        )
        check(
            parked.x() >= monitor.x
            and parked.y() >= monitor.y
            and parked.right() <= monitor.x + monitor.width
            and parked.bottom() <= monitor.y + monitor.height,
            f"the {name} is still wholly on the screen "
            f"({parked.x()},{parked.y()} {parked.width()}x{parked.height()} on "
            f"{monitor.width}x{monitor.height})",
        )

        # 3. a press on a control in the header is that control, not a drag.
        held = widget.pos()
        point = control.mapToGlobal(control.rect().center())
        click_at(app, point.x(), point.y())
        pump(app, 0.4)
        check(bool(fired), f"the {control_name} button in the {name} header fires")
        check(
            widget.pos() == held,
            f"pressing it does NOT move the {name} "
            f"({held.x()},{held.y()} -> {widget.pos().x()},{widget.pos().y()})",
        )
        check(
            styles_of(widget)["excluded"],
            f"the {name} is still hidden from capture after being dragged",
        )

        widget.close()
        pump(app, 0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="all")
    args = parser.parse_args()

    winapi.enable_dpi_awareness()
    app = QtWidgets.QApplication(sys.argv)
    theme.apply(app)

    before = winapi.cursor_pos()
    warm_up()
    try:
        if args.only in ("all", "canvas"):
            probe_canvas(app)
        if args.only in ("all", "toolbar"):
            probe_toolbar(app)
        if args.only in ("all", "panels"):
            probe_panels(app)
    finally:
        move_to(*before)

    failures = [text for ok, text in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failures)} of {len(RESULTS)} checks passed")
    for text in failures:
        print(f"  FAILED: {text}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
