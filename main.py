"""
main.py — Watcher Entry Point
==============================
This is the file you run to start Watcher. It:
    1. Sets up logging
    2. Checks Ollama is running
    3. Creates the Qt application (required before any UI)
    4. Initialises all components
    5. Wires them together (dependency injection)
    6. Starts the hotkey listener
    7. Enters the Qt event loop (keeps everything alive)

WHY DOES ORDER MATTER HERE?
    - Logging must be set up FIRST so every subsequent step can log.
    - QApplication must be created BEFORE any QWidget (overlay).
      Qt enforces this — creating a widget without an app crashes.
    - Dependencies must be injected AFTER all singletons are created.
      You can't inject the overlay into inline_suggestion before
      the overlay exists.
    - The Qt event loop (app.exec()) must be the LAST thing called.
      It blocks forever, processing UI events. Everything else runs
      in background threads while this loop keeps the UI alive.

WHAT IS AN EVENT LOOP?
    Qt's event loop is a while-true loop that:
        - Checks for pending UI events (mouse clicks, window redraws, timers)
        - Dispatches them to the appropriate handlers
        - Repeats forever until quit() is called

    This is how ALL GUI frameworks work. The event loop is what keeps
    the application window alive and responsive. Without it, the program
    would create the window and immediately exit.

    Our background threads (keyboard listener, Ollama calls) run
    independently of this loop. The loop only handles Qt UI events.
"""

import sys
import signal
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from core.logger import setup_logging, get_logger
from core.config import WATCHER_HOTKEY


def main():
    # -----------------------------------------------------------------------
    # Step 1: Logging — must be first
    # -----------------------------------------------------------------------
    setup_logging()
    log = get_logger(__name__)
    log.info("=" * 60)
    log.info("WATCHER starting up")
    log.info("=" * 60)

    # -----------------------------------------------------------------------
    # Step 2: Check Ollama is running
    # -----------------------------------------------------------------------
    from brain.llm import llm_client
    if not llm_client.health_check():
        log.critical(
            "Ollama is not running. Start it with: ollama serve\n"
            "Watcher cannot function without the local LLM."
        )
        sys.exit(1)  # Exit with error code 1 (non-zero = failure)

    # -----------------------------------------------------------------------
    # Step 3: Create Qt Application
    # -----------------------------------------------------------------------
    # QApplication manages the GUI application's control flow and settings.
    # sys.argv passes command-line arguments to Qt (it uses some internally).
    # This MUST exist before any QWidget is created.
    app = QApplication(sys.argv)
    app.setApplicationName("Watcher")
    app.setQuitOnLastWindowClosed(False)  # Don't quit when overlay hides

    log.info("Qt application created")

    # -----------------------------------------------------------------------
    # Step 4: Initialise all components
    # -----------------------------------------------------------------------
    # Import here (after QApplication exists) to avoid Qt widget creation
    # before the application is ready.
    from output.overlay import WatcherOverlay
    from output.inline_suggest import inline_suggestion
    from output.typist import typist
    from output.voice_output import voice_output
    from input.keyboard_hook import HotkeyListener
    from core.orchestrator import orchestrator

    # Create the overlay widget
    overlay = WatcherOverlay()
    log.info("Overlay created")

    # -----------------------------------------------------------------------
    # Step 5: Wire components together (dependency injection)
    # -----------------------------------------------------------------------
    # inline_suggestion needs overlay (to show text) and typist (to type text)
    inline_suggestion.set_dependencies(overlay=overlay, typist=typist)

    # orchestrator needs voice_output and inline_suggestion
    orchestrator.set_dependencies(
        voice_output=voice_output,
        inline_suggestion=inline_suggestion
    )

    log.info("Component dependencies wired")

    # -----------------------------------------------------------------------
    # Step 6: Start the hotkey listener
    # -----------------------------------------------------------------------
    hotkey_listener = HotkeyListener(
        on_trigger=orchestrator.handle_trigger
    )
    hotkey_listener.start()
    log.info("Watcher is active — press %s to trigger", WATCHER_HOTKEY)
    log.info("Press Ctrl+C in this terminal to stop Watcher")

    # -----------------------------------------------------------------------
    # Step 7: Handle Ctrl+C gracefully
    # -----------------------------------------------------------------------
    # signal.signal() registers a function to call when the process receives
    # a signal. SIGINT is sent when the user presses Ctrl+C in the terminal.
    # Without this, Ctrl+C would abruptly kill the process without cleanup.
    def shutdown(sig, frame):
        log.info("Shutdown signal received — stopping Watcher")
        hotkey_listener.stop()
        app.quit()  # Tells Qt event loop to exit cleanly

    signal.signal(signal.SIGINT, shutdown)

    # -----------------------------------------------------------------------
    # Step 8: Enter Qt event loop — blocks here until quit() is called
    # -----------------------------------------------------------------------
    log.info("Entering Qt event loop")
    exit_code = app.exec()
    log.info("Watcher stopped (exit code: %d)", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    # __name__ == "__main__" is True only when this file is run directly.
    # If someone imports main.py, this block doesn't execute.
    # This is Python's standard guard for entry point files.
    main()
