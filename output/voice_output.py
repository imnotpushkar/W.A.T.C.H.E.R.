"""
output/voice_output.py — Text-to-Speech Output
===============================================
Two-engine TTS system:
    Primary:  kokoro-onnx (local, fast, ~300ms latency, bm_lewis voice)
    Fallback: edge-tts (Microsoft neural TTS, requires internet, ~1.5s latency)

WHY TWO ENGINES?
    kokoro-onnx runs entirely on your machine — no network, no dependency on
    Microsoft's servers, starts producing audio in ~300ms. It's the primary.
    edge-tts is the fallback for if kokoro fails to load or crashes.

HOW KOKORO-ONNX WORKS:
    kokoro is a neural TTS model (~80MB) that converts text → audio waveform
    entirely locally using ONNX Runtime. ONNX (Open Neural Network Exchange)
    is a standard format for ML models that runs on CPU or GPU without needing
    the original training framework (PyTorch, TensorFlow etc).

    Pipeline: text → phonemizer (text to phonemes) → kokoro model → audio array
    → soundfile writes to WAV → playsound3 plays it

    phonemizer converts text like "suggestion" into phonetic symbols like
    "səɡˈɛstʃən" — the actual sounds. The model works on sounds, not letters.
    espeak-ng (bundled via espeakng-loader) does this conversion.

HOW edge-tts FALLBACK WORKS:
    If kokoro fails (model files missing, import error, etc), we fall back to
    edge-tts which makes an HTTPS request to Microsoft's TTS service, downloads
    an MP3, and plays it. Higher latency but always available while online.

THREADING MODEL:
    All TTS runs in daemon background threads. speak() returns immediately —
    audio plays while Watcher continues doing other things.
    blocking=True is available for cases where we need to wait for speech.
"""

import asyncio
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from core.config import TTS_ENGINE, TTS_VOICE, TTS_VOLUME, PROJECT_ROOT
from core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# kokoro-onnx configuration
# ---------------------------------------------------------------------------
KOKORO_MODEL_PATH = PROJECT_ROOT / "data" / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_PATH = PROJECT_ROOT / "data" / "voices-v1.0.bin"
KOKORO_VOICE = "bm_lewis"       # British male, calm — closest to Jarvis
KOKORO_SPEED = 1.3              # Tested and confirmed — brisk but clear
KOKORO_LANG = "en-us"


class VoiceOutput:
    """
    Speaks text using kokoro-onnx (primary) or edge-tts (fallback).
    Automatically selects engine based on availability at startup.
    """

    def __init__(self):
        self._engine = None          # "kokoro" or "edge-tts"
        self._kokoro = None          # Loaded Kokoro instance (kept warm)
        self._current_thread: Optional[threading.Thread] = None
        self._setup_engine()

    def _setup_engine(self) -> None:
        """
        Tries to load kokoro-onnx. Falls back to edge-tts if it fails.
        Keeps the Kokoro instance warm (loaded in memory) so subsequent
        calls don't pay the model-load cost again.

        WHY KEEP THE MODEL WARM?
            Loading the ONNX model from disk takes ~500ms on first call.
            If we load it fresh on every speak() call, the first audio of
            each session is slow. Loading once at startup and reusing the
            instance means every subsequent call starts immediately.
        """
        if self._try_load_kokoro():
            self._engine = "kokoro"
            log.info("TTS engine: kokoro-onnx (local, voice: %s, speed: %.1f)",
                     KOKORO_VOICE, KOKORO_SPEED)
        else:
            self._engine = "edge-tts"
            log.info("TTS engine: edge-tts (fallback, voice: %s)", TTS_VOICE)

    def _try_load_kokoro(self) -> bool:
        """
        Attempts to load the kokoro-onnx model.
        Returns True if successful, False if anything fails.
        All failures are caught — this should never crash Watcher startup.
        """
        try:
            # Check model files exist before trying to import
            if not KOKORO_MODEL_PATH.exists():
                log.warning(
                    "kokoro model not found at %s\n"
                    "Download it: see README for instructions",
                    KOKORO_MODEL_PATH
                )
                return False

            if not KOKORO_VOICES_PATH.exists():
                log.warning("kokoro voices file not found at %s", KOKORO_VOICES_PATH)
                return False

            from kokoro_onnx import Kokoro
            self._kokoro = Kokoro(
                str(KOKORO_MODEL_PATH),
                str(KOKORO_VOICES_PATH)
            )

            # Warm-up call — generates a tiny silent phrase to pre-load
            # the ONNX inference session. First real call will be faster.
            log.debug("Warming up kokoro model...")
            self._kokoro.create(".", voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang=KOKORO_LANG)
            log.debug("kokoro warm-up complete")
            return True

        except ImportError:
            log.warning("kokoro-onnx not installed — falling back to edge-tts")
            return False
        except Exception as e:
            log.warning("kokoro failed to load: %s — falling back to edge-tts", str(e))
            return False

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def speak(self, text: str, blocking: bool = False) -> None:
        """
        Speaks the given text using the active TTS engine.

        Args:
            text: Text to speak.
            blocking: If True, waits for speech to finish before returning.
                      Default False — speech plays in background.
        """
        if not text or not text.strip():
            return

        clean = self._clean_for_speech(text)
        log.info("Speaking: %s...", clean[:60])

        if blocking:
            self._speak_sync(clean)
        else:
            self._current_thread = threading.Thread(
                target=self._speak_sync,
                args=(clean,),
                name="watcher-tts",
                daemon=True
            )
            self._current_thread.start()

    # -----------------------------------------------------------------------
    # Internal dispatch
    # -----------------------------------------------------------------------

    def _speak_sync(self, text: str) -> None:
        """Dispatches to active engine. Runs in background thread."""
        try:
            if self._engine == "kokoro":
                self._speak_kokoro(text)
            else:
                asyncio.run(self._speak_edge_tts(text))
        except Exception as e:
            log.error("TTS error: %s", str(e))

    def _speak_kokoro(self, text: str) -> None:
        """
        Generates audio with kokoro-onnx and plays it.

        kokoro.create() returns:
            audio: numpy array of float32 audio samples
            rate:  sample rate in Hz (typically 24000)

        We write to a temp WAV file then play it.
        WAV (not MP3) because kokoro produces raw PCM — no encoding needed.
        soundfile writes it, playsound3 plays it.
        """
        import soundfile as sf

        # create() is the main kokoro call — text → audio array
        audio, rate = self._kokoro.create(
            text,
            voice=KOKORO_VOICE,
            speed=KOKORO_SPEED,
            lang=KOKORO_LANG
        )

        # Write to temp WAV file
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
        """
        Fallback: generates speech via edge-tts (Microsoft neural TTS).
        Requires internet. ~1.5s latency before audio starts.
        """
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
        """
        Plays an audio file using playsound3.
        Falls back to PowerShell's built-in media player if playsound3 fails.
        """
        try:
            from playsound3 import playsound
            playsound(file_path)
        except Exception as e:
            log.warning("playsound3 failed (%s) — trying subprocess fallback", str(e))
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
        """
        Cleans text before TTS — removes things that sound bad when spoken.
        """
        import re
        text = re.sub(r'https?://\S+', 'link', text)   # URLs → "link"
        text = re.sub(r'[*_`#\[\]]', '', text)          # Markdown symbols
        text = re.sub(r'\[Watcher:.*?\]', '', text)     # Error tags
        text = re.sub(r'💡', '', text)                  # Emoji
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def stop(self) -> None:
        """No-op for now — mid-speech stopping requires more complex audio management."""
        pass


# Module-level singleton — model loads once at import time
voice_output = VoiceOutput()
