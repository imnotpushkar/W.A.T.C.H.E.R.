"""
brain/llm.py — Ollama LLM Wrapper
===================================
This module is the ONLY place in Watcher that talks to Ollama.
No other module should make direct HTTP calls to Ollama — they all go through here.

WHAT IS OLLAMA?
    Ollama is a local server that runs LLMs (Large Language Models) on your machine.
    When you run `ollama serve`, it starts an HTTP server on port 11434.
    You communicate with it by sending HTTP POST requests — exactly like calling
    any web API, except the server is running on your own PC, not in the cloud.

WHAT IS A REST API?
    REST (Representational State Transfer) is a pattern for web communication.
    You send an HTTP request to a URL with some data (the prompt).
    The server processes it and sends back a response (the generated text).
    Ollama uses the same request/response pattern as OpenAI's API — intentionally,
    so you could swap Ollama for OpenAI by just changing a URL and an API key.

WHAT IS STREAMING?
    Normally an API waits until it has the FULL response before sending anything.
    For an LLM generating 200 words, that means waiting 3-4 seconds for the first
    character. Streaming changes this: the server sends each TOKEN as it's generated.
    A token is roughly 0.75 words. You start seeing output almost immediately.
    We implement streaming so the overlay can show partial responses as they arrive.

    Technically: Ollama sends back a series of JSON objects separated by newlines.
    Each object has a "response" field with the next token, and a "done" field
    that's True on the last chunk. We read line by line and yield each token.
"""

import json
import requests
from typing import Generator
from core.config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_MAX_TOKENS, OLLAMA_TEMPERATURE
from core.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# System prompt — tells Ollama who it is and how to behave
# ---------------------------------------------------------------------------
# This is the "personality" instruction sent before every conversation.
# It's separate from the user's actual message — think of it as the
# briefing you give an employee before they start work.

SYSTEM_PROMPT = """You are Watcher — a silent, intelligent personal AI assistant
running on the user's Windows PC. You observe their screen and help them respond,
draft, summarise, and think.

Your rules:
- Be concise. The user is busy. No padding, no filler.
- When suggesting a reply to a message, write ONLY the reply text — nothing else.
- When asked to summarise, be brief and accurate.
- When asked a question, answer directly.
- Never say "As an AI..." or "I cannot..." — just help.
- Match the tone of the conversation you're reading (casual vs formal).
"""


class OllamaClient:
    """
    A client that wraps all communication with the local Ollama server.

    WHY A CLASS INSTEAD OF PLAIN FUNCTIONS?
        A class lets us store state (like the base URL and model name) once
        and reuse it across multiple method calls. It also makes it easy to
        add connection pooling, retry logic, or swap to a different LLM
        provider later — you'd just swap the class, not hunt for function calls.
    """

    def __init__(self):
        # Build the two endpoint URLs we'll use.
        # f-strings in Python: f"text {variable} text" embeds variables inline.
        self.generate_url = f"{OLLAMA_URL}/api/generate"
        self.chat_url = f"{OLLAMA_URL}/api/chat"
        self.model = OLLAMA_MODEL
        log.debug("OllamaClient initialised — model: %s, url: %s", self.model, OLLAMA_URL)

    # -----------------------------------------------------------------------
    # Health check — verify Ollama is actually running before we try anything
    # -----------------------------------------------------------------------

    def health_check(self) -> bool:
        """
        Pings Ollama's root endpoint to confirm the server is running.
        Returns True if reachable, False if not.

        Called at startup so we fail early with a clear message rather than
        crashing mid-conversation with a confusing connection error.
        """
        try:
            # requests.get() sends an HTTP GET request.
            # timeout=3 means: if no response in 3 seconds, give up.
            response = requests.get(OLLAMA_URL, timeout=3)
            # HTTP status 200 means "OK". Anything else means something's wrong.
            if response.status_code == 200:
                log.info("Ollama health check passed — server is running")
                return True
            else:
                log.warning("Ollama responded with status %d", response.status_code)
                return False
        except requests.exceptions.ConnectionError:
            # ConnectionError is raised when the server isn't running at all.
            log.error(
                "Cannot connect to Ollama at %s\n"
                "Make sure Ollama is running: open a terminal and run `ollama serve`",
                OLLAMA_URL
            )
            return False
        except requests.exceptions.Timeout:
            log.error("Ollama health check timed out after 3 seconds")
            return False

    # -----------------------------------------------------------------------
    # Streaming generation — yields tokens as they arrive
    # -----------------------------------------------------------------------

    def generate_stream(self, prompt: str) -> Generator[str, None, None]:
        """
        Sends a prompt to Ollama and yields response tokens one by one as
        they are generated. This is the main method used by the overlay
        to show partial responses in real time.

        WHAT IS A GENERATOR (yield)?
            A normal function runs, builds a complete result, and returns it all at once.
            A generator function uses `yield` instead of `return`. Each time the caller
            asks for the next value (via a for loop or next()), the function runs until
            it hits a `yield`, hands that value to the caller, then PAUSES.
            The function's local state is preserved between yields.

            This is perfect for streaming: we yield each token as it arrives,
            so the caller (the overlay) can display it immediately without waiting
            for the full response.

        Args:
            prompt: The full prompt string to send to Ollama.

        Yields:
            Individual token strings as they stream from Ollama.
        """
        # Build the request payload as a Python dict.
        # This gets serialised to JSON before sending.
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": True,                    # Enable streaming
            "options": {
                "num_predict": OLLAMA_MAX_TOKENS,
                "temperature": OLLAMA_TEMPERATURE,
            }
        }

        log.debug("Sending prompt to Ollama (%d chars)", len(prompt))

        try:
            # stream=True in requests means: don't download the full response body
            # immediately. Instead, keep the connection open and let us read it
            # line by line as data arrives.
            with requests.post(
                self.generate_url,
                json=payload,           # json= automatically sets Content-Type header
                stream=True,            # Keep connection open for streaming
                timeout=60              # Give up if no response at all after 60s
            ) as response:

                response.raise_for_status()  # Raises exception if status is 4xx/5xx

                # iter_lines() reads the response body line by line.
                # Each line from Ollama is one JSON object.
                for line in response.iter_lines():
                    if not line:
                        continue  # Skip empty lines

                    # line comes back as bytes — decode to string
                    chunk = json.loads(line.decode("utf-8"))

                    # Extract the token text from the chunk
                    token = chunk.get("response", "")
                    if token:
                        yield token  # Hand this token to whoever called us

                    # When Ollama sends "done": true, generation is complete
                    if chunk.get("done", False):
                        log.debug("Ollama stream complete")
                        break

        except requests.exceptions.ConnectionError:
            log.error("Lost connection to Ollama during generation")
            yield "[Watcher: Connection to Ollama lost]"
        except requests.exceptions.Timeout:
            log.error("Ollama generation timed out")
            yield "[Watcher: Response timed out]"
        except Exception as e:
            log.error("Unexpected error during generation: %s", str(e))
            yield f"[Watcher: Error — {str(e)}]"

    # -----------------------------------------------------------------------
    # Non-streaming generation — returns the complete response as a string
    # -----------------------------------------------------------------------

    def generate(self, prompt: str) -> str:
        """
        Sends a prompt and returns the COMPLETE response as a single string.
        Used when we don't need streaming — e.g. for TTS (you can't speak
        partial sentences) or for memory operations.

        Internally just collects all tokens from generate_stream() into one string.
        This reuses all the error handling in generate_stream() for free.

        Args:
            prompt: The full prompt string.

        Returns:
            Complete response string from Ollama.
        """
        # "".join(iterable) concatenates all strings from an iterable into one.
        # We're collecting every token yielded by generate_stream().
        full_response = "".join(self.generate_stream(prompt))
        log.info("Generated response (%d chars)", len(full_response))
        return full_response.strip()  # .strip() removes leading/trailing whitespace


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
# We create ONE OllamaClient instance here. Any module that does:
#     from brain.llm import llm_client
# gets this same instance. This is the Singleton pattern — one shared object
# instead of creating a new client (and new HTTP connection) on every call.

llm_client = OllamaClient()
