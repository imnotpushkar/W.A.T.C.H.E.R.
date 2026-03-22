"""
core/orchestrator.py — Central Router and State Manager
=========================================================
The orchestrator is the brain stem of Watcher. It connects every component
and defines WHAT HAPPENS when the hotkey is pressed.

WHAT DOES AN ORCHESTRATOR DO?
    In software architecture, an orchestrator coordinates multiple services
    to complete a workflow. It doesn't do the work itself — it tells other
    components what to do and in what order.

    Watcher's Phase 1 workflow when hotkey fires:
        1. Read the active window (screen_reader)
        2. Build a prompt from that screen content
        3. Send prompt to Ollama (llm_client) — streaming
        4. Show streaming tokens in overlay (inline_suggestion)
        5. Speak the full response (voice_output)

    The orchestrator owns this sequence. If we add memory in Phase 2,
    we add it HERE — between steps 2 and 3. No other file changes.

STATE MANAGEMENT:
    _is_processing flag prevents overlapping triggers. If Watcher is already
    generating a response and the hotkey is pressed again, we ignore the
    second press. This prevents multiple simultaneous Ollama requests.

THREAD SAFETY:
    The orchestrator's handle_trigger() runs in a background thread
    (spawned by keyboard_hook.py). It touches shared state (_is_processing).
    We use threading.Lock() to prevent race conditions.

    A RACE CONDITION is when two threads read/modify shared data simultaneously,
    producing unpredictable results. Example without lock:
        Thread A reads _is_processing = False
        Thread B reads _is_processing = False  (before A updates it)
        Both threads proceed — now two requests run simultaneously.
    With a lock, only one thread can check-and-set at a time.
"""

import threading
from core.logger import get_logger
from brain.llm import llm_client
from input.screen_reader import screen_reader

log = get_logger(__name__)


class Orchestrator:
    """
    Coordinates all Watcher components in response to trigger events.
    """

    def __init__(self):
        self._is_processing = False
        self._lock = threading.Lock()
        # These are set after all components initialise in main.py
        self._voice_output = None
        self._inline_suggestion = None
        log.debug("Orchestrator initialised")

    def set_dependencies(self, voice_output, inline_suggestion) -> None:
        """
        Injects output components after they're created.
        Called from main.py after all singletons exist.
        """
        self._voice_output = voice_output
        self._inline_suggestion = inline_suggestion
        log.debug("Orchestrator dependencies set")

    def handle_trigger(self) -> None:
        """
        Called by the keyboard hook when the hotkey is pressed.
        Runs in a background thread — must be thread-safe.

        This is the main Phase 1 workflow.
        """
        # Thread-safe check: only one trigger can be processing at a time
        # threading.Lock() as context manager: acquires lock on enter,
        # releases on exit (even if an exception occurs)
        with self._lock:
            if self._is_processing:
                log.info("Trigger ignored — already processing a request")
                return
            self._is_processing = True

        try:
            self._run_pipeline()
        except Exception as e:
            log.error("Pipeline error: %s", str(e), exc_info=True)
            # exc_info=True tells the logger to include the full stack trace
        finally:
            # Always release the lock — even if pipeline crashed
            with self._lock:
                self._is_processing = False

    def _run_pipeline(self) -> None:
        """
        The full Phase 1 pipeline: observe → think → respond.
        """
        # ---------------------------------------------------------------
        # Step 1: Read the screen
        # ---------------------------------------------------------------
        log.info("Pipeline started — reading screen")
        screen_data = screen_reader.read_active_window()

        app_name = screen_data.get("app_name", "Unknown")
        window_title = screen_data.get("window_title", "")
        text_content = screen_data.get("text_content", "")
        focused_text = screen_data.get("focused_text", "")

        log.info("Screen read complete — app: %s, chars: %d",
                 app_name, len(text_content))

        if not text_content or text_content.startswith("[Screen content unavailable"):
            log.warning("No screen content available — using window title only")

        # ---------------------------------------------------------------
        # Step 2: Build the prompt
        # ---------------------------------------------------------------
        prompt = self._build_prompt(
            app_name=app_name,
            window_title=window_title,
            text_content=text_content,
            focused_text=focused_text
        )
        log.debug("Prompt built (%d chars)", len(prompt))

        # ---------------------------------------------------------------
        # Step 3: Stream response from Ollama, show in overlay
        # ---------------------------------------------------------------
        log.info("Sending to Ollama...")
        full_response = ""

        for token in llm_client.generate_stream(prompt):
            full_response += token
            # Show each token in the overlay as it arrives
            if self._inline_suggestion:
                self._inline_suggestion.show_streaming(token)

        log.info("Ollama response complete (%d chars)", len(full_response))

        if not full_response or full_response.startswith("[Watcher:"):
            log.warning("Empty or error response from Ollama")
            return

        # ---------------------------------------------------------------
        # Step 4: Speak the response
        # ---------------------------------------------------------------
        if self._voice_output:
            # Non-blocking — speech plays in background
            self._voice_output.speak(full_response, blocking=False)

    def _build_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
        focused_text: str
    ) -> str:
        """
        Assembles the screen context into a prompt for Ollama.

        PROMPT ENGINEERING:
            How you structure the prompt dramatically affects response quality.
            Key principles we use here:
            1. Tell the model what app context it's seeing
            2. Separate different pieces of context clearly
            3. Give a specific instruction at the end
            4. Keep it concise — shorter prompts = faster responses

            In Phase 2, this method will also include memory (past conversations)
            and contact profiles. The structure we establish here makes
            adding those easy — just more sections before the instruction.
        """
        sections = []

        sections.append(f"APP: {app_name}")

        if window_title and window_title != app_name:
            sections.append(f"WINDOW: {window_title}")

        if focused_text:
            sections.append(f"USER IS TYPING: {focused_text}")

        if text_content and not text_content.startswith("[Screen content"):
            # Truncate very long content with a note
            if len(text_content) > 2000:
                text_content = text_content[-2000:]
                sections.append(f"SCREEN CONTENT (recent):\n{text_content}")
            else:
                sections.append(f"SCREEN CONTENT:\n{text_content}")

        sections.append(
            "Based on what you see above, provide a helpful, concise response or suggestion."
        )

        return "\n\n".join(sections)


# Module-level singleton
orchestrator = Orchestrator()
