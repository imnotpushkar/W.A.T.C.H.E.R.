"""
core/orchestrator.py — Central Router
=======================================
Phase 1 pipeline: screen read → suggestion (typed) → spoken commentary.

TWO-CALL ARCHITECTURE:
    Call 1 — mode="suggestion": streams the clean typed suggestion into overlay
    Call 2 — mode="spoken": generates a short Jarvis-style spoken line for TTS

    These are separate Ollama calls with different system prompts.
    Call 1 streams to overlay in real time.
    Call 2 happens after Call 1 completes, result goes straight to TTS.

    WHY NOT ONE CALL?
        One call would mean the spoken text and typed text are mixed together,
        requiring parsing to separate them — fragile and error-prone.
        Two calls with different system prompts is cleaner and more reliable.
        The extra latency is acceptable since Call 2 only starts after
        the user has already seen and accepted/dismissed the suggestion.
"""

import threading
from core.logger import get_logger
from brain.llm import llm_client
from input.screen_reader import screen_reader

log = get_logger(__name__)


class Orchestrator:

    def __init__(self):
        self._is_processing = False
        self._lock = threading.Lock()
        self._voice_output = None
        self._inline_suggestion = None
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
        log.info("Screen read — app: %s, chars: %d", app_name, len(text_content))

        # ---------------------------------------------------------------
        # Step 2: Build prompts
        # ---------------------------------------------------------------
        suggestion_prompt = self._build_suggestion_prompt(
            app_name, window_title, text_content, focused_text
        )
        spoken_prompt = self._build_spoken_prompt(
            app_name, window_title, text_content
        )

        # ---------------------------------------------------------------
        # Step 3: Stream typed suggestion into overlay (Call 1)
        # ---------------------------------------------------------------
        log.info("Generating suggestion...")
        full_suggestion = ""
        for token in llm_client.generate_stream(suggestion_prompt, mode="suggestion"):
            full_suggestion += token
            if self._inline_suggestion:
                self._inline_suggestion.show_streaming(token)

        log.info("Suggestion complete (%d chars): %s", len(full_suggestion), full_suggestion[:60])

        if not full_suggestion or full_suggestion.startswith("[Watcher:"):
            log.warning("Bad suggestion response")
            return

        # Finalize overlay — adds [Tab]/[Esc] instructions
        if self._inline_suggestion:
            self._inline_suggestion.finalize_stream()

        # ---------------------------------------------------------------
        # Step 4: Generate spoken commentary (Call 2) and speak it
        # ---------------------------------------------------------------
        # This runs AFTER the suggestion is ready so the user sees the
        # overlay immediately. The spoken line then plays while they
        # decide whether to accept or dismiss.
        log.info("Generating spoken commentary...")
        spoken_response = llm_client.generate(spoken_prompt, mode="spoken")
        log.info("Speaking: %s", spoken_response[:80])

        if self._voice_output and spoken_response:
            self._voice_output.speak(spoken_response, blocking=False)

    def _build_suggestion_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
        focused_text: str
    ) -> str:
        """
        Prompt for the clean typed suggestion.
        Strict — output must be only the text to type.
        """
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
            "No explanation. No preamble. Just the text itself."
        )
        return "\n\n".join(parts)

    def _build_spoken_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
    ) -> str:
        """
        Prompt for the Jarvis-style spoken commentary.
        Should describe what Watcher just did / observed in 1-2 sentences.
        """
        parts = [f"APP: {app_name}"]

        if window_title and window_title != app_name:
            parts.append(f"WINDOW: {window_title}")

        if text_content and not text_content.startswith("[Screen"):
            # Only send a short excerpt for spoken context — we don't need the full thing
            excerpt = text_content[-500:] if len(text_content) > 500 else text_content
            parts.append(f"SCREEN EXCERPT:\n{excerpt}")

        parts.append(
            "You've just generated a text suggestion for the user. "
            "In 1-2 sentences, tell them what you observed and what the suggestion is for. "
            "Speak as Watcher. Be brief, confident, and direct."
        )
        return "\n\n".join(parts)


orchestrator = Orchestrator()
