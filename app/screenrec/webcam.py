"""The camera in the corner of the recording.

**Why this is a window on the desktop and not a second input to ffmpeg.**

`ddagrab` captures the composed desktop, which is why the click highlight and the
annotation canvas reach the file without ffmpeg knowing they exist. The camera is drawn
the same way, and it buys the thing this program is built around: on the common path a
frame goes from the Desktop Duplication API into NVENC without ever being copied to
system memory, and a second input with an `overlay` filter would end that for every
recording with a camera in it. Measured on this build, there is no GPU overlay path to
fall back on either - `overlay_cuda` needs cuda frames and `hwmap=derive_device=cuda`
fails here, the same wall `docs/ENCODERS.md` records for 4:4:4.

The cost is honest and worth stating: **the camera is visible on the real screen while
it records.** It is a window, so anyone sitting at the machine sees it, exactly as they
see the click rings. For a recording of a lesson that is usually what is wanted - the
picture is where the viewer will see it - but it is a difference from OBS, where the
camera exists only inside the recording.

Two things this module must never do: take the mouse, and take focus. A camera that
swallows a click on what is underneath it would be worse than no camera.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtMultimedia, QtWidgets

from . import i18n
from .i18n import _
from .overlay import _OverlayBase

__all__ = [
    "CORNERS", "CORNER_TOP_LEFT", "CORNER_TOP_RIGHT",
    "CORNER_BOTTOM_LEFT", "CORNER_BOTTOM_RIGHT",
    "SIZES", "SIZE_FRACTIONS", "SHAPES", "SHAPE_ROUNDED", "SHAPE_CIRCLE",
    "SHAPE_RECTANGLE", "USABLE_FRAME_RATE", "WebcamOverlay", "cameras",
    "camera_named", "resolutions_of", "choose_format",
]

CORNER_TOP_LEFT = "top_left"
CORNER_TOP_RIGHT = "top_right"
CORNER_BOTTOM_LEFT = "bottom_left"
CORNER_BOTTOM_RIGHT = "bottom_right"
CORNERS: tuple[str, ...] = (
    CORNER_TOP_LEFT, CORNER_TOP_RIGHT, CORNER_BOTTOM_LEFT, CORNER_BOTTOM_RIGHT,
)

SHAPE_ROUNDED = "rounded"
SHAPE_CIRCLE = "circle"
SHAPE_RECTANGLE = "rectangle"
SHAPES: tuple[str, ...] = (SHAPE_ROUNDED, SHAPE_CIRCLE, SHAPE_RECTANGLE)

#: A camera mode slower than this is not video. The c922's two largest modes,
#: 2304x1536 and 2304x1296, run at 2 fps - measured 2026-11-08 - so "the biggest
#: resolution" is a trap and the automatic choice has to step over it.
USABLE_FRAME_RATE = 24.0

#: How much larger than the drawn picture the camera should run, so that it is not
#: being enlarged. More than this buys nothing: the picture is a few hundred pixels.
RESOLUTION_HEADROOM = 1.25

SIZES: tuple[str, ...] = ("tiny", "small", "medium", "large")
#: The picture's width as a fraction of the captured width, so it keeps its share of the
#: frame whatever the screen is. A quarter of the width is already a lot of a lesson.
#: How wide the picture is as a fraction of the recorded area. "tiny" was added on
#: 2026-11-09 because the camera was covering things that mattered: at 0.08 it is 205 px
#: on a 2560-wide screen, which is a face you can recognise and little more.
SIZE_FRACTIONS: dict[str, float] = {
    "tiny": 0.08, "small": 0.12, "medium": 0.17, "large": 0.24,
}

#: How far the picture sits from the edges of the captured area, as a fraction of its
#: width. Enough that it does not look stuck to the corner.
MARGIN_FRACTION = 0.22

#: The corner radius and the hairline, in pixels at the picture's own scale.
RADIUS_FRACTION = 0.06
BORDER = 2


def cameras() -> list[QtMultimedia.QCameraDevice]:
    """Every camera Windows reports, default first if it says which."""
    found = list(QtMultimedia.QMediaDevices.videoInputs())
    found.sort(key=lambda d: (not d.isDefault(), d.description()))
    return found


def camera_named(description: str) -> QtMultimedia.QCameraDevice | None:
    """The camera with this description, or the default, or None.

    Matched by description rather than by id: an id changes when a camera moves to a
    different USB port, and the person chose a name from a list.
    """
    found = cameras()
    if not found:
        return None
    if description:
        for device in found:
            if device.description() == description:
                return device
    return found[0]


def resolutions_of(device: object) -> list[tuple[int, int, float]]:
    """Every distinct resolution the camera offers, with the best frame rate for it.

    Largest first, which is how a person reads a list of resolutions. The frame rate
    is carried along because it is the thing that makes the biggest mode the wrong
    one, and a list that hides it invites exactly that mistake.
    """
    best: dict[tuple[int, int], float] = {}
    try:
        formats = list(device.videoFormats())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return []
    for fmt in formats:
        try:
            size = fmt.resolution()
            key = (int(size.width()), int(size.height()))
            rate = float(fmt.maxFrameRate())
        except Exception:  # noqa: BLE001
            continue
        if key[0] <= 0 or key[1] <= 0:
            continue
        if rate > best.get(key, 0.0):
            best[key] = rate
    return [(w, h, best[(w, h)]) for (w, h) in sorted(best, key=lambda k: -k[0] * k[1])]


def choose_format(device: object, wanted: str, picture_width: int) -> object | None:
    """The camera mode to run in, or None to let Qt decide.

    `wanted` is "WIDTHxHEIGHT" when somebody picked one, or "" for automatic. Automatic
    takes the **smallest mode that still covers the picture**, among those that can do
    `USABLE_FRAME_RATE`; a camera running at 2 fps in the corner is worse than a
    slightly soft one, and a 1080p stream for a 400-pixel picture is waste nobody sees.
    """
    try:
        formats = list(device.videoFormats())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    if not formats:
        return None

    def size_of(fmt: object) -> tuple[int, int]:
        res = fmt.resolution()  # type: ignore[attr-defined]
        return int(res.width()), int(res.height())

    if wanted:
        for fmt in formats:
            if "%dx%d" % size_of(fmt) == wanted:
                return fmt
        # The camera no longer offers what was saved - a different camera, or a driver
        # update. Fall through to automatic rather than refusing to start.

    usable = [f for f in formats if float(f.maxFrameRate()) >= USABLE_FRAME_RATE]
    if not usable:
        usable = formats
    need = max(1, int(picture_width * RESOLUTION_HEADROOM))
    big_enough = [f for f in usable if size_of(f)[0] >= need]
    if big_enough:
        return min(big_enough, key=lambda f: size_of(f)[0] * size_of(f)[1])
    return max(usable, key=lambda f: size_of(f)[0] * size_of(f)[1])


class WebcamOverlay(_OverlayBase):
    """A click-through window that paints the camera in one corner of the capture.

    It covers the whole desktop, like the other overlays, and paints in one rectangle
    of it. `set_area` is what decides where: the caller knows what is being recorded -
    a monitor, a dragged-out region, a window - and this class is not going to guess.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("ScreenRec - camera"))
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        #: Empty, or why there is no picture. The caller shows it; this class never
        #: puts a message on the screen, because the screen is being recorded.
        self.error = ""
        #: The last exception a paint raised, if any. A paint must never raise out.
        self.paint_problem = ""

        self._camera: QtMultimedia.QCamera | None = None
        self._session: QtMultimedia.QMediaCaptureSession | None = None
        self._sink: QtMultimedia.QVideoSink | None = None
        self._image = QtGui.QImage()
        self._area = QtCore.QRect(self.desktop_rect)
        self._corner = CORNER_BOTTOM_RIGHT
        self._size = "medium"
        self._shape = SHAPE_ROUNDED
        #: What the camera was actually set to run at, for the log and for a test.
        #: Empty until `start` has chosen.
        self.format_chosen = ""
        #: How many frames have been painted. The way a test, or a person reading the
        #: log, can tell a running camera from a black rectangle.
        self.frames_drawn = 0

        self.setGeometry(self._window_rect())
        i18n.notifier.changed.connect(self.retranslate)

    def retranslate(self) -> None:
        self.setWindowTitle(_("ScreenRec - camera"))

    # -- what and where ----------------------------------------------------

    def set_area(self, area: QtCore.QRect) -> None:
        """The rectangle being recorded, in desktop pixels.

        The picture goes in a corner of *this*, not of the desktop: recording one
        window with the camera parked on another screen would be a camera nobody sees.
        """
        if area.isValid() and not area.isEmpty():
            self._area = QtCore.QRect(area)
            self.update()

    def set_placement(self, corner: str, size: str, shape: str = "") -> None:
        self._corner = corner if corner in CORNERS else CORNER_BOTTOM_RIGHT
        self._size = size if size in SIZE_FRACTIONS else "medium"
        if shape:
            self._shape = shape if shape in SHAPES else SHAPE_ROUNDED
        self.update()

    def picture_rect(self) -> QtCore.QRect:
        """Where the camera will be drawn, in desktop pixels.

        Public because it is the one thing worth asserting about placement, and
        because the window is transparent: there is nothing else to look at.
        """
        fraction = SIZE_FRACTIONS.get(self._size, SIZE_FRACTIONS["medium"])
        width = max(96, int(round(self._area.width() * fraction)))
        if self._shape == SHAPE_CIRCLE:
            # A circle is as tall as it is wide whatever the camera's shape; the frame
            # is cropped to its middle square rather than squashed into one.
            height = width
        elif self._image.isNull():
            height = int(round(width * 9 / 16))
        else:
            height = max(1, int(round(width * self._image.height() / self._image.width())))
        margin = int(round(width * MARGIN_FRACTION))

        left = self._area.left() + margin
        right = self._area.right() - margin - width + 1
        top = self._area.top() + margin
        bottom = self._area.bottom() - margin - height + 1
        x = left if self._corner in (CORNER_TOP_LEFT, CORNER_BOTTOM_LEFT) else right
        y = top if self._corner in (CORNER_TOP_LEFT, CORNER_TOP_RIGHT) else bottom
        return QtCore.QRect(x, y, width, height)

    # -- lifecycle ---------------------------------------------------------

    def start(self, description: str = "", resolution: str = "") -> str:
        """Open the camera and show the picture. Returns "" or why it could not.

        A camera that cannot be opened is not an error worth stopping a recording for:
        the caller shows the reason and records without it.
        """
        self.error = ""
        if self.desktop_rect.isEmpty():
            self.error = self.geometry_problem
            return self.error

        device = camera_named(description)
        if device is None:
            self.error = _("No camera was found on this machine.")
            return self.error

        try:
            self._sink = QtMultimedia.QVideoSink(self)
            self._sink.videoFrameChanged.connect(self._on_frame)
            self._camera = QtMultimedia.QCamera(device, self)
            chosen = choose_format(device, resolution, self.picture_rect().width())
            if chosen is not None:
                self._camera.setCameraFormat(chosen)
                size = chosen.resolution()
                self.format_chosen = "%dx%d" % (size.width(), size.height())
            self._session = QtMultimedia.QMediaCaptureSession(self)
            self._session.setCamera(self._camera)
            self._session.setVideoSink(self._sink)
            self._camera.errorOccurred.connect(self._on_camera_error)
            self._camera.start()
        except Exception as exc:  # noqa: BLE001 - a camera is somebody else's hardware
            self.error = _("The camera could not be started: {reason}").format(reason=exc)
            self.stop()
            return self.error

        self.setGeometry(self._window_rect())
        self.show()
        self.raise_()
        return ""

    def stop(self) -> None:
        """Close the camera and hide the window. Safe to call when nothing is running."""
        if self._camera is not None:
            try:
                self._camera.stop()
            except Exception:  # noqa: BLE001
                pass
        if self._session is not None:
            try:
                self._session.setCamera(None)
                self._session.setVideoSink(None)
            except Exception:  # noqa: BLE001
                pass
        self._camera = None
        self._session = None
        self._sink = None
        self._image = QtGui.QImage()
        self.hide()

    def running(self) -> bool:
        return self._camera is not None and self._camera.isActive()

    # -- frames ------------------------------------------------------------

    def _on_camera_error(self, *args: object) -> None:
        camera = self._camera
        if camera is not None:
            self.error = _("The camera stopped: {reason}").format(
                reason=camera.errorString() or "?"
            )

    def _on_frame(self, frame: object) -> None:
        """Keep the newest frame and repaint only the corner it goes in.

        `toImage` is the conversion Qt itself uses to show a frame in a widget. It
        costs a copy per frame, which is the price of drawing the camera with a
        `QPainter`; at the size this is drawn, that copy is small and it is on the GUI
        thread's own schedule, not the recording's.
        """
        try:
            image = frame.toImage()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return
        if image.isNull():
            return
        was = self.picture_rect()
        self._image = image
        self.frames_drawn += 1
        # Both rectangles: the picture can change shape when the first frame arrives
        # and the 16:9 guess is replaced by the camera's real aspect.
        self.update(was.united(self.picture_rect()).adjusted(-4, -4, 4, 4))

    # -- painting ----------------------------------------------------------

    def _outline(self, box: QtCore.QRectF) -> tuple[QtGui.QPainterPath, float]:
        """The shape to clip to and to stroke, and the corner radius behind it."""
        path = QtGui.QPainterPath()
        if self._shape == SHAPE_CIRCLE:
            path.addEllipse(box)
            return path, box.width() / 2
        radius = 0.0 if self._shape == SHAPE_RECTANGLE else box.width() * RADIUS_FRACTION
        path.addRoundedRect(box, radius, radius)
        return path, radius

    def _source_rect_for(self, box: QtCore.QRectF) -> QtGui.QImage:
        """The part of the camera frame to draw into `box`.

        A circle wants the middle square of the picture, not the whole of a 16:9 frame
        squashed into one - squashing turns a face into a different face.
        """
        if self._shape != SHAPE_CIRCLE or self._image.isNull():
            return self._image
        width, height = self._image.width(), self._image.height()
        side = min(width, height)
        return self._image.copy(
            (width - side) // 2, (height - side) // 2, side, side
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if self._image.isNull():
            return
        try:
            painter = QtGui.QPainter(self)
            try:
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
                painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
                box = QtCore.QRectF(self.picture_rect())
                path, radius = self._outline(box)
                painter.setClipPath(path)
                painter.drawImage(box, self._source_rect_for(box))
                painter.setClipping(False)

                # A hairline, so the picture reads as a frame rather than as a hole cut
                # in the screen when the camera sees something dark.
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 46), BORDER)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                inner, _radius = self._outline(box.adjusted(1, 1, -1, -1))
                painter.drawPath(inner)
            finally:
                painter.end()
        except Exception as exc:  # noqa: BLE001 - a paint must never raise out
            if not self.paint_problem:
                self.paint_problem = repr(exc)
