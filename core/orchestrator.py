"""
core/orchestrator.py — Central Router
=======================================
Clean pipeline using pre-written voice templates.
Zero LLM involvement in voice — instant, reliable, always correct tone.

Pipeline:
    t=0.0s  Hotkey fires
    t=0.0s  ack() template spoken immediately
    t=0.0s  Suggestion stream starts → overlay fills
    t=Xs    Overlay complete → ready() template spoken
    t=Xs    User presses Tab → accepted() spoken
           OR User presses Esc → dismissed() spoken
"""

import threading
from core.logger import get_logger
from brain.llm import llm_client
from brain.voice_templates import ack, ready, error as voice_error
from input.screen_reader import screen_reader

log = get_logger(__name__)


class Orchestrator:

    def __init__(self):
        self._is_processing = False
        self._lock = threading.Lock()
        self._voice_output = None
        self._inline_suggestion = None
        self._app_name = ""   # Stored so accepted/dismissed can use it
        log.debug("Orchestrator initialised")

    def set_dependencies(self, voice_output, inline_suggestion) -> None:
        self._voice_output = voice_output
        self._inline_suggestion = inline_suggestion
        log.debug("Orchestrator dependencies set")

    def handle_trigger(self) -> None:
        with self._lock:
            if self._is_processing:
                log.info("Trigger ignored — already processing")
                return
            self._is_processing = True
        try:
            self._run_pipeline()
        except Exception as e:
            log.error("Pipeline error: %s", str(e), exc_info=True)
        finally:
            with self._lock:
                self._is_processing = False

    def _run_pipeline(self) -> None:
        # ---------------------------------------------------------------
        # Step 1: Read screen
        # ---------------------------------------------------------------
        log.info("Pipeline started — reading screen")
        screen_data = screen_reader.read_active_window()
        app_name = screen_data.get("app_name", "Unknown")
        window_title = screen_data.get("window_title", "")
        text_content = screen_data.get("text_content", "")
        focused_text = screen_data.get("focused_text", "")
        self._app_name = app_name
        log.info("Screen read — app: %s, chars: %d", app_name, len(text_content))

        # ---------------------------------------------------------------
        # Step 2: Instant ack — fires before anything else
        # ---------------------------------------------------------------
        if self._voice_output:
            self._voice_output.clear_queue()
            self._voice_output.speak(ack(app_name), blocking=False)

        # ---------------------------------------------------------------
        # Step 3: Check we actually have content to work with
        # ---------------------------------------------------------------
        if not text_content or text_content.startswith("[Screen content"):
            log.warning("No screen content available")
            if self._voice_output:
                self._voice_output.speak(
                    voice_error("no_content"), blocking=False
                )
            return

        # ---------------------------------------------------------------
        # Step 4: Build prompt and stream suggestion to overlay
        # ---------------------------------------------------------------
        prompt = self._build_suggestion_prompt(
            app_name, window_title, text_content, focused_text
        )

        log.info("Generating suggestion...")
        full_suggestion = ""

        for token in llm_client.generate_stream(prompt, mode="suggestion"):
            full_suggestion += token
            if self._inline_suggestion:
                self._inline_suggestion.show_streaming(token)

        log.info("Suggestion complete (%d chars): %s",
                 len(full_suggestion), full_suggestion[:60])

        if not full_suggestion or full_suggestion.startswith("[Watcher:"):
            log.warning("Bad suggestion from Ollama")
            if self._voice_output:
                self._voice_output.speak(
                    voice_error("default"), blocking=False
                )
            return

        # Finalize overlay — adds [Tab]/[Esc] instructions
        if self._inline_suggestion:
            self._inline_suggestion.finalize_stream()

        # ---------------------------------------------------------------
        # Step 5: Speak "ready" template — suggestion is in overlay
        # This fires AFTER the overlay is fully populated so the timing
        # feels natural: overlay appears, then Watcher announces it.
        # With templates this is instant — no LLM wait.
        # ---------------------------------------------------------------
        if self._voice_output:
            self._voice_output.speak(ready(app_name), blocking=False)

    def _build_suggestion_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
        focused_text: str
    ) -> str:
        parts = [f"APP: {app_name}"]
        if window_title and window_title != app_name:
            parts.append(f"WINDOW: {window_title}")
        if text_content and not text_content.startswith("[Screen"):
            content = text_content[-2000:] if len(text_content) > 2000 else text_content
            parts.append(f"SCREEN CONTENT:\n{content}")
        if focused_text:
            parts.append(f"USER IS TYPING: {focused_text}")
        parts.append(
            "Output ONLY the suggested reply or text completion. "
            "Spell every word correctly. "
            "No explanation. No preamble. Just the text itself."
        )
        return "\n\n".join(parts)


orchestrator = Orchestrator()
