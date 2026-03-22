"""
output/inline_suggest.py — Inline Suggestion Manager
=====================================================
Manages suggestion lifecycle: show → Tab accept / Esc dismiss.
Now speaks accepted/dismissed templates via voice_output.
"""

import threading
from typing import Optional
from pynput import keyboard as kb
from core.logger import get_logger

log = get_logger(__name__)


class InlineSuggestion:

    def __init__(self):
        self._raw_text: str = ""
        self._display_text: str = ""
        self._is_active: bool = False
        self._key_listener: Optional[kb.Listener] = None
        self._overlay = None
        self._typist = None
        self._voice_output = None
        self._app_name: str = ""
        log.debug("InlineSuggestion initialised")

    def set_dependencies(self, overlay, typist, voice_output=None) -> None:
        self._overlay = overlay
        self._typist = typist
        self._voice_output = voice_output
        log.debug("InlineSuggestion dependencies set")

    def set_app_name(self, app_name: str) -> None:
        """Called by orchestrator so accepted/dismissed templates know the app."""
        self._app_name = app_name

    def show(self, text: str) -> None:
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
        self._raw_text += token
        if self._overlay:
            self._overlay.append_token(token)
        if not self._is_active:
            self._is_active = True
            self._start_key_listener()

    def finalize_stream(self) -> None:
        """Adds [Tab]/[Esc] instructions after stream completes."""
        if self._raw_text and self._overlay:
            self._display_text = self._format_for_display(self._raw_text)
            self._overlay.show_suggestion(self._display_text)

    def _format_for_display(self, raw_text: str) -> str:
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
                log.info("Tab — accepting")
                threading.Thread(
                    target=self._accept, daemon=True, name="watcher-accept"
                ).start()
                return False
            elif key == kb.Key.esc:
                log.info("Esc — dismissing")
                threading.Thread(
                    target=self._dismiss, args=("escape",),
                    daemon=True, name="watcher-dismiss"
                ).start()
                return False
        except Exception as e:
            log.error("Key handler error: %s", str(e))
        return None

    def _accept(self) -> None:
        """Types the suggestion and speaks the accepted template."""
        text_to_type = self._raw_text
        self._is_active = False
        self._stop_key_listener()

        if self._overlay:
            self._overlay.hide_overlay()

        if self._typist and text_to_type:
            self._typist.type_text(text_to_type)

        # Speak accepted template AFTER typing
        if self._voice_output:
            from brain.voice_templates import accepted
            self._voice_output.speak(
                accepted(self._app_name), blocking=False
            )

        self._raw_text = ""
        self._display_text = ""
        log.info("Suggestion accepted and typed")

    def _dismiss(self, reason: str = "unknown") -> None:
        """Hides overlay and speaks dismissed template."""
        self._is_active = False
        self._stop_key_listener()

        if self._overlay:
            self._overlay.hide_overlay()

        # Speak dismissed template
        if self._voice_output and reason == "escape":
            from brain.voice_templates import dismissed
            self._voice_output.speak(
                dismissed(self._app_name), blocking=False
            )

        self._raw_text = ""
        self._display_text = ""
        log.info("Suggestion dismissed (%s)", reason)

    @property
    def is_active(self) -> bool:
        return self._is_active


inline_suggestion = InlineSuggestion()
