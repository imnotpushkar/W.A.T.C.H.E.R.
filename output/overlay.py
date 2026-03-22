"""
output/overlay.py — Always-on-top Transparent Overlay Window
=============================================================
Creates the floating suggestion window that appears over any app.

WHAT IS Qt AND WHY DO WE USE IT?
    Qt is a C++ framework for building GUI applications, created in 1991.
    It's used in VLC, Telegram, KDE, and thousands of other apps.
    PySide6 is the official Python binding — it lets us use Qt from Python.

    We use Qt specifically because it supports two things tkinter cannot:
    1. Qt.WindowStaysOnTopHint — window stays above ALL other windows
    2. WA_TranslucentBackground — window background is truly transparent
       (not just a color that looks transparent — actually see-through)

PYSIDE6 vs PYQT5 — KEY DIFFERENCES:
    Both wrap the same Qt framework. The API is ~95% identical. Differences:
    - PyQt5:   from PyQt5.QtCore import Qt
    - PySide6: from PySide6.QtCore import Qt
    - PyQt5:   widget.exec_()       (old style)
    - PySide6: widget.exec()        (modern style, no underscore)
    - Signals are defined the same way, connected the same way.
    We use PySide6 because it has Python 3.14 compatible pre-built wheels.

HOW THE TRANSPARENT OVERLAY WORKS:
    1. We create a QWidget with no window frame (FramelessWindowHint)
    2. We set WA_TranslucentBackground — Qt punches a hole in the window
       so anything behind it shows through
    3. We set WindowStaysOnTopHint — OS keeps it above other windows
    4. We paint our content (rounded rectangle + text) using QPainter
    5. We position it near the cursor using QCursor.pos()

WHAT IS A SIGNAL?
    Qt's communication mechanism between objects. A signal is emitted when
    something happens. Any number of slots (functions) can connect to it.
    When the signal fires, all connected slots are called automatically.
    Example: button.clicked is a signal. button.clicked.connect(my_func)
    means my_func() gets called every time the button is clicked.
    We use signals to safely update the UI from background threads —
    Qt requires all UI changes to happen on the main thread.
"""

from PySide6.QtWidgets import QWidget, QApplication, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint
from PySide6.QtGui import QPainter, QColor, QFont, QPainterPath, QCursor
from core.config import OVERLAY_OPACITY, OVERLAY_TIMEOUT_MS
from core.logger import get_logger

log = get_logger(__name__)

# Overlay visual constants — tweak these to change appearance
OVERLAY_WIDTH = 480
OVERLAY_MAX_HEIGHT = 200
OVERLAY_PADDING = 16
OVERLAY_BORDER_RADIUS = 12
OVERLAY_BG_COLOR = QColor(18, 18, 24, int(255 * OVERLAY_OPACITY))   # Dark background
OVERLAY_TEXT_COLOR = QColor(220, 220, 255)                            # Soft white-blue
OVERLAY_ACCENT_COLOR = QColor(99, 102, 241)                           # Indigo accent


class OverlaySignals(QObject):
    """
    Signals for thread-safe communication with the overlay.

    WHY A SEPARATE CLASS FOR SIGNALS?
        Qt signals must be defined as class attributes of a QObject subclass.
        Our overlay IS a QWidget (which IS a QObject), so we could define
        signals directly on it. But separating them into their own class makes
        the architecture cleaner — any module can import and emit these signals
        without needing a reference to the overlay widget itself.
    """
    # Signal carrying a string — emitted when we want to show new text
    show_text = Signal(str)
    # Signal with no arguments — emitted when we want to hide the overlay
    hide_overlay = Signal()
    # Signal carrying a string — emitted to append streaming tokens
    append_token = Signal(str)


class WatcherOverlay(QWidget):
    """
    The floating suggestion window. Transparent, always-on-top, frameless.
    """

    def __init__(self):
        # QWidget.__init__ must be called — it initialises the Qt object internals
        super().__init__()

        self.signals = OverlaySignals()
        self._current_text = ""
        self._auto_hide_timer = QTimer(self)

        self._setup_window()
        self._setup_layout()
        self._connect_signals()

        log.debug("WatcherOverlay initialised")

    def _setup_window(self) -> None:
        """
        Configures window flags and attributes for transparent always-on-top display.

        WINDOW FLAGS EXPLAINED:
            Qt.WindowStaysOnTopHint  — tells OS: keep this above all other windows
            Qt.FramelessWindowHint   — removes title bar, borders, close button
            Qt.Tool                  — marks as a tool window: doesn't appear in
                                       taskbar, doesn't steal focus from other apps

        WINDOW ATTRIBUTES EXPLAINED:
            WA_TranslucentBackground — enables true transparency (alpha channel)
            WA_ShowWithoutActivating — show without stealing keyboard focus from
                                       the app the user is currently typing in
        """
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(OVERLAY_WIDTH)

    def _setup_layout(self) -> None:
        """Sets up the text label inside the overlay."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            OVERLAY_PADDING, OVERLAY_PADDING,
            OVERLAY_PADDING, OVERLAY_PADDING
        )

        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        self.text_label.setFont(QFont("Segoe UI", 11))

        # Qt stylesheet syntax — similar to CSS but for Qt widgets
        self.text_label.setStyleSheet(f"""
            color: rgb({OVERLAY_TEXT_COLOR.red()},
                       {OVERLAY_TEXT_COLOR.green()},
                       {OVERLAY_TEXT_COLOR.blue()});
            background: transparent;
            padding: 4px;
        """)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.text_label)

    def _connect_signals(self) -> None:
        """
        Connects signals to their handler methods (slots).

        WHY CONNECT SIGNALS INSTEAD OF CALLING METHODS DIRECTLY?
            Watcher's background threads (LLM, screen reader) run on non-main threads.
            Qt strictly requires all UI modifications to happen on the MAIN thread.
            If a background thread calls self.text_label.setText() directly,
            Qt may crash or produce visual corruption.

            Emitting a signal from any thread is safe — Qt's signal/slot mechanism
            automatically queues the call to run on the main thread's event loop.
            This is called a "queued connection" and it's the correct pattern.
        """
        self.signals.show_text.connect(self._on_show_text)
        self.signals.hide_overlay.connect(self.hide_overlay)
        self.signals.append_token.connect(self._on_append_token)

        # Auto-hide timer — fires once after OVERLAY_TIMEOUT_MS milliseconds
        if OVERLAY_TIMEOUT_MS > 0:
            self._auto_hide_timer.setSingleShot(True)
            self._auto_hide_timer.timeout.connect(self.hide_overlay)

    def paintEvent(self, event) -> None:
        """
        Qt calls this automatically whenever the widget needs to be redrawn.
        We draw a rounded rectangle as the background — since the window itself
        is transparent, only what we paint here is visible.

        QPainter is Qt's drawing API. QPainterPath lets us define complex shapes
        (like rounded rectangles) as vector paths before filling/stroking them.
        """
        if not self._current_text:
            return

        painter = QPainter(self)
        # Antialiasing makes curved edges smooth instead of pixelated
        painter.setRenderHint(QPainter.Antialiasing)

        # Build a rounded rectangle path
        path = QPainterPath()
        path.addRoundedRect(
            0, 0, self.width(), self.height(),
            OVERLAY_BORDER_RADIUS, OVERLAY_BORDER_RADIUS
        )

        # Fill with semi-transparent dark background
        painter.fillPath(path, OVERLAY_BG_COLOR)

        # Draw a subtle accent line at the top
        painter.setPen(OVERLAY_ACCENT_COLOR)
        painter.drawLine(
            OVERLAY_BORDER_RADIUS, 1,
            self.width() - OVERLAY_BORDER_RADIUS, 1
        )

    # -----------------------------------------------------------------------
    # Public API — called via signals from background threads
    # -----------------------------------------------------------------------

    def show_suggestion(self, text: str) -> None:
        """
        Shows the overlay with the given text.
        Safe to call from any thread — uses signal internally.
        """
        self.signals.show_text.emit(text)

    def append_token(self, token: str) -> None:
        """
        Appends a streaming token to the current text.
        Safe to call from any thread — used during LLM streaming.
        """
        self.signals.append_token.emit(token)

    def hide_overlay(self) -> None:
        """Hides the overlay and clears its text."""
        self._current_text = ""
        self.text_label.setText("")
        self.hide()
        self._auto_hide_timer.stop()
        log.debug("Overlay hidden")

    # -----------------------------------------------------------------------
    # Slots — run on main thread via signal/slot mechanism
    # -----------------------------------------------------------------------

    def _on_show_text(self, text: str) -> None:
        """Slot: updates text and positions overlay near cursor."""
        self._current_text = text
        self.text_label.setText(text)
        self.adjustSize()
        self._position_near_cursor()
        self.show()
        self.update()  # Triggers paintEvent redraw

        # Reset auto-hide timer
        if OVERLAY_TIMEOUT_MS > 0:
            self._auto_hide_timer.start(OVERLAY_TIMEOUT_MS)

        log.debug("Overlay shown with %d chars", len(text))

    def _on_append_token(self, token: str) -> None:
        """Slot: appends a streaming token and refreshes display."""
        self._current_text += token
        self.text_label.setText(self._current_text)
        self.adjustSize()
        # Re-show if not visible (first token of a new response)
        if not self.isVisible():
            self._position_near_cursor()
            self.show()
            if OVERLAY_TIMEOUT_MS > 0:
                self._auto_hide_timer.start(OVERLAY_TIMEOUT_MS)

    def _position_near_cursor(self) -> None:
        """
        Positions the overlay just below and to the right of the cursor.
        Checks screen boundaries so the overlay never goes off-screen.
        """
        cursor_pos = QCursor.pos()  # Current cursor position in screen coordinates
        screen = QApplication.primaryScreen().geometry()

        x = cursor_pos.x() + 16  # 16px to the right of cursor
        y = cursor_pos.y() + 24  # 24px below cursor (below the cursor tip)

        # If overlay would go off the right edge, flip it to the left of cursor
        if x + OVERLAY_WIDTH > screen.width():
            x = cursor_pos.x() - OVERLAY_WIDTH - 16

        # If overlay would go off the bottom, show it above the cursor
        if y + self.height() > screen.height():
            y = cursor_pos.y() - self.height() - 8

        self.move(x, y)


# Module-level singleton — created when overlay.py is first imported
# QApplication must exist before any QWidget is created.
# main.py creates QApplication first, then imports this.
overlay: WatcherOverlay = None  # Initialised in main.py after QApplication exists
