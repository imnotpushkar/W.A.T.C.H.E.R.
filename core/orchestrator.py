"""
core/orchestrator.py — Central Router
=======================================
A+B voice architecture:
    - Instant: template acknowledgement spoken immediately on trigger (zero latency)
    - After suggestion streams: speak the suggestion text itself
    - No second LLM call for spoken commentary — zero extra Ollama cost
"""

import threading
from core.logger import get_logger
from brain.llm import llm_client, get_acknowledgement
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
        # Step 2: Speak instant acknowledgement — ZERO latency
        # Template lookup, no LLM. Watcher sounds alive immediately.
        # ---------------------------------------------------------------
        acknowledgement = get_acknowledgement(app_name)
        log.info("Speaking acknowledgement: %s", acknowledgement)
        if self._voice_output:
            self._voice_output.speak(acknowledgement, blocking=False)

        # ---------------------------------------------------------------
        # Step 3: Build suggestion prompt and stream to overlay
        # ---------------------------------------------------------------
        suggestion_prompt = self._build_suggestion_prompt(
            app_name, window_title, text_content, focused_text
        )

        log.info("Generating suggestion...")
        full_suggestion = ""
        for token in llm_client.generate_stream(suggestion_prompt, mode="suggestion"):
            full_suggestion += token
            if self._inline_suggestion:
                self._inline_suggestion.show_streaming(token)

        log.info("Suggestion complete (%d chars): %s", len(full_suggestion), full_suggestion[:60])

        if not full_suggestion or full_suggestion.startswith("[Watcher:"):
            log.warning("Bad suggestion from Ollama")
            return

        # Finalize overlay — adds [Tab]/[Esc] instructions
        if self._inline_suggestion:
            self._inline_suggestion.finalize_stream()

        # ---------------------------------------------------------------
        # Step 4: Speak the suggestion itself in Watcher's voice
        # We generate one SHORT spoken line to announce the suggestion.
        # num_predict=60 means this call is fast — just 1-2 sentences.
        # ---------------------------------------------------------------
        spoken_prompt = self._build_spoken_prompt(
            app_name, full_suggestion
        )
        spoken_line = llm_client.generate(spoken_prompt, mode="spoken")
        log.info("Speaking: %s", spoken_line)

        if self._voice_output and spoken_line:
            self._voice_output.speak(spoken_line, blocking=False)

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
            "No explanation. No preamble. Just the text itself."
        )
        return "\n\n".join(parts)

    def _build_spoken_prompt(self, app_name: str, suggestion: str) -> str:
        """
        Short prompt for the post-suggestion spoken line.
        Tells Watcher what it just produced so it can announce it naturally.
        Keeps it to 1-2 sentences — num_predict=60 enforces brevity too.
        """
        return (
            f"APP: {app_name}\n\n"
            f"SUGGESTION PREPARED: {suggestion[:100]}\n\n"
            f"In one short sentence, tell the user their suggestion is ready. "
            f"Be Watcher. Be brief."
        )


orchestrator = Orchestrator()
