"""
main.py — Watcher Entry Point
==============================
Initialises all components in the correct order and starts the event loop.
"""

import sys
import signal
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

from core.logger import setup_logging, get_logger
from core.config import WATCHER_HOTKEY


def main():
    # Step 1: Logging first — everything else logs
    setup_logging()
    log = get_logger(__name__)
    log.info("=" * 60)
    log.info("WATCHER starting up")
    log.info("=" * 60)

    # Step 2: Verify Ollama is running before going further
    from brain.llm import llm_client
    if not llm_client.health_check():
        log.critical(
            "Ollama is not running.\n"
            "Run this in a separate terminal: ollama serve\n"
            "Then restart Watcher."
        )
        sys.exit(1)

    # Step 3: Qt application — must exist before any QWidget
    app = QApplication(sys.argv)
    app.setApplicationName("Watcher")
    app.setQuitOnLastWindowClosed(False)
    log.info("Qt application created")

    # Step 4: Initialise all components
    from output.overlay import WatcherOverlay
    from output.inline_suggest import inline_suggestion
    from output.typist import typist
    from output.voice_output import voice_output
    from core.orchestrator import orchestrator

    overlay = WatcherOverlay()
    log.info("All components initialised")

    # Step 5: Wire dependencies
    inline_suggestion.set_dependencies(overlay=overlay, typist=typist)
    orchestrator.set_dependencies(
        voice_output=voice_output,
        inline_suggestion=inline_suggestion
    )
    log.info("Dependencies wired")

    # Step 6: Start hotkey listener
    from input.keyboard_hook import HotkeyListener
    hotkey_listener = HotkeyListener(on_trigger=orchestrator.handle_trigger)
    hotkey_listener.start()
    log.info("Hotkey listener started — press %s to trigger Watcher", WATCHER_HOTKEY)

    # Step 7: Ctrl+C fix — QTimer polls shutdown flag every 200ms
    # Qt's event loop blocks Python's signal handler on Windows.
    # A QTimer that fires regularly gives Python a chance to check for SIGINT.
    _shutdown_flag = [False]

    def _on_sigint(sig, frame):
        log.info("Ctrl+C received — shutting down")
        _shutdown_flag[0] = True

    signal.signal(signal.SIGINT, _on_sigint)

    def _check_shutdown():
        if _shutdown_flag[0]:
            log.info("Stopping Watcher...")
            overlay.shutdown()       # Set _is_alive=False BEFORE Qt tears down
            hotkey_listener.stop()
            app.quit()

    shutdown_timer = QTimer()
    shutdown_timer.timeout.connect(_check_shutdown)
    shutdown_timer.start(200)

    # Step 8: Enter Qt event loop — blocks here until app.quit() is called
    log.info("Watcher is running. Press %s to trigger. Ctrl+C to stop.", WATCHER_HOTKEY)
    exit_code = app.exec()
    log.info("Watcher stopped (exit code: %d)", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
