"""
core/orchestrator.py — Central Router
=======================================
Parallel streaming architecture:

    Thread A: Ollama (suggestion mode) → overlay tokens
    Thread B: Ollama (conversational mode) → voice_output.speak_stream()

Both start simultaneously after screen read.
Watcher starts speaking within ~1s of first sentence completing.
Overlay fills with suggestion text at the same time.
No silence gap. No waiting for one before the other starts.

THREAD COORDINATION:
    We use threading.Thread for both streams.
    Both are daemon threads — they die if main exits.
    The pipeline waits for BOTH to complete before releasing the
    _is_processing lock. This prevents a new trigger firing while
    either stream is still running.

    threading.Thread.join() blocks until that thread finishes.
    We start both, then join both — parallel execution, sequential completion.
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
        # Step 2: Instant acknowledgement — zero latency, fires immediately
        # ---------------------------------------------------------------
        if self._voice_output:
            self._voice_output.clear_queue()  # Clear any leftover audio
            ack = get_acknowledgement(app_name)
            self._voice_output.speak(ack, blocking=False)
            log.info("Acknowledgement queued: %s", ack)

        # ---------------------------------------------------------------
        # Step 3: Build both prompts
        # ---------------------------------------------------------------
        suggestion_prompt = self._build_suggestion_prompt(
            app_name, window_title, text_content, focused_text
        )
        conversation_prompt = self._build_conversation_prompt(
            app_name, window_title, text_content
        )

        # ---------------------------------------------------------------
        # Step 4: Launch both streams in parallel
        #
        # Thread A streams suggestion tokens → overlay
        # Thread B streams conversational tokens → sentence buffer → speech
        #
        # WHY PARALLEL?
        #   Sequential would mean: wait for suggestion to finish (3-4s),
        #   THEN start generating the spoken line (another 2-3s).
        #   Parallel means both generate simultaneously — Watcher speaks
        #   while the overlay fills. No silence gap.
        #
        # GPU CONTENTION NOTE:
        #   Two Ollama calls at once compete for GPU memory.
        #   On 6GB VRAM with llama3.1:8b (~5GB), there may be slight
        #   slowdown on both streams. If this causes problems we can
        #   stagger: start conversation stream 0.5s after suggestion.
        #   We test first before adding that complexity.
        # ---------------------------------------------------------------
        suggestion_result = {"text": ""}   # Mutable dict for thread result
        stream_error = {"occurred": False}

        def run_suggestion_stream():
            """Thread A: suggestion tokens → overlay"""
            try:
                for token in llm_client.generate_stream(
                    suggestion_prompt, mode="suggestion"
                ):
                    suggestion_result["text"] += token
                    if self._inline_suggestion:
                        self._inline_suggestion.show_streaming(token)

                # Finalize overlay with Tab/Esc instructions
                if self._inline_suggestion and suggestion_result["text"]:
                    if not suggestion_result["text"].startswith("[Watcher:"):
                        self._inline_suggestion.finalize_stream()
                        log.info("Suggestion complete (%d chars): %s",
                                 len(suggestion_result["text"]),
                                 suggestion_result["text"][:60])
                    else:
                        log.warning("Bad suggestion from Ollama")
                        stream_error["occurred"] = True
            except Exception as e:
                log.error("Suggestion stream error: %s", str(e))
                stream_error["occurred"] = True

        def run_conversation_stream():
            """Thread B: conversational tokens → sentence buffer → speech"""
            try:
                if self._voice_output:
                    # speak_stream() handles sentence detection and queuing
                    self._voice_output.speak_stream(
                        llm_client.generate_stream(
                            conversation_prompt, mode="spoken"
                        )
                    )
                    log.info("Conversation stream complete")
            except Exception as e:
                log.error("Conversation stream error: %s", str(e))

        # Create both threads
        suggestion_thread = threading.Thread(
            target=run_suggestion_stream,
            name="watcher-suggestion",
            daemon=True
        )
        conversation_thread = threading.Thread(
            target=run_conversation_stream,
            name="watcher-conversation",
            daemon=True
        )

        # Start both simultaneously
        suggestion_thread.start()
        conversation_thread.start()

        # Wait for both to complete before releasing pipeline lock
        # join() blocks until the thread's target function returns
        suggestion_thread.join()
        conversation_thread.join()

        log.info("Pipeline complete")

    # -----------------------------------------------------------------------
    # Prompt builders
    # -----------------------------------------------------------------------

    def _build_suggestion_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
        focused_text: str
    ) -> str:
        """
        Strict prompt for clean typed suggestion — zero personality.
        Output must be exactly what gets typed into the app.
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
            "Spell every word correctly. "
            "No explanation. No preamble. Just the text itself."
        )
        return "\n\n".join(parts)

    def _build_conversation_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
    ) -> str:
        """
        Prompt for Watcher's spoken conversational response.

        This is what makes Watcher feel like Jarvis — he doesn't just announce
        a result, he makes a remark, gives context, maybe a dry observation.
        Streams sentence by sentence so speech starts before generation ends.

        The prompt is designed to produce 2-4 short sentences naturally —
        enough for a real conversational feel without being verbose.
        """
        excerpt = ""
        if text_content and not text_content.startswith("[Screen"):
            excerpt = text_content[-400:] if len(text_content) > 400 else text_content

        return (
            f"APP: {app_name}\n"
            f"WINDOW: {window_title}\n"
            f"SCREEN EXCERPT:\n{excerpt}\n\n"
            f"You are Watcher. You've just read the user's screen and prepared a suggestion.\n"
            f"Have a brief conversation about what you see — 2 to 3 short sentences.\n"
            f"Observe something specific from the screen content. Make a remark.\n"
            f"Tell the user their suggestion is ready.\n"
            f"Be Watcher — confident, dry, British, Jarvis-like.\n"
            f"Do NOT say what the suggestion is. Do NOT repeat the screen content verbatim."
        )


orchestrator = Orchestrator()
