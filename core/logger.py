"""
core/logger.py — Structured Logging Setup
==========================================
Sets up Watcher's logging system. Every module uses this instead of print().

HOW PYTHON LOGGING WORKS:
    Python's logging module has a hierarchy of severity levels:
        DEBUG    → detailed diagnostic info (dev only)
        INFO     → normal operation ("Watcher started", "Hotkey triggered")
        WARNING  → something unexpected but not fatal ("Ollama slow to respond")
        ERROR    → something failed but Watcher keeps running
        CRITICAL → something failed that stops Watcher entirely

    A Logger is named after the module it belongs to.
    All loggers feed into the ROOT logger which decides where output goes.

    Handlers are the "destinations" for log output:
        StreamHandler → prints to console (terminal)
        FileHandler   → writes to a file

    Formatters control the layout of each log line.

HOW TO USE IN OTHER MODULES:
    from core.logger import get_logger
    log = get_logger(__name__)   # __name__ is automatically the module name
    log.info("Watcher started")
    log.debug("Screen text: %s", text[:100])
    log.error("Ollama failed: %s", str(e))

WHY NOT JUST USE PRINT():
    - print() has no level filtering (can't turn off debug prints in production)
    - print() has no timestamps
    - print() doesn't tell you which module the message came from
    - print() doesn't write to files
    - logging does all of this automatically
"""

import logging
import sys
from pathlib import Path

# Import config here — config has no dependencies so this is safe
from core.config import LOG_LEVEL, LOG_FILE


# ---------------------------------------------------------------------------
# Module-level state: track whether setup has already run
# ---------------------------------------------------------------------------
# This prevents the setup from running multiple times if logger.py is
# imported from several different modules (which it will be).
_configured = False


def setup_logging() -> None:
    """
    Configure the root logger with two handlers:
        1. Console handler — coloured output to terminal
        2. File handler — plain text to data/watcher.log

    This should be called ONCE at startup from main.py.
    All subsequent calls are no-ops (guarded by _configured flag).
    """
    global _configured
    if _configured:
        return

    # Convert the string log level ("DEBUG", "INFO", etc.) to the integer
    # constant that logging expects. logging.getLevelName() does this conversion.
    numeric_level = getattr(logging, LOG_LEVEL.upper(), logging.DEBUG)

    # -----------------------------------------------------------------------
    # Formatter — defines the layout of each log line
    # -----------------------------------------------------------------------
    # %(asctime)s     → timestamp like "2025-03-22 14:30:01,234"
    # %(name)-20s     → logger name, padded to 20 chars (the module name)
    # %(levelname)-8s → level like "DEBUG   " or "ERROR   ", padded to 8
    # %(message)s     → the actual message you passed to log.info() etc.

    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console formatter is slightly simpler — no date, just time
    console_formatter = logging.Formatter(
        fmt="%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S"
    )

    # -----------------------------------------------------------------------
    # Handler 1: Console (stdout)
    # -----------------------------------------------------------------------
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_formatter)

    # -----------------------------------------------------------------------
    # Handler 2: File
    # -----------------------------------------------------------------------
    # encoding="utf-8" ensures emoji, Hindi characters, etc. don't crash logging
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)   # Always log everything to file
    file_handler.setFormatter(file_formatter)

    # -----------------------------------------------------------------------
    # Root logger — the parent of all loggers
    # -----------------------------------------------------------------------
    # Setting level on root logger controls what bubbles up from child loggers.
    # We set it to DEBUG so nothing is filtered before reaching the handlers.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Silence noisy third-party libraries that use logging internally.
    # Without this, libraries like httpx, urllib3 flood your console with
    # their own debug messages.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger for a module.

    Usage in any module:
        from core.logger import get_logger
        log = get_logger(__name__)

    __name__ in Python is automatically set to the module's dotted path,
    e.g. "brain.llm" or "input.screen_reader". This means log output
    automatically identifies exactly which file it came from.
    """
    return logging.getLogger(name)
