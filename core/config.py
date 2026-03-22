"""
core/config.py — Global Configuration Loader
=============================================
This is the single source of truth for all settings in Watcher.

HOW IT WORKS:
    1. python-dotenv reads the .env file from the project root.
    2. The values are loaded into os.environ (Python's environment variable dict).
    3. This module reads from os.environ and exposes typed constants.
    4. Every other module imports from HERE — never from os.environ directly.

WHY THIS MATTERS:
    - If you rename a config key, you change it in ONE place (here), not 20 files.
    - Types are enforced here (int, float, bool) so other modules don't do type casting.
    - If a required variable is missing, we fail loudly at startup, not randomly later.

EDUCATIONAL NOTE — os.environ:
    os.environ is a dictionary-like object that holds all environment variables.
    On Windows, it includes system variables like PATH, USERPROFILE, etc.
    python-dotenv's load_dotenv() adds our .env variables into this same dict.
    os.getenv(key, default) reads from it, returning `default` if key doesn't exist.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: find and load the .env file
# ---------------------------------------------------------------------------

# Path(__file__) is the absolute path to THIS file (core/config.py).
# .parent goes up one level to core/
# .parent again goes up to the project root (watcher/)
# This works regardless of which directory you run the script from.
PROJECT_ROOT = Path(__file__).parent.parent

# load_dotenv() searches for a .env file and loads it into os.environ.
# If the file doesn't exist, it silently does nothing (no crash).
# override=False means: if a variable is already set in the system environment,
# don't overwrite it. This lets you override settings without editing .env.
load_dotenv(PROJECT_ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# Helper: read a required variable (crashes with a clear message if missing)
# ---------------------------------------------------------------------------

def _require(key: str) -> str:
    """
    Reads an environment variable that MUST exist.
    If it's missing, raises a clear error at startup rather than a confusing
    AttributeError or None-type crash somewhere deep in the code later.
    """
    value = os.getenv(key)
    if value is None:
        raise EnvironmentError(
            f"[Watcher] Required config key '{key}' is missing from .env\n"
            f"Copy .env.example to .env and fill in all values."
        )
    return value


# ---------------------------------------------------------------------------
# Ollama — Local LLM settings
# ---------------------------------------------------------------------------

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
# The model name must match what you've pulled via `ollama pull`
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
# int() converts the string from .env to an actual integer
OLLAMA_MAX_TOKENS: int = int(os.getenv("OLLAMA_MAX_TOKENS", "512"))
# float() converts to decimal number
OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))


# ---------------------------------------------------------------------------
# Hotkey — what key combo triggers Watcher
# ---------------------------------------------------------------------------

WATCHER_HOTKEY: str = os.getenv("WATCHER_HOTKEY", "ctrl+space")


# ---------------------------------------------------------------------------
# Overlay — the floating suggestion window
# ---------------------------------------------------------------------------

OVERLAY_OPACITY: float = float(os.getenv("OVERLAY_OPACITY", "0.92"))
OVERLAY_TIMEOUT_MS: int = int(os.getenv("OVERLAY_TIMEOUT_MS", "8000"))


# ---------------------------------------------------------------------------
# Voice Output — TTS settings
# ---------------------------------------------------------------------------

TTS_ENGINE: str = os.getenv("TTS_ENGINE", "edge-tts")
TTS_VOICE: str = os.getenv("TTS_VOICE", "en-IN-NeerjaNeural")
TTS_VOLUME: float = float(os.getenv("TTS_VOLUME", "0.9"))


# ---------------------------------------------------------------------------
# Screen Reader — how Watcher reads your screen
# ---------------------------------------------------------------------------

SCREEN_READ_TIMEOUT: int = int(os.getenv("SCREEN_READ_TIMEOUT", "3"))
SCREEN_READ_MAX_CHARS: int = int(os.getenv("SCREEN_READ_MAX_CHARS", "4000"))


# ---------------------------------------------------------------------------
# Typist — how Watcher types on your behalf
# ---------------------------------------------------------------------------

TYPIST_CHAR_DELAY: float = float(os.getenv("TYPIST_CHAR_DELAY", "0.02"))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG")
# PROJECT_ROOT / "data/watcher.log" builds the full absolute path
LOG_FILE: Path = PROJECT_ROOT / os.getenv("LOG_FILE", "data/watcher.log")


# ---------------------------------------------------------------------------
# Data Paths — where Watcher stores its data
# ---------------------------------------------------------------------------

DB_PATH: Path = PROJECT_ROOT / os.getenv("DB_PATH", "data/watcher.db")
CHROMA_PATH: Path = PROJECT_ROOT / os.getenv("CHROMA_PATH", "data/chroma")


# ---------------------------------------------------------------------------
# Ensure data directories exist
# ---------------------------------------------------------------------------
# mkdir(parents=True) creates all parent directories if they don't exist.
# exist_ok=True means: don't crash if the directory already exists.
# This runs every time config is imported, ensuring folders are always there.

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
CHROMA_PATH.mkdir(parents=True, exist_ok=True)
