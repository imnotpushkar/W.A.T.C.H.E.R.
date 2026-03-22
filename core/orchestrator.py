"""
core/orchestrator.py — Central Router
=======================================
STAGGERED STREAMING ARCHITECTURE:

Problem with pure parallel: two simultaneous Ollama calls compete for the
same GPU. On 6GB VRAM with llama3.1:8b, both slow down — the suggestion
stream wins because it started first, voice arrives after overlay anyway.

Solution: STAGGER intentionally.
    1. Start conversation stream FIRST — short prompt, fast to generate
    2. 600ms later start suggestion stream
    3. Voice speaks while suggestion is still generating
    4. Overlay appears after voice has already started

This matches how Jarvis actually worked — he spoke WHILE computing,
not after. The voice leads, the result follows.

CONVERSATION PROMPT IS SHORT ON PURPOSE:
    We cap conversation at num_predict=80 — roughly 2-3 sentences.
    Short generation = first sentence ready in ~1s = voice starts fast.
    Suggestion gets the full token budget since it needs to be accurate.
"""

import threading
import time
from core.logger import get_logger
from brain.llm import llm_client, get_acknowledgement
from input.screen_reader import screen_reader

log = get_logger(__name__)

# How long to wait before starting suggestion stream after conversation starts.
# Gives conversation stream a head start so voice fires before overlay appears.
SUGGESTION_STREAM_DELAY = 0.6  # seconds


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
        # Step 2: Instant acknowledgement
        # ---------------------------------------------------------------
        if self._voice_output:
            self._voice_output.clear_queue()
            ack = get_acknowledgement(app_name)
            self._voice_output.speak(ack, blocking=False)

        # ---------------------------------------------------------------
        # Step 3: Build prompts
        # ---------------------------------------------------------------
        suggestion_prompt = self._build_suggestion_prompt(
            app_name, window_title, text_content, focused_text
        )
        conversation_prompt = self._build_conversation_prompt(
            app_name, window_title, text_content
        )

        # ---------------------------------------------------------------
        # Step 4: Staggered streams
        #
        # conversation_thread starts immediately — short prompt, fast.
        # suggestion_thread starts after SUGGESTION_STREAM_DELAY seconds.
        #
        # Timeline:
        #   t=0.0s  — ack spoken ("Reading your screen.")
        #   t=0.0s  — conversation stream starts generating
        #   t=0.6s  — suggestion stream starts generating
        #   t=1.0s  — first conversational sentence speaks (voice leads)
        #   t=2.5s  — overlay starts filling with suggestion
        #   t=2.0s  — second conversational sentence speaks
        #   t=3.5s  — overlay complete, [Tab]/[Esc] shown
        # ---------------------------------------------------------------
        suggestion_result = {"text": ""}

        def run_conversation_stream():
            """Starts immediately. Short prompt → fast first sentence."""
            try:
                if self._voice_output:
                    self._voice_output.speak_stream(
                        llm_client.generate_stream(
                            conversation_prompt, mode="spoken"
                        )
                    )
                    log.info("Conversation stream complete")
            except Exception as e:
                log.error("Conversation stream error: %s", str(e))

        def run_suggestion_stream():
            """Starts after delay. Suggestion appears while voice is speaking."""
            try:
                # Delay so conversation gets a head start
                time.sleep(SUGGESTION_STREAM_DELAY)

                for token in llm_client.generate_stream(
                    suggestion_prompt, mode="suggestion"
                ):
                    suggestion_result["text"] += token
                    if self._inline_suggestion:
                        self._inline_suggestion.show_streaming(token)

                if suggestion_result["text"] and \
                   not suggestion_result["text"].startswith("[Watcher:"):
                    if self._inline_suggestion:
                        self._inline_suggestion.finalize_stream()
                    log.info("Suggestion complete: %s",
                             suggestion_result["text"][:60])
                else:
                    log.warning("Bad suggestion response")
            except Exception as e:
                log.error("Suggestion stream error: %s", str(e))

        # Start conversation immediately
        conversation_thread = threading.Thread(
            target=run_conversation_stream,
            name="watcher-conversation",
            daemon=True
        )

        # Start suggestion with built-in delay
        suggestion_thread = threading.Thread(
            target=run_suggestion_stream,
            name="watcher-suggestion",
            daemon=True
        )

        conversation_thread.start()
        suggestion_thread.start()

        # Wait for both before releasing lock
        conversation_thread.join()
        suggestion_thread.join()

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
        Short, specific prompt for Watcher's spoken response.

        DELIBERATELY SHORT:
            num_predict=80 in llm.py for spoken mode — ~2-3 sentences.
            Short generation = first sentence ready faster = voice starts sooner.
            We extract a short excerpt (300 chars) to keep the prompt lean.
            Lean prompt + low token budget = fast first sentence.

        SPECIFIC INSTRUCTION:
            "Pick one specific detail" forces Watcher to reference actual
            content rather than giving a generic "I've read your screen" line.
            Generic lines sound like a loading spinner. Specific ones sound
            like Jarvis actually read it.
        """
        excerpt = ""
        if text_content and not text_content.startswith("[Screen"):
            excerpt = text_content[-300:] if len(text_content) > 300 else text_content

        return (
            f"APP: {app_name}\n"
            f"SCREEN:\n{excerpt}\n\n"
            f"You are Watcher. You've read the user's screen.\n"
            f"Speak 2 short sentences in Watcher's voice.\n"
            f"Sentence 1: Pick ONE specific detail from the screen and make a brief remark about it.\n"
            f"Sentence 2: Tell the user their suggestion is ready.\n"
            f"Be specific. Be brief. Sound like Jarvis."
        )


orchestrator = Orchestrator()
