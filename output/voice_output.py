"""
output/voice_output.py — Text-to-Speech Output
===============================================
Two-engine TTS with streaming sentence-by-sentence playback.

NEW: speak_stream()
    Accepts a token generator (from Ollama streaming).
    Buffers tokens until a sentence boundary is detected.
    Fires each complete sentence to kokoro immediately.
    Result: Watcher starts speaking within ~1s of first sentence completing,
    while Ollama is still generating the rest.

SENTENCE BOUNDARY DETECTION:
    We watch the token stream for '.', '?', '!' followed by a space or end.
    When detected, the buffered text up to that point is a complete sentence.
    We flush it to kokoro and start a new buffer for the next sentence.

    Edge cases handled:
    - "Mr." / "Dr." / "vs." — abbreviations shouldn't trigger a flush
    - Numbers like "1.3" — decimal points aren't sentence ends
    - Ellipsis "..." — not a sentence end
"""

import asyncio
import os
import queue
import re
import tempfile
import threading
from pathlib import Path
from typing import Generator, Optional

from core.config import TTS_VOICE, PROJECT_ROOT, KOKORO_VOICE as _KOKORO_VOICE, KOKORO_SPEED as _KOKORO_SPEED
from core.logger import get_logger

log = get_logger(__name__)

KOKORO_MODEL_PATH = PROJECT_ROOT / "data" / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_PATH = PROJECT_ROOT / "data" / "voices-v1.0.bin"
KOKORO_VOICE = _KOKORO_VOICE
KOKORO_SPEED = _KOKORO_SPEED
KOKORO_LANG = "en-us"

# Abbreviations that contain dots but are NOT sentence endings
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs",
    "etc", "e.g", "i.e", "approx", "dept", "est"
}


class VoiceOutput:

    def __init__(self):
        self._engine = None
        self._kokoro = None
        self._tts_queue: queue.Queue = queue.Queue()
        self._player_thread: Optional[threading.Thread] = None
        self._setup_engine()
        self._start_player_thread()

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    def _setup_engine(self) -> None:
        if self._try_load_kokoro():
            self._engine = "kokoro"
            log.info("TTS engine: kokoro-onnx (voice: %s, speed: %.1f)",
                     KOKORO_VOICE, KOKORO_SPEED)
        else:
            self._engine = "edge-tts"
            log.info("TTS engine: edge-tts fallback (voice: %s)", TTS_VOICE)

    def _try_load_kokoro(self) -> bool:
        try:
            if not KOKORO_MODEL_PATH.exists():
                log.warning("kokoro model not found at %s", KOKORO_MODEL_PATH)
                return False
            if not KOKORO_VOICES_PATH.exists():
                log.warning("kokoro voices not found at %s", KOKORO_VOICES_PATH)
                return False

            from kokoro_onnx import Kokoro
            self._kokoro = Kokoro(
                str(KOKORO_MODEL_PATH),
                str(KOKORO_VOICES_PATH)
            )
            log.debug("Warming up kokoro...")
            self._kokoro.create(".", voice=KOKORO_VOICE,
                                speed=KOKORO_SPEED, lang=KOKORO_LANG)
            log.debug("kokoro ready")
            return True
        except ImportError:
            log.warning("kokoro-onnx not installed — using edge-tts")
            return False
        except Exception as e:
            log.warning("kokoro load failed: %s — using edge-tts", str(e))
            return False

    def _start_player_thread(self) -> None:
        """
        Starts a persistent background thread that reads from _tts_queue
        and plays audio serially.

        WHY A PERSISTENT PLAYER THREAD WITH A QUEUE?
            Without a queue, if two speak() calls happen quickly (ack line +
            first streamed sentence), they'd spawn two threads that both call
            playsound3 simultaneously — producing overlapping audio.

            A queue serialises playback: items go in one end, the player
            thread pulls them out one at a time and plays them in order.
            This guarantees audio plays sequentially, never overlapping.

        WHAT IS A QUEUE?
            queue.Queue is a thread-safe FIFO (first in, first out) data
            structure. .put(item) adds to the end. .get() blocks until an
            item is available, then returns it. Multiple threads can safely
            put/get simultaneously — Queue handles the locking internally.

        SENTINEL VALUE:
            To stop the player thread cleanly, we put None in the queue.
            The thread checks for None and exits its loop when it sees it.
            This is called a "sentinel value" — a special signal value.
        """
        self._player_thread = threading.Thread(
            target=self._player_loop,
            name="watcher-tts-player",
            daemon=True
        )
        self._player_thread.start()
        log.debug("TTS player thread started")

    def _player_loop(self) -> None:
        """
        Runs forever in background thread.
        Pulls text items from queue and speaks them one at a time.
        """
        while True:
            item = self._tts_queue.get()  # Blocks until item available
            if item is None:              # Sentinel — shut down
                break
            try:
                self._speak_sync(item)
            except Exception as e:
                log.error("Player loop error: %s", str(e))
            finally:
                self._tts_queue.task_done()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def speak(self, text: str, blocking: bool = False) -> None:
        """
        Speaks text. Queued for serial playback — never overlaps other audio.

        Args:
            text: Text to speak.
            blocking: If True, waits for this item to finish playing.
        """
        if not text or not text.strip():
            return
        clean = self._clean_for_speech(text)
        if not clean:
            return

        if blocking:
            # For blocking, bypass queue and speak directly
            self._speak_sync(clean)
        else:
            self._tts_queue.put(clean)

    def speak_stream(self, token_generator: Generator[str, None, None]) -> None:
        """
        Accepts a streaming token generator from Ollama.
        Buffers tokens into sentences and queues each sentence for playback
        the moment it's complete — no waiting for full response.

        This is what makes Watcher sound like Jarvis: he starts speaking
        as soon as the first sentence is ready, while still generating more.

        HOW SENTENCE DETECTION WORKS:
            We accumulate tokens into a buffer string.
            After each token, we check if the buffer ends with a sentence
            boundary (. ? !) that is NOT an abbreviation or decimal.
            When detected, we flush the buffer to the TTS queue and reset.
            Any remaining buffer after the generator exhausts is also flushed.

        Args:
            token_generator: Generator yielding string tokens from Ollama.
        """
        buffer = ""

        for token in token_generator:
            buffer += token
            # Check if we have a complete sentence to flush
            if self._is_sentence_end(buffer):
                sentence = buffer.strip()
                if sentence:
                    log.debug("Streaming sentence: %s", sentence[:50])
                    self._tts_queue.put(sentence)
                buffer = ""

        # Flush any remaining text that didn't end with punctuation
        remainder = buffer.strip()
        if remainder:
            log.debug("Flushing remainder: %s", remainder[:50])
            self._tts_queue.put(remainder)

    def clear_queue(self) -> None:
        """
        Clears all pending TTS items from the queue.
        Called when a new trigger fires before current speech finishes.
        """
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
                self._tts_queue.task_done()
            except queue.Empty:
                break
        log.debug("TTS queue cleared")

    # -----------------------------------------------------------------------
    # Sentence boundary detection
    # -----------------------------------------------------------------------

    def _is_sentence_end(self, text: str) -> bool:
        """
        Returns True if the text buffer ends with a complete sentence.

        Rules:
        - Must end with . ? or !
        - The word before the dot must not be an abbreviation
        - Must not be a decimal number (1.3, 99.9)
        - Must not be ellipsis (...)
        - Should have enough content to be worth speaking (> 3 chars)
        """
        text = text.rstrip()
        if len(text) < 4:
            return False

        if not text[-1] in '.?!':
            return False

        # Ellipsis check
        if text.endswith('...'):
            return False

        # Decimal number check — digit before dot
        if text[-1] == '.' and len(text) >= 2 and text[-2].isdigit():
            return False

        # Abbreviation check — get the last word before the dot
        if text[-1] == '.':
            words = text[:-1].split()
            if words:
                last_word = words[-1].lower().rstrip('.')
                if last_word in _ABBREVIATIONS:
                    return False

        return True

    # -----------------------------------------------------------------------
    # Internal TTS dispatch
    # -----------------------------------------------------------------------

    def _speak_sync(self, text: str) -> None:
        """Dispatches to active engine synchronously."""
        try:
            if self._engine == "kokoro":
                self._speak_kokoro(text)
            else:
                asyncio.run(self._speak_edge_tts(text))
        except Exception as e:
            log.error("TTS speak error: %s", str(e))

    def _speak_kokoro(self, text: str) -> None:
        """Generates and plays audio via kokoro-onnx."""
        import soundfile as sf

        audio, rate = self._kokoro.create(
            text,
            voice=KOKORO_VOICE,
            speed=KOKORO_SPEED,
            lang=KOKORO_LANG
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            sf.write(tmp_path, audio, rate)
            self._play_audio(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    async def _speak_edge_tts(self, text: str) -> None:
        """Fallback: generates and plays audio via edge-tts."""
        import edge_tts
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            communicate = edge_tts.Communicate(text=text, voice=TTS_VOICE)
            await communicate.save(tmp_path)
            self._play_audio(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _play_audio(self, file_path: str) -> None:
        """Plays audio file via playsound3 with subprocess fallback."""
        try:
            from playsound3 import playsound
            playsound(file_path)
        except Exception as e:
            log.warning("playsound3 failed (%s) — subprocess fallback", str(e))
            import subprocess
            subprocess.run(
                ["powershell", "-c",
                 f'Add-Type -AssemblyName presentationCore; '
                 f'$mp = New-Object System.Windows.Media.MediaPlayer; '
                 f'$mp.Open("{file_path}"); $mp.Play(); Start-Sleep 3'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

    def _clean_for_speech(self, text: str) -> str:
        """Strips markdown, URLs, emoji and error tags before TTS."""
        text = re.sub(r'https?://\S+', 'link', text)
        text = re.sub(r'[*_`#\[\]]', '', text)
        text = re.sub(r'\[Watcher:.*?\]', '', text)
        text = re.sub(r'💡', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def stop(self) -> None:
        self.clear_queue()


voice_output = VoiceOutput()
