"""
core/orchestrator.py — Central Router and State Manager
=========================================================
Coordinates all Watcher components in response to trigger events.
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
        # Step 1: Read screen
        log.info("Pipeline started — reading screen")
        screen_data = screen_reader.read_active_window()
        app_name = screen_data.get("app_name", "Unknown")
        window_title = screen_data.get("window_title", "")
        text_content = screen_data.get("text_content", "")
        focused_text = screen_data.get("focused_text", "")
        log.info("Screen read — app: %s, chars: %d", app_name, len(text_content))

        # Step 2: Build prompt
        prompt = self._build_prompt(app_name, window_title, text_content, focused_text)
        log.debug("Prompt built (%d chars)", len(prompt))

        # Step 3: Stream from Ollama, show tokens in overlay
        log.info("Sending to Ollama...")
        full_response = ""
        for token in llm_client.generate_stream(prompt):
            full_response += token
            if self._inline_suggestion:
                self._inline_suggestion.show_streaming(token)

        log.info("Ollama response complete (%d chars)", len(full_response))

        if not full_response or full_response.startswith("[Watcher:"):
            log.warning("Empty or error response from Ollama")
            return

        # Step 4: Finalize overlay — adds [Tab]/[Esc] instructions now that
        # streaming is done. We don't add them mid-stream because they'd
        # appear as the model is still generating tokens.
        if self._inline_suggestion:
            self._inline_suggestion.finalize_stream()

        # Step 5: Speak the response
        if self._voice_output:
            self._voice_output.speak(full_response, blocking=False)

    def _build_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
        focused_text: str
    ) -> str:
        """
        Builds the prompt sent to Ollama.

        PROMPT QUALITY FIX:
            The previous prompt was too vague — "provide a helpful response".
            Vague instructions produce meta-commentary instead of direct action.
            We now give specific, firm instructions based on what we can detect:
            - If there's text being typed → suggest how to complete/improve it
            - If there's a conversation → suggest a reply (text only, no preamble)
            - Otherwise → summarise what's on screen concisely
        """
        sections = []
        sections.append(f"APP: {app_name}")

        if window_title and window_title != app_name:
            sections.append(f"WINDOW: {window_title}")

        if text_content and not text_content.startswith("[Screen content"):
            content = text_content[-2000:] if len(text_content) > 2000 else text_content
            sections.append(f"SCREEN CONTENT:\n{content}")

        if focused_text:
            sections.append(f"USER IS CURRENTLY TYPING: {focused_text}")

        # Firm, specific instruction — no wiggle room for meta-commentary
        sections.append(
            "INSTRUCTION: Based on the screen content above, provide ONE of:\n"
            "- If the user is typing a message: suggest the best completion or reply. "
            "Write ONLY the message text. No explanations, no preamble.\n"
            "- If there is a conversation visible: write a suggested reply. "
            "Write ONLY the reply text.\n"
            "- Otherwise: give a single short helpful observation or action.\n"
            "Be direct. No meta-commentary. No 'I notice you are...' or 'It looks like...'"
        )

        return "\n\n".join(sections)


orchestrator = Orchestrator()
