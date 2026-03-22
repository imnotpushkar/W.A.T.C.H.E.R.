"""
core/logger.py — Structured Logging Setup
==========================================
Sets up Watcher's logging system. Every module uses this instead of print().
"""

import logging
import sys
from core.config import LOG_LEVEL, LOG_FILE

_configured = False


def setup_logging() -> None:
    """
    Configure the root logger with console and file handlers.
    Call once at startup from main.py.
    """
    global _configured
    if _configured:
        return

    numeric_level = getattr(logging, LOG_LEVEL.upper(), logging.DEBUG)

    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_formatter = logging.Formatter(
        fmt="%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # ---------------------------------------------------------------------------
    # Silence noisy third-party loggers
    # ---------------------------------------------------------------------------
    # comtypes floods the console with every COM object creation and release.
    # These are internal uiautomation operations — not useful for us.
    # Setting to WARNING means only actual problems from these libs appear.
    noisy_loggers = [
        "httpx",
        "urllib3",
        "asyncio",
        "comtypes",                      # COM object lifecycle spam
        "comtypes.client._generate",     # Type library generation messages
        "comtypes.client._code_cache",   # Cache directory messages
        "comtypes._post_coinit.unknwn",  # IUnknown Release() calls — very spammy
        "comtypes.client._create",       # CoCreateInstance calls
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger for a module.
    Usage: log = get_logger(__name__)
    """
    return logging.getLogger(name)
