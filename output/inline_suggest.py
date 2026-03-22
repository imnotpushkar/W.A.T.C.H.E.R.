"""
output/inline_suggest.py — Inline Suggestion Manager
=====================================================
Manages the suggestion lifecycle: show → Tab to accept / Esc to dismiss.

BUG FIXES IN THIS VERSION:
    1. _raw_text vs _display_text separation:
       _raw_text  = the actual suggestion to type (no emoji, no instructions)
       _display_text = what the overlay shows (formatted with hints)
       Tab now types _raw_text only — not the formatted display string.

    2. Streaming also tracks raw vs display separately.
"""

import threading
from typing import Optional
from pynput import keyboard as kb
from core.logger import get_logger

log = get_logger(__name__)


class InlineSuggestion:

    def __init__(self):
        self._raw_text: str = ""          # What gets typed on Tab
        self._display_text: str = ""      # What the overlay shows
        self._is_active: bool = False
        self._key_listener: Optional[kb.Listener] = None
        self._overlay = None
        self._typist = None
        log.debug("InlineSuggestion initialised")

    def set_dependencies(self, overlay, typist) -> None:
        self._overlay = overlay
        self._typist = typist
        log.debug("InlineSuggestion dependencies set")

    def show(self, text: str) -> None:
        """
        Displays a completed suggestion in the overlay.
        Stores raw text separately from display text.
        """
        if not text or not text.strip():
            return

        if self._is_active:
            self._dismiss(reason="replaced")

        self._raw_text = text.strip()
        self._display_text = self._format_for_display(self._raw_text)
        self._is_active = True

        if self._overlay:
            self._overlay.show_suggestion(self._display_text)

        self._start_key_listener()
        log.info("Suggestion active (%d chars)", len(self._raw_text))

    def show_streaming(self, token: str) -> None:
        """
        Appends a streaming token. Raw text accumulates separately
        from what the overlay displays during streaming.
        """
        self._raw_text += token

        # During streaming we show just the raw text — no instructions yet.
        # Instructions are added after streaming completes (in show()).
        # This avoids "[Tab] Accept" appearing mid-stream.
        if self._overlay:
            self._overlay.append_token(token)

        if not self._is_active:
            self._is_active = True
            self._start_key_listener()

    def finalize_stream(self) -> None:
        """
        Call this after streaming completes to update the overlay
        with the final formatted display (adds Tab/Esc instructions).
        Called from orchestrator after the LLM stream finishes.
        """
        if self._raw_text and self._overlay:
            self._display_text = self._format_for_display(self._raw_text)
            self._overlay.show_suggestion(self._display_text)

    def _format_for_display(self, raw_text: str) -> str:
        """
        Adds visual instructions to the raw suggestion for display only.
        This string is NEVER typed — only shown.
        """
        return f"💡 {raw_text}\n\n[Tab] Accept   [Esc] Dismiss"

    def _start_key_listener(self) -> None:
        if self._key_listener:
            self._stop_key_listener()
        self._key_listener = kb.Listener(
            on_press=self._on_key_press,
            suppress=False
        )
        self._key_listener.start()
        log.debug("Key listener started")

    def _stop_key_listener(self) -> None:
        if self._key_listener:
            self._key_listener.stop()
            self._key_listener = None

    def _on_key_press(self, key) -> Optional[bool]:
        if not self._is_active:
            return False
        try:
            if key == kb.Key.tab:
                log.info("Tab — accepting suggestion")
                threading.Thread(
                    target=self._accept, daemon=True, name="watcher-accept"
                ).start()
                return False
            elif key == kb.Key.esc:
                log.info("Esc — dismissing suggestion")
                threading.Thread(
                    target=self._dismiss, args=("escape",),
                    daemon=True, name="watcher-dismiss"
                ).start()
                return False
        except Exception as e:
            log.error("Key handler error: %s", str(e))
        return None

    def _accept(self) -> None:
        """Types _raw_text only — never the display-formatted string."""
        text_to_type = self._raw_text     # RAW text only
        self._is_active = False
        self._stop_key_listener()

        if self._overlay:
            self._overlay.hide_overlay()

        if self._typist and text_to_type:
            log.info("Typing: %s...", text_to_type[:50])
            self._typist.type_text(text_to_type)

        self._raw_text = ""
        self._display_text = ""

    def _dismiss(self, reason: str = "unknown") -> None:
        self._is_active = False
        self._stop_key_listener()
        if self._overlay:
            self._overlay.hide_overlay()
        self._raw_text = ""
        self._display_text = ""
        log.info("Suggestion dismissed (%s)", reason)

    @property
    def is_active(self) -> bool:
        return self._is_active


inline_suggestion = InlineSuggestion()
