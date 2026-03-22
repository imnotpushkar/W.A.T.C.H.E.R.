"""
output/voice_output.py — Text-to-Speech Output
===============================================
Converts Watcher's text responses into spoken audio using edge-tts.

WHAT IS edge-tts?
    edge-tts is a Python library that calls Microsoft's neural TTS service —
    the same engine powering the Edge browser's Read Aloud feature and
    Windows 11 Narrator. It's free, requires no API key, and produces
    significantly better voice quality than any fully offline alternative.

    COST NOTE: edge-tts makes HTTPS requests to Microsoft's servers.
    It is free with no documented rate limits. The audio is generated
    remotely and streamed back as an MP3 file. If you're offline, it fails.
    pyttsx3 is kept as a commented fallback for offline use.

HOW ASYNC WORKS HERE:
    edge-tts is an async library — it uses Python's asyncio framework.
    asyncio lets a single thread handle multiple I/O operations concurrently
    by "awaiting" them — pausing the current task while waiting for network/disk,
    and resuming when the data arrives.

    Our speak() method is synchronous (regular def, not async def) because
    the rest of Watcher calls it like a normal function. Inside, we use
    asyncio.run() to create a temporary event loop, run the async TTS call
    to completion, then return. This bridges sync and async cleanly.

WHAT IS AN EVENT LOOP?
    asyncio.run() creates an "event loop" — a scheduler that manages async tasks.
    Think of it like a traffic controller: when task A is waiting for network data,
    the event loop runs task B instead of sitting idle. When A's data arrives,
    the loop resumes A. asyncio.run() runs until the given coroutine completes,
    then shuts the loop down.
"""

import asyncio
import tempfile
import os
import threading
from pathlib import Path
from core.config import TTS_ENGINE, TTS_VOICE, TTS_VOLUME
from core.logger import get_logger

log = get_logger(__name__)


class VoiceOutput:
    """
    Speaks text using the configured TTS engine.
    Runs audio playback in a background thread so it never blocks Watcher's UI.
    """

    def __init__(self):
        self.engine = TTS_ENGINE
        self.voice = TTS_VOICE
        self._current_thread: threading.Thread = None
        log.debug("VoiceOutput initialised — engine: %s, voice: %s", self.engine, self.voice)

    def speak(self, text: str, blocking: bool = False) -> None:
        """
        Speaks the given text aloud.

        Args:
            text: The text to speak.
            blocking: If True, waits for speech to finish before returning.
                      If False (default), speaks in background while Watcher continues.

        WHY NON-BLOCKING BY DEFAULT?
            If speak() blocked, pressing Ctrl+Space while Watcher is talking
            would freeze the entire program until speech finished.
            Non-blocking means speech plays in the background — Watcher stays responsive.
        """
        if not text or not text.strip():
            log.debug("speak() called with empty text — skipping")
            return

        # Clean the text — remove things that sound bad when spoken
        clean_text = self._clean_for_speech(text)
        log.info("Speaking (%d chars): %s...", len(clean_text), clean_text[:50])

        if blocking:
            self._speak_sync(clean_text)
        else:
            # daemon=True: thread dies automatically if main program exits
            self._current_thread = threading.Thread(
                target=self._speak_sync,
                args=(clean_text,),
                name="watcher-tts",
                daemon=True
            )
            self._current_thread.start()

    def _speak_sync(self, text: str) -> None:
        """
        Internal synchronous wrapper. Chooses engine and handles errors.
        This runs in a background thread when blocking=False.
        """
        try:
            if self.engine == "edge-tts":
                asyncio.run(self._speak_edge_tts(text))
            else:
                self._speak_pyttsx3(text)
        except Exception as e:
            log.error("TTS failed: %s", str(e))

    async def _speak_edge_tts(self, text: str) -> None:
        """
        Async method that generates speech via edge-tts and plays it.

        HOW edge-tts WORKS:
            1. We create an edge_tts.Communicate object with our text and voice name.
            2. We call .save() to generate the audio and write it to a temp MP3 file.
            3. We play that file using playsound3.
            4. We delete the temp file.

        WHY A TEMP FILE?
            edge-tts generates audio as a stream of MP3 data. We need to write it
            somewhere before playing it. tempfile.NamedTemporaryFile creates a file
            with a unique random name in the system temp folder — no naming conflicts,
            auto-cleaned up by the OS even if we crash.
        """
        import edge_tts

        # Create a temporary file to store the generated audio
        # delete=False because we need to close it before playsound can open it on Windows
        # (Windows locks files that are open — two processes can't open the same file)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Create TTS communicator with our text and voice
            communicate = edge_tts.Communicate(text=text, voice=self.voice)

            # Generate audio and save to temp file
            # This is the network call to Microsoft's servers
            await communicate.save(tmp_path)

            # Play the audio file
            self._play_audio(tmp_path)

        finally:
            # Always clean up the temp file, even if playback crashed
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _play_audio(self, file_path: str) -> None:
        """
        Plays an audio file using playsound3.
        playsound3 uses Windows' winmm API (built into every Windows installation)
        so no additional audio libraries are needed.
        """
        try:
            from playsound3 import playsound
            playsound(file_path)
        except Exception as e:
            log.warning("playsound3 failed: %s — trying fallback", str(e))
            # Fallback: use Windows built-in media player via subprocess
            import subprocess
            subprocess.Popen(
                ["powershell", "-c", f'(New-Object Media.SoundPlayer "{file_path}").PlaySync()'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

    def _speak_pyttsx3(self, text: str) -> None:
        """
        Offline TTS fallback using pyttsx3.
        Lower voice quality but works without internet.
        Only used if TTS_ENGINE=pyttsx3 in .env
        """
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("volume", TTS_VOLUME)
            engine.say(text)
            engine.runAndWait()
        except ImportError:
            log.error("pyttsx3 not installed. Install it: pip install pyttsx3")

    def _clean_for_speech(self, text: str) -> str:
        """
        Cleans text before sending to TTS.
        Removes things that sound terrible when spoken aloud.
        """
        import re
        # Remove URLs — "https colon slash slash www dot..." sounds awful
        text = re.sub(r'https?://\S+', 'link', text)
        # Remove markdown formatting characters
        text = re.sub(r'[*_`#]', '', text)
        # Remove Watcher's own error tags
        text = re.sub(r'\[Watcher:.*?\]', '', text)
        # Collapse multiple spaces/newlines
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def stop(self) -> None:
        """Stops any currently playing speech. No-op if nothing is playing."""
        # playsound3 doesn't support stop() — we'd need a more complex
        # audio library for that. For Phase 1 this is acceptable.
        # Will be improved in Phase 3 with proper audio management.
        log.debug("stop() called — note: mid-speech stopping not yet implemented")


# Module-level singleton
voice_output = VoiceOutput()
