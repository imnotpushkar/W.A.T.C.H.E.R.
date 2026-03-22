"""
brain/llm.py — Ollama LLM Wrapper
===================================
Wraps all communication with the local Ollama server.
Generates two kinds of output:
    1. TYPED output — the clean text that gets typed into apps (no personality)
    2. SPOKEN output — Jarvis-style spoken commentary on what Watcher is doing

WHY TWO OUTPUTS?
    The text that gets typed into WhatsApp or an email must be clean —
    just the message, nothing else. But the voice should have personality.
    Jarvis doesn't just recite text — he comments, he observes, he has tone.
    We achieve this by generating the typed suggestion first, then generating
    a separate spoken line that contextualises what Watcher just did.
"""

import json
import requests
from typing import Generator
from core.config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_MAX_TOKENS, OLLAMA_TEMPERATURE
from core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# System prompt for TYPED suggestions — clean, no personality
# ---------------------------------------------------------------------------
SUGGESTION_SYSTEM_PROMPT = """You are a silent text assistant. Your only job is to
produce the exact text the user should send or type — nothing else.

Rules (never break these):
- Output ONLY the message/reply text. Zero preamble, zero explanation.
- No "Here's a reply:", no "I suggest:", no "You could say:"
- No emojis unless the conversation uses them
- Match the tone of the conversation exactly (casual stays casual, formal stays formal)
- If asked to write "hello", output: hello
"""

# ---------------------------------------------------------------------------
# System prompt for SPOKEN commentary — Jarvis personality
# ---------------------------------------------------------------------------
JARVIS_SYSTEM_PROMPT = """You are Watcher — a sharp, calm, intelligent AI assistant
modelled after Jarvis from Iron Man. You observe the user's screen and assist them.

Your spoken personality:
- Confident and direct. Never uncertain or wishy-washy.
- Dry wit when appropriate, but never at the wrong moment.
- Refer to the user as "sir" occasionally (not every time — only when natural).
- Short sentences. You speak, not write essays.
- Never say "As an AI" or "I cannot" — you always have an answer.
- You've already taken action — tell the user what you did, not what you're about to do.

Examples of good Watcher spoken lines:
- "Done. I've drafted a reply to that message."
- "You're looking at a settings file. The highlighted option controls startup behaviour."
- "Suggestion ready, sir. Press Tab to send it."
- "That email's been open for a while. I've drafted a response."

Keep it to 1-2 sentences maximum.
"""


class OllamaClient:

    def __init__(self):
        self.generate_url = f"{OLLAMA_URL}/api/generate"
        self.model = OLLAMA_MODEL
        log.debug("OllamaClient initialised — model: %s", self.model)

    def health_check(self) -> bool:
        """Verifies Ollama server is running."""
        try:
            response = requests.get(OLLAMA_URL, timeout=3)
            if response.status_code == 200:
                log.info("Ollama health check passed — server is running")
                return True
            log.warning("Ollama responded with status %d", response.status_code)
            return False
        except requests.exceptions.ConnectionError:
            log.error(
                "Cannot connect to Ollama at %s — run: ollama serve", OLLAMA_URL
            )
            return False
        except requests.exceptions.Timeout:
            log.error("Ollama health check timed out")
            return False

    def generate_stream(self, prompt: str, mode: str = "suggestion") -> Generator[str, None, None]:
        """
        Streams tokens from Ollama one by one as they're generated.

        Args:
            prompt: The prompt to send.
            mode: "suggestion" = clean typed output
                  "spoken"     = Jarvis-style spoken commentary

        Yields:
            Individual token strings as they arrive.
        """
        system = SUGGESTION_SYSTEM_PROMPT if mode == "suggestion" else JARVIS_SYSTEM_PROMPT

        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": True,
            "options": {
                "num_predict": OLLAMA_MAX_TOKENS,
                "temperature": OLLAMA_TEMPERATURE,
            }
        }

        log.debug("Sending prompt to Ollama — mode: %s, chars: %d", mode, len(prompt))

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
            log.error("Lost connection to Ollama during generation")
            yield "[Watcher: Connection lost]"
        except requests.exceptions.Timeout:
            log.error("Ollama generation timed out")
            yield "[Watcher: Timed out]"
        except Exception as e:
            log.error("Unexpected error: %s", str(e))
            yield f"[Watcher: Error — {str(e)}]"

    def generate(self, prompt: str, mode: str = "suggestion") -> str:
        """
        Returns complete response as a single string.
        Used for TTS and non-streaming operations.
        """
        full = "".join(self.generate_stream(prompt, mode=mode))
        log.info("Generated response (%d chars)", len(full))
        return full.strip()


llm_client = OllamaClient()
