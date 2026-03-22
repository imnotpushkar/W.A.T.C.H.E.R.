"""
brain/voice_templates.py — Watcher Voice Template Bank
========================================================
All spoken lines Watcher will ever say in Phase 1.
Zero LLM involvement — pure string selection based on context.

WHY TEMPLATES INSTEAD OF LLM FOR VOICE?
    LLM-generated voice has two problems on local hardware:
    1. Latency — Llama 3.1 8B takes 2-3s to generate even 2 sentences.
       Templates fire in microseconds.
    2. Reliability — small models leak prompt instructions, repeat themselves,
       or generate gibberish. Templates always sound right.

    Jarvis himself was largely templated — the writers gave Paul Bettany
    specific lines for specific situations. The intelligence was in WHICH
    line was chosen, not in generating new ones on the fly.

TEMPLATE STRUCTURE:
    Templates are organised by STAGE (when in the pipeline) and APP (context).
    The selector picks the most specific match available, falling back to
    generic lines when no specific match exists.

    Stages:
        "ack"         — immediately on trigger (screen read starting)
        "thinking"    — Ollama is generating (filler, rarely used)
        "ready"       — suggestion is in overlay, ready to accept
        "accepted"    — user pressed Tab, text was typed
        "dismissed"   — user pressed Esc
        "error"       — something went wrong

RANDOMISATION:
    Each entry is a list — we pick randomly from it so Watcher doesn't
    say the exact same line every single time. Enough variety that it
    doesn't feel mechanical, not so much variety that it feels inconsistent.
"""

import random
from typing import Optional
from core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Template bank
# ---------------------------------------------------------------------------
# Structure: TEMPLATES[stage][app_key] = [list of lines]
# app_key "default" is the fallback when no specific app matches.

TEMPLATES = {

    # -----------------------------------------------------------------------
    # ACK — fires immediately when hotkey is pressed
    # -----------------------------------------------------------------------
    "ack": {
        "whatsapp": [
            "Reading the conversation.",
            "On it. Checking the thread.",
            "Reading your messages, sir.",
        ],
        "discord": [
            "Reading the channel.",
            "On it.",
            "Checking the conversation.",
        ],
        "gmail": [
            "Reading the email.",
            "On it. Checking your inbox.",
            "Reading the message, sir.",
        ],
        "outlook": [
            "Reading the message.",
            "On it.",
            "Checking your mail.",
        ],
        "notepad": [
            "Reading the document.",
            "On it.",
            "Reading your notes.",
        ],
        "word": [
            "Reading the document.",
            "On it. Checking the file.",
            "Reading your document, sir.",
        ],
        "chrome": [
            "Reading the page.",
            "On it.",
            "Checking the content.",
        ],
        "firefox": [
            "Reading the page.",
            "On it.",
        ],
        "edge": [
            "Reading the page.",
            "On it.",
        ],
        "visual studio code": [
            "Reading the file.",
            "On it. Checking your code.",
            "Reading the editor.",
        ],
        "telegram": [
            "Reading the conversation.",
            "On it.",
        ],
        "default": [
            "Reading your screen.",
            "On it.",
            "On it, sir.",
            "Reading your screen, sir.",
            "Got it.",
        ],
    },

    # -----------------------------------------------------------------------
    # READY — suggestion is in overlay, waiting for Tab or Esc
    # -----------------------------------------------------------------------
    "ready": {
        "whatsapp": [
            "Draft ready. Press Tab to send it.",
            "Reply prepared, sir.",
            "Suggestion ready. Tab to send.",
            "I've drafted a reply.",
        ],
        "gmail": [
            "Draft ready. Press Tab to insert it.",
            "Reply drafted, sir.",
            "I've written a response. Tab to insert.",
            "Suggestion ready.",
        ],
        "outlook": [
            "Draft ready.",
            "Reply prepared, sir.",
            "Suggestion ready. Tab to insert.",
        ],
        "notepad": [
            "Suggestion ready. Press Tab to accept.",
            "Draft prepared.",
            "Ready, sir. Tab to insert.",
            "Done. Suggestion is in the overlay.",
        ],
        "word": [
            "Draft ready. Tab to insert.",
            "Suggestion prepared, sir.",
            "I've drafted something. Tab to accept.",
        ],
        "visual studio code": [
            "Suggestion ready. Tab to accept.",
            "Done. Tab to insert.",
            "Ready, sir.",
        ],
        "default": [
            "Suggestion ready. Press Tab to accept.",
            "Done. Tab to accept, Escape to dismiss.",
            "Ready, sir.",
            "Suggestion prepared.",
            "Draft ready.",
            "Done.",
        ],
    },

    # -----------------------------------------------------------------------
    # ACCEPTED — user pressed Tab, text was typed
    # -----------------------------------------------------------------------
    "accepted": {
        "whatsapp": [
            "Sent.",
            "Done.",
            "Reply inserted.",
        ],
        "gmail": [
            "Inserted.",
            "Done, sir.",
            "Draft inserted.",
        ],
        "default": [
            "Done.",
            "Inserted.",
            "Done, sir.",
            "All yours.",
        ],
    },

    # -----------------------------------------------------------------------
    # DISMISSED — user pressed Esc
    # -----------------------------------------------------------------------
    "dismissed": {
        "default": [
            "Dismissed.",
            "Understood.",
            "Standing by.",
            "As you wish.",
            "Noted.",
        ],
    },

    # -----------------------------------------------------------------------
    # ERROR — something went wrong
    # -----------------------------------------------------------------------
    "error": {
        "ollama_down": [
            "I've lost contact with the local model, sir.",
            "The AI brain isn't responding.",
            "Ollama isn't running. I can't process that.",
        ],
        "no_content": [
            "Nothing readable on screen.",
            "I couldn't read that window, sir.",
            "Screen content unavailable.",
        ],
        "default": [
            "Something went wrong.",
            "I encountered an error, sir.",
            "That didn't work. Check the logs.",
        ],
    },
}


# ---------------------------------------------------------------------------
# Template selector
# ---------------------------------------------------------------------------

def get_line(stage: str, app_name: str = "", error_key: str = "") -> str:
    """
    Returns a random line for the given stage and app context.

    HOW THE LOOKUP WORKS:
        1. Normalise app_name to lowercase for matching
        2. Check if any key in the stage dict is a substring of the app name
           e.g. app_name="Google Chrome" matches key="chrome"
        3. If no specific match, use "default"
        4. Pick randomly from the matched list

    Args:
        stage:     "ack", "ready", "accepted", "dismissed", "error"
        app_name:  The active app name (e.g. "Google Chrome", "WhatsApp")
        error_key: For error stage — "ollama_down", "no_content", or "default"

    Returns:
        A single string to pass to voice_output.speak()
    """
    stage_templates = TEMPLATES.get(stage)
    if not stage_templates:
        log.warning("Unknown voice template stage: %s", stage)
        return ""

    # For error stage, use error_key directly
    if stage == "error":
        lines = stage_templates.get(error_key) or stage_templates.get("default", [])
        return random.choice(lines) if lines else ""

    # For other stages, match by app name
    app_lower = app_name.lower()
    matched_lines = None

    for key, lines in stage_templates.items():
        if key == "default":
            continue
        if key in app_lower:
            matched_lines = lines
            break

    if not matched_lines:
        matched_lines = stage_templates.get("default", [])

    if not matched_lines:
        return ""

    chosen = random.choice(matched_lines)
    log.debug("Voice template: stage=%s app=%s → '%s'", stage, app_name, chosen)
    return chosen


# ---------------------------------------------------------------------------
# Convenience functions — used directly by orchestrator
# ---------------------------------------------------------------------------

def ack(app_name: str) -> str:
    """Instant acknowledgement line. Called immediately on trigger."""
    return get_line("ack", app_name)


def ready(app_name: str) -> str:
    """'Suggestion is ready' line. Called after overlay is finalized."""
    return get_line("ready", app_name)


def accepted(app_name: str) -> str:
    """'Done' line. Called after Tab-accept types the suggestion."""
    return get_line("accepted", app_name)


def dismissed(app_name: str = "") -> str:
    """'Dismissed' line. Called after Esc."""
    return get_line("dismissed", app_name)


def error(error_key: str = "default") -> str:
    """Error line. Called when something goes wrong."""
    return get_line("error", error_key=error_key)
