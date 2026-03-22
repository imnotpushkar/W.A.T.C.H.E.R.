"""
output/inline_suggest.py — Inline Suggestion Manager
=====================================================
Manages the suggestion lifecycle: show → wait for Tab/Escape → accept or dismiss.

WHAT THIS MODULE DOES:
    This is the coordinator between the overlay (display) and the typist (action).
    When Watcher generates a response:
        1. inline_suggest shows it in the overlay
        2. It starts listening for Tab (accept) or Escape (dismiss)
        3. If Tab → typist types the text into the active app
        4. If Escape or timeout → overlay hides, nothing typed

WHY SEPARATE FROM overlay.py?
    overlay.py only knows about DISPLAYING text — it's a pure UI component.
    inline_suggest.py knows about the WORKFLOW — what happens when the user
    interacts with the suggestion. Separation of concerns: each file has
    one job. This makes both easier to test and modify independently.

HOW TAB INTERCEPTION WORKS:
    We use pynput to add a TEMPORARY keyboard listener when a suggestion
    is active. This listener watches specifically for Tab and Escape.
    When the suggestion is dismissed (accepted or cancelled), we remove
    the listener immediately so Tab works normally in the underlying app.

    IMPORTANT: We consume the Tab keypress (suppress=True in pynput) so
    the underlying app doesn't also receive it. Without this, pressing Tab
    would both accept the suggestion AND insert a tab character.
"""

import threading
from typing import Optional
from pynput import keyboard as kb
from core.logger import get_logger

log = get_logger(__name__)


class InlineSuggestion:
    """
    Manages the full lifecycle of a single inline suggestion:
    display → user interaction → accept or dismiss.
    """

    def __init__(self):
        self._current_text: str = ""
        self._is_active: bool = False
        self._key_listener: Optional[kb.Listener] = None
        self._overlay = None   # Set after QApplication exists in main.py
        self._typist = None    # Set after initialisation in main.py
        log.debug("InlineSuggestion initialised")

    def set_dependencies(self, overlay, typist) -> None:
        """
        Injects overlay and typist after they're created.
        Called from main.py after all components are initialised.

        WHY NOT IMPORT DIRECTLY?
            overlay.py and typist.py both depend on other components.
            Importing them at module level here could create circular imports
            (A imports B which imports A). Dependency injection avoids this —
            main.py creates everything and wires them together.
        """
        self._overlay = overlay
        self._typist = typist
        log.debug("InlineSuggestion dependencies set")

    def show(self, text: str) -> None:
        """
        Displays a suggestion in the overlay and starts listening for Tab/Escape.

        Args:
            text: The suggestion text to display and potentially type.
        """
        if not text or not text.strip():
            return

        # Cancel any existing suggestion before showing new one
        if self._is_active:
            self._dismiss(reason="replaced")

        self._current_text = text.strip()
        self._is_active = True

        # Show in overlay
        if self._overlay:
            self._overlay.show_suggestion(
                f"💡 {self._current_text}\n\n"
                f"[Tab] Accept   [Esc] Dismiss"
            )

        # Start temporary key listener
        self._start_key_listener()
        log.info("Suggestion active (%d chars)", len(self._current_text))

    def show_streaming(self, token: str) -> None:
        """
        Appends a streaming token to the overlay during LLM generation.
        Called repeatedly as tokens stream from Ollama.

        Args:
            token: Single token string from LLM stream.
        """
        self._current_text += token
        if self._overlay:
            self._overlay.append_token(token)

        # Start key listener on first token if not already active
        if not self._is_active:
            self._is_active = True
            self._start_key_listener()

    def _start_key_listener(self) -> None:
        """
        Starts a pynput keyboard listener watching for Tab and Escape.

        suppress=True means: consume the keypresses we handle.
        This prevents Tab from reaching the underlying app when we intercept it.

        The listener runs in its own daemon thread (pynput handles this internally
        when you call .start() instead of .run()).
        """
        if self._key_listener:
            self._stop_key_listener()

        self._key_listener = kb.Listener(
            on_press=self._on_key_press,
            suppress=False   # We selectively suppress in _on_key_press
        )
        self._key_listener.start()
        log.debug("Key listener started for suggestion interaction")

    def _stop_key_listener(self) -> None:
        """Stops and cleans up the key listener."""
        if self._key_listener:
            self._key_listener.stop()
            self._key_listener = None

    def _on_key_press(self, key) -> Optional[bool]:
        """
        pynput calls this for every keypress while the listener is active.

        Returns False to stop the listener (pynput convention).
        Returns None to continue listening.

        KEY IDENTIFICATION IN PYNPUT:
            Special keys come as kb.Key objects: kb.Key.tab, kb.Key.esc
            Regular keys come as kb.KeyCode objects with a .char attribute
            We only care about Tab and Escape here.
        """
        if not self._is_active:
            return False  # Stop listener if suggestion was dismissed externally

        try:
            if key == kb.Key.tab:
                log.info("Tab pressed — accepting suggestion")
                # Run accept in a thread so we don't block the key listener
                threading.Thread(
                    target=self._accept,
                    daemon=True,
                    name="watcher-accept"
                ).start()
                return False  # Stop this listener

            elif key == kb.Key.esc:
                log.info("Escape pressed — dismissing suggestion")
                threading.Thread(
                    target=self._dismiss,
                    args=("escape",),
                    daemon=True,
                    name="watcher-dismiss"
                ).start()
                return False  # Stop this listener

        except Exception as e:
            log.error("Error in key handler: %s", str(e))

        return None  # Continue listening for other keys

    def _accept(self) -> None:
        """
        Accepts the suggestion: hides overlay and types the text.
        Called in a background thread when Tab is pressed.
        """
        text_to_type = self._current_text
        self._is_active = False
        self._stop_key_listener()

        if self._overlay:
            self._overlay.hide_overlay()

        if self._typist and text_to_type:
            log.info("Typing accepted suggestion")
            self._typist.type_text(text_to_type)

        self._current_text = ""

    def _dismiss(self, reason: str = "unknown") -> None:
        """
        Dismisses the suggestion without typing anything.

        Args:
            reason: Why it was dismissed (for logging).
        """
        self._is_active = False
        self._stop_key_listener()

        if self._overlay:
            self._overlay.hide_overlay()

        self._current_text = ""
        log.info("Suggestion dismissed (%s)", reason)

    @property
    def is_active(self) -> bool:
        """Returns True if a suggestion is currently being displayed."""
        return self._is_active


# Module-level singleton
inline_suggestion = InlineSuggestion()
