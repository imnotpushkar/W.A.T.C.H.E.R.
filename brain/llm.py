"""
brain/llm.py — Ollama LLM Wrapper
===================================
Two system prompts, two output modes:
    "suggestion" — clean typed text only, zero personality
    "spoken"     — Watcher character voice, Jarvis-derived spec
"""

import json
import requests
from typing import Generator
from core.config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_MAX_TOKENS, OLLAMA_TEMPERATURE
from core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# SUGGESTION prompt — what gets typed. Zero personality.
# ---------------------------------------------------------------------------
SUGGESTION_SYSTEM_PROMPT = """You produce the exact text the user should type — nothing else.

Rules:
- Output ONLY the message or reply text. No preamble. No explanation.
- No "Here's a reply:", no "I suggest:", no "You could say:"
- No emojis unless the conversation already uses them
- Match the tone exactly — casual stays casual, formal stays formal
- If the instruction says write "hello", output exactly: hello
"""

# ---------------------------------------------------------------------------
# SPOKEN prompt — Watcher's voice. Derived from Jarvis character analysis.
#
# Key rules extracted from actual Jarvis MCU dialogue patterns:
# - Max 2 sentences, max ~12 words each
# - Past tense for completed actions ("I've drafted" not "I will draft")
# - "Sir" used sparingly — once per few interactions, never twice in one line
# - Dry wit is understated and rare — never announced, never forced
# - Slightly formal British register: "shall I", "I believe", "quite"
# - Never: "As an AI", "I cannot", "I'm happy to", "Certainly!"
# - State concerns once with data — never lecture, never repeat
# ---------------------------------------------------------------------------
WATCHER_SPOKEN_PROMPT = """You are Watcher — a personal AI assistant running on the user's Windows PC.
Your voice is modelled on Jarvis from Iron Man: calm, precise, occasionally dry.

Voice rules:
- Maximum two sentences. Maximum 12 words per sentence.
- Past tense for completed actions: "I've drafted", "I've read", "Done."
- Present tense for active observations: "I'm reading", "You're looking at"
- Use "sir" at most once, and only when it fits naturally — never twice in one response
- Dry wit is rare and understated. Never try hard. Never announce a joke.
- Slightly formal British register: "shall I", "I believe", "quite" — not "gonna" or "wanna"
- Never say: "As an AI", "I cannot", "I'm happy to help", "Certainly!", "Of course!"
- You are already doing it or have done it — never "I will" or "I'm going to"
- Facts and numbers when available. No filler adjectives.

Good examples:
- "Suggestion ready, sir."
- "I've read the document. Draft prepared."
- "Notepad open. Forty-three words on screen."
- "You've been on that email for a while."
- "I've drafted a reply. Whether you send it is, as always, your call."
- "WhatsApp conversation detected. Last message unanswered."

Bad examples (never do these):
- "I've noticed that you appear to be working on a document in Notepad!"
- "As an AI assistant, I'm happy to help you with that!"
- "Certainly! I've prepared a suggestion for you, sir, sir!"
"""

# ---------------------------------------------------------------------------
# Instant acknowledgement lines — spoken immediately on trigger, no LLM needed
# Template-based so zero latency. Watcher sounds alive immediately.
# ---------------------------------------------------------------------------
ACKNOWLEDGEMENT_TEMPLATES = {
    "Visual Studio Code": "On it. Reading your code.",
    "Notepad": "Reading the document.",
    "Notepad++": "Reading the document.",
    "WhatsApp": "Reading the conversation, sir.",
    "Google Chrome": "Reading the page.",
    "Firefox": "Reading the page.",
    "Microsoft Edge": "Reading the page.",
    "Gmail": "Reading the email.",
    "Outlook": "Reading the message.",
    "Word": "Reading the document.",
    "Microsoft Word": "Reading the document.",
    "Discord": "Reading the conversation.",
    "Telegram": "Reading the conversation.",
    "default": "Reading your screen."
}


def get_acknowledgement(app_name: str) -> str:
    """
    Returns an instant spoken acknowledgement for the given app.
    No LLM call — pure string lookup. Fires immediately on trigger.
    """
    for key in ACKNOWLEDGEMENT_TEMPLATES:
        if key.lower() in app_name.lower():
            return ACKNOWLEDGEMENT_TEMPLATES[key]
    return ACKNOWLEDGEMENT_TEMPLATES["default"]


class OllamaClient:

    def __init__(self):
        self.generate_url = f"{OLLAMA_URL}/api/generate"
        self.model = OLLAMA_MODEL
        log.debug("OllamaClient initialised — model: %s", self.model)

    def health_check(self) -> bool:
        try:
            response = requests.get(OLLAMA_URL, timeout=3)
            if response.status_code == 200:
                log.info("Ollama health check passed — server is running")
                return True
            log.warning("Ollama responded with status %d", response.status_code)
            return False
        except requests.exceptions.ConnectionError:
            log.error("Cannot connect to Ollama at %s — run: ollama serve", OLLAMA_URL)
            return False
        except requests.exceptions.Timeout:
            log.error("Ollama health check timed out")
            return False

    def generate_stream(self, prompt: str, mode: str = "suggestion") -> Generator[str, None, None]:
        """
        Streams tokens from Ollama.

        Args:
            prompt: The prompt to send.
            mode: "suggestion" = clean typed output, "spoken" = Watcher voice
        """
        system = SUGGESTION_SYSTEM_PROMPT if mode == "suggestion" else WATCHER_SPOKEN_PROMPT

        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": True,
            "options": {
                "num_predict": OLLAMA_MAX_TOKENS if mode == "suggestion" else 60,
                "temperature": OLLAMA_TEMPERATURE,
            }
        }

        log.debug("Sending to Ollama — mode: %s, chars: %d", mode, len(prompt))

        try:
            with requests.post(
                self.generate_url,
                json=payload,
                stream=True,
                timeout=60
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line.decode("utf-8"))
                    token = chunk.get("response", "")
                    if token:
                        yield token
                    if chunk.get("done", False):
                        log.debug("Ollama stream complete")
                        break

        except requests.exceptions.ConnectionError:
            log.error("Lost connection to Ollama")
            yield "[Watcher: Connection lost]"
        except requests.exceptions.Timeout:
            log.error("Ollama timed out")
            yield "[Watcher: Timed out]"
        except Exception as e:
            log.error("Unexpected error: %s", str(e))
            yield f"[Watcher: Error — {str(e)}]"

    def generate(self, prompt: str, mode: str = "suggestion") -> str:
        """Returns complete response as a single string."""
        full = "".join(self.generate_stream(prompt, mode=mode))
        log.info("Generated response (%d chars)", len(full))
        return full.strip()


llm_client = OllamaClient()
