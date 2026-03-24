"""
core/orchestrator.py — Central Router
=======================================
Phase 2 pipeline — now memory-aware:

    t=0.0s  Hotkey fires
    t=0.0s  ack() template spoken immediately
    t=0.0s  context_builder.start_session() — opens DB session
    t=0.0s  context_builder.build_prompt() — screen + memory → enriched prompt
    t=Xs    Suggestion streams to overlay
    t=Xs    ready() template spoken
    t=Xs    User accepts/dismisses
    t=Xs    context_builder.save_interaction() — saves to SQLite + ChromaDB

KEY CHANGE FROM PHASE 1:
    orchestrator no longer builds prompts itself.
    context_builder owns prompt assembly — it knows about memory.
    orchestrator just calls context_builder and passes the result to Ollama.
"""

import threading
from core.logger import get_logger
from brain.llm import llm_client
from brain.voice_templates import ack, ready, error as voice_error
from brain.context_builder import context_builder
from input.screen_reader import screen_reader

log = get_logger(__name__)


class Orchestrator:

    def __init__(self):
        self._is_processing = False
        self._lock = threading.Lock()
        self._voice_output = None
        self._inline_suggestion = None
        self._app_name = ""
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
        # Step 2: Instant ack
        # ---------------------------------------------------------------
        if self._voice_output:
            self._voice_output.clear_queue()
            self._voice_output.speak(ack(app_name), blocking=False)

        # ---------------------------------------------------------------
        # Step 3: Check content
        # ---------------------------------------------------------------
        if not text_content or text_content.startswith("[Screen content"):
            log.warning("No screen content")
            if self._voice_output:
                self._voice_output.speak(voice_error("no_content"), blocking=False)
            return

        # ---------------------------------------------------------------
        # Step 4: Start memory session + build enriched prompt
        # Phase 2 change: context_builder replaces _build_suggestion_prompt()
        # ---------------------------------------------------------------
        context_builder.start_session(app_name, window_title)

        prompt = context_builder.build_prompt(
            app_name=app_name,
            window_title=window_title,
            text_content=text_content,
            focused_text=focused_text
        )
        log.debug("Enriched prompt built (%d chars)", len(prompt))

        # ---------------------------------------------------------------
        # Step 5: Stream suggestion to overlay
        # ---------------------------------------------------------------
        log.info("Generating suggestion...")
        full_suggestion = ""

        for token in llm_client.generate_stream(prompt, mode="suggestion"):
            full_suggestion += token
            if self._inline_suggestion:
                self._inline_suggestion.show_streaming(token)

        log.info("Suggestion: %s", full_suggestion[:80])

        if not full_suggestion or full_suggestion.startswith("[Watcher:"):
            log.warning("Bad suggestion from Ollama")
            if self._voice_output:
                self._voice_output.speak(voice_error("default"), blocking=False)
            context_builder.end_session()
            return

        # Finalize overlay
        if self._inline_suggestion:
            self._inline_suggestion.finalize_stream()

        # ---------------------------------------------------------------
        # Step 6: Speak ready template
        # ---------------------------------------------------------------
        if self._voice_output:
            self._voice_output.speak(ready(app_name), blocking=False)

        # ---------------------------------------------------------------
        # Step 7: Save interaction to memory
        # We save regardless of whether user accepts — the screen content
        # and suggestion are both valuable for future context.
        # accepted=False here because we don't know yet — inline_suggest
        # will call save_interaction(accepted=True) separately on Tab.
        # ---------------------------------------------------------------
        context_builder.save_interaction(
            screen_content=text_content,
            suggestion=full_suggestion,
            accepted=False   # Updated to True if user presses Tab
        )

        context_builder.end_session()

    def get_current_app(self) -> str:
        return self._app_name


# Module-level singleton
orchestrator = Orchestrator()
