"""
output/overlay.py — Always-on-top Transparent Overlay Window
=============================================================
Creates the floating suggestion window that appears over any app.
"""

from PySide6.QtWidgets import QWidget, QApplication, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QFont, QPainterPath, QCursor
from core.config import OVERLAY_OPACITY, OVERLAY_TIMEOUT_MS
from core.logger import get_logger

log = get_logger(__name__)

OVERLAY_WIDTH = 480
OVERLAY_PADDING = 16
OVERLAY_BORDER_RADIUS = 12
OVERLAY_BG_COLOR = QColor(18, 18, 24, int(255 * OVERLAY_OPACITY))
OVERLAY_TEXT_COLOR = QColor(220, 220, 255)
OVERLAY_ACCENT_COLOR = QColor(99, 102, 241)


class OverlaySignals(QObject):
    """
    Signals for thread-safe communication with the overlay.
    Defined on a separate QObject so background threads can emit
    without holding a reference to the full WatcherOverlay widget.
    """
    show_text = Signal(str)
    hide_overlay = Signal()
    append_token = Signal(str)


class WatcherOverlay(QWidget):
    """
    Transparent always-on-top frameless suggestion window.
    """

    def __init__(self):
        super().__init__()
        self.signals = OverlaySignals()
        self._current_text = ""
        self._auto_hide_timer = QTimer(self)
        self._is_alive = True   # Guard flag for shutdown race condition

        self._setup_window()
        self._setup_layout()
        self._connect_signals()
        log.debug("WatcherOverlay initialised")

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(OVERLAY_WIDTH)

    def _setup_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            OVERLAY_PADDING, OVERLAY_PADDING,
            OVERLAY_PADDING, OVERLAY_PADDING
        )
        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        self.text_label.setFont(QFont("Segoe UI", 11))
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
        self.signals.show_text.connect(self._on_show_text)
        self.signals.hide_overlay.connect(self.hide_overlay)
        self.signals.append_token.connect(self._on_append_token)

        if OVERLAY_TIMEOUT_MS > 0:
            self._auto_hide_timer.setSingleShot(True)
            self._auto_hide_timer.timeout.connect(self.hide_overlay)

    def paintEvent(self, event) -> None:
        if not self._current_text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            0, 0, self.width(), self.height(),
            OVERLAY_BORDER_RADIUS, OVERLAY_BORDER_RADIUS
        )
        painter.fillPath(path, OVERLAY_BG_COLOR)
        painter.setPen(OVERLAY_ACCENT_COLOR)
        painter.drawLine(
            OVERLAY_BORDER_RADIUS, 1,
            self.width() - OVERLAY_BORDER_RADIUS, 1
        )

    # -----------------------------------------------------------------------
    # Public API — safe to call from any thread
    # -----------------------------------------------------------------------

    def show_suggestion(self, text: str) -> None:
        """Shows overlay with text. Safe to call from background threads."""
        if not self._is_alive:
            return
        self.signals.show_text.emit(text)

    def append_token(self, token: str) -> None:
        """Appends streaming token. Safe to call from background threads."""
        if not self._is_alive:
            return
        self.signals.append_token.emit(token)

    def hide_overlay(self) -> None:
        """Hides overlay and clears text."""
        self._current_text = ""
        self.text_label.setText("")
        self.hide()
        self._auto_hide_timer.stop()
        log.debug("Overlay hidden")

    def shutdown(self) -> None:
        """
        Called before Qt teardown to prevent signal emission on dead objects.
        Sets _is_alive=False so background threads stop emitting signals
        after Qt has started shutting down.
        """
        self._is_alive = False
        self.hide_overlay()
        log.debug("Overlay shutdown flag set")

    # -----------------------------------------------------------------------
    # Slots — always run on main thread via Qt signal/slot
    # -----------------------------------------------------------------------

    def _on_show_text(self, text: str) -> None:
        self._current_text = text
        self.text_label.setText(text)
        self.adjustSize()
        self._position_near_cursor()
        self.show()
        self.update()
        if OVERLAY_TIMEOUT_MS > 0:
            self._auto_hide_timer.start(OVERLAY_TIMEOUT_MS)
        log.debug("Overlay shown (%d chars)", len(text))

    def _on_append_token(self, token: str) -> None:
        self._current_text += token
        self.text_label.setText(self._current_text)
        self.adjustSize()
        if not self.isVisible():
            self._position_near_cursor()
            self.show()
            if OVERLAY_TIMEOUT_MS > 0:
                self._auto_hide_timer.start(OVERLAY_TIMEOUT_MS)

    def _position_near_cursor(self) -> None:
        cursor_pos = QCursor.pos()
        screen = QApplication.primaryScreen().geometry()
        x = cursor_pos.x() + 16
        y = cursor_pos.y() + 24
        if x + OVERLAY_WIDTH > screen.width():
            x = cursor_pos.x() - OVERLAY_WIDTH - 16
        if y + self.height() > screen.height():
            y = cursor_pos.y() - self.height() - 8
        self.move(x, y)


# Initialised in main.py after QApplication exists
overlay: WatcherOverlay = None
