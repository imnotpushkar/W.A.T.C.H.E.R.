# WATCHER — Continuation Document
**Generated:** 2026-03-23
**Session summary:** Built and stabilised the complete Phase 1 core loop — hotkey trigger, screen reader, Ollama LLM integration, streaming overlay, Tab-to-type, kokoro-onnx TTS with voice templates.

---

## Project Overview (brief)

Watcher is a personal, system-wide AI assistant for Windows. It reads the screen across any app, remembers conversations per contact, understands cross-app context, and suggests or speaks responses when invoked. Named after Uatu the Watcher (Marvel Universe). Built for personal use with professional code standards, designed to be portable.

Wake word: "Hey Watcher" (Phase 2)
AI Brain: Ollama + Llama 3.1 8B (local, free)
Primary language: Python 3.14
Target OS: Windows 11
Project path: `C:\Users\Pushkar Gupta\OneDrive\Desktop\VSCODE\PROJECTS\WATCHER`

Full details in MASTER.md.

---

## Current Build Phase

**Active phase:** Phase 1 — COMPLETE (with known TODOs)
**Phase goal:** Working end-to-end loop: hotkey → screen read → LLM → overlay → voice
**Phase status:** ~90% complete. Core loop works. Overlay UI redesign and inline text deferred to Phase 3.

---

## What Was Built This Session

- `.env.example` — All environment variables documented with defaults
- `core/config.py` — Loads .env, exposes typed constants, ensures data dirs exist. Now includes KOKORO_VOICE and KOKORO_SPEED.
- `core/logger.py` — Dual-handler logging (console + file). Silences comtypes, httpx, urllib3, asyncio noise.
- `core/orchestrator.py` — Central pipeline: ack template → screen read → suggestion stream → ready template. Clean single-stream, no parallel complexity.
- `input/keyboard_hook.py` — Global hotkey via pynput low-level Listener (NOT GlobalHotKeys — avoids Qt message pump conflict). Key state tracking for combo detection. 1.5s debounce.
- `input/screen_reader.py` — Windows UI Automation via uiautomation. Returns structured dict with app_name, window_title, text_content, focused_text. Fallback to ctypes Win32.
- `brain/llm.py` — OllamaClient wrapping /api/generate. Two system prompts: SUGGESTION_SYSTEM_PROMPT (clean typed output) and WATCHER_SPOKEN_PROMPT (Jarvis voice — not used in final pipeline, kept for reference). generate_stream() yields tokens. generate() returns full string. mode param selects prompt.
- `brain/voice_templates.py` — NEW. Pre-written template bank for all spoken lines. Stages: ack, ready, accepted, dismissed, error. App-specific lines for WhatsApp, Gmail, Discord, Chrome, VS Code, Notepad, Word etc. random.choice() for variety. Zero LLM involvement.
- `output/voice_output.py` — kokoro-onnx primary TTS (bm_lewis, speed from config). edge-tts fallback. Queue-based serial player thread prevents audio overlap. speak_stream() for sentence-by-sentence streaming (built but not used in current pipeline — kept for Phase 3).
- `output/overlay.py` — PySide6 transparent always-on-top frameless window. Signal/slot for thread safety. _is_alive guard prevents shutdown race condition. shutdown() method called before app.quit().
- `output/inline_suggest.py` — Suggestion lifecycle: show_streaming() → finalize_stream() → Tab accept / Esc dismiss. Speaks accepted/dismissed templates via voice_output. Stores _raw_text (typed) separately from _display_text (shown).
- `output/typist.py` — pyautogui typing. Backspace before typing to delete Tab character. Clipboard fallback for non-ASCII.
- `main.py` — Strict init order: logging → Ollama health → QApplication → components → wire dependencies → hotkey → QTimer Ctrl+C polling → app.exec().
- `requirements.txt` — All Phase 1 deps. PyQt5 replaced with PySide6 (Python 3.12+ compat). pygame replaced with playsound3. kokoro-onnx and soundfile added.

---

## Current File States

| File | Status | Notes |
|---|---|---|
| `main.py` | ✅ Working | |
| `.env.example` | ✅ Working | |
| `requirements.txt` | ✅ Working | |
| `core/config.py` | ✅ Working | Includes KOKORO_VOICE, KOKORO_SPEED |
| `core/logger.py` | ✅ Working | comtypes noise silenced |
| `core/orchestrator.py` | ✅ Working | Clean template-based pipeline |
| `input/keyboard_hook.py` | ✅ Working | Low-level listener, Qt-safe |
| `input/screen_reader.py` | ✅ Working | uiautomation + ctypes fallback |
| `brain/llm.py` | ✅ Working | suggestion + spoken modes |
| `brain/voice_templates.py` | ✅ Working | Full template bank, all stages |
| `output/voice_output.py` | ✅ Working | kokoro primary, edge-tts fallback |
| `output/overlay.py` | ✅ Working | Shutdown race condition fixed |
| `output/inline_suggest.py` | ✅ Working | Raw/display text separated |
| `output/typist.py` | ✅ Working | Backspace fix for Tab character |
| `input/voice_listener.py` | 🔲 Stub | Phase 2 |
| `brain/context_builder.py` | 🔲 Stub | Phase 2 |
| `brain/tone_engine.py` | 🔲 Stub | Phase 3 |
| `memory/db.py` | 🔲 Stub | Phase 2 |
| `memory/vector_store.py` | 🔲 Stub | Phase 2 |
| `memory/contact_profiles.py` | 🔲 Stub | Phase 2 |
| `memory/app_registry.py` | 🔲 Stub | Phase 2 |
| `output/typist.py` | ✅ Working | |
| `ui/tray.py` | 🔲 Stub | Phase 3 |
| `ui/settings_panel.py` | 🔲 Stub | Phase 3 |

---

## What is Currently Working

- Pressing Ctrl+Space activates Watcher from any app
- Screen text read successfully via Windows UI Automation
- Ollama (llama3.1:8b) receives context and streams response tokens
- Overlay appears with streaming suggestion text
- [Tab] accepts suggestion and types it into the active app
- [Esc] dismisses overlay
- kokoro-onnx speaks instantly via bm_lewis voice at speed 1.4
- Voice templates fire at correct pipeline stages: ack → ready → accepted/dismissed
- Ctrl+C shuts down cleanly via QTimer polling
- comtypes debug noise suppressed in logs

---

## What is NOT Working / In Progress

- Overlay UI is functional but visually rough — redesign deferred to Phase 3
- Suggestion quality limited by Llama 3.1 8B — occasionally misspells words, gives generic responses
- Tab sometimes leaves a leading character in certain apps (partially fixed with backspace)
- No memory — every trigger is a blank slate, Watcher knows nothing about the user
- No voice input — can't speak to Watcher yet (Phase 2)
- No wake word (Phase 2)
- Inline text (ghost text in input field) not yet built — deferred to Phase 3

---

## Active Bugs

- **BUG-001:** Tab-to-type occasionally leaves a leading space in some apps despite backspace fix. Affects apps with non-standard input handling. Low priority.
- **BUG-002:** Suggestion quality inconsistent — Llama 3.1 8B sometimes produces generic responses instead of context-specific ones. Prompt engineering partially mitigates. Will improve with memory layer in Phase 2.

---

## Decisions Made This Session

- **D012 — playsound3 over pygame:** pygame requires C compilation on Python 3.12+. playsound3 uses Windows winmm via ctypes. No build tools needed.
- **D013 — PySide6 over PyQt5:** PyQt5 has no Python 3.12+ wheels. PySide6 is the official Qt6 binding with full Python 3.14 support. API is ~95% identical.
- **D014 — kokoro-onnx over edge-tts as primary TTS:** Local neural TTS, ~300ms latency, no network dependency. bm_lewis voice at speed 1.4 confirmed by user testing. edge-tts retained as automatic fallback.
- **D015 — Pre-written voice templates over LLM voice generation:** LLM-generated voice had two problems: 2-3s latency (overlay always appeared first) and small model leaked prompt instructions as output. Templates fire in microseconds, always sound correct, sufficient variety via random.choice().
- **D016 — Low-level pynput Listener over GlobalHotKeys:** GlobalHotKeys conflicts with Qt's Windows message pump — both hook into the same OS mechanism. Low-level Listener runs its own message pump in a separate thread. Manual key state tracking detects combos reliably.
- **D017 — Inline text deferred to Phase 3:** Floating overlay is Phase 1 MVP. True ghost-text inline suggestions (GitHub Copilot style) require deeper input field integration. Planned for Phase 3.
- **D018 — KOKORO_VOICE and KOKORO_SPEED in .env:** Speed 1.4, voice bm_lewis confirmed by user testing multiple options (1.15, 1.25, 1.35, 1.5, finally 1.3 then 1.4). Configurable without code changes.

---

## Exact Next Steps (in order)

1. **Start Phase 2 — Memory layer first (before voice input)**
   - `memory/db.py` — SQLite wrapper, schema: conversations table (id, app, contact, message, role, timestamp), sessions table
   - `memory/vector_store.py` — ChromaDB local vector store, semantic search over past conversations
   - `memory/contact_profiles.py` — per-person tone, relationship context, message history

2. **Wire memory into orchestrator**
   - `brain/context_builder.py` — assembles screen content + relevant memory into enriched prompt
   - Orchestrator calls context_builder instead of building prompt directly

3. **Voice input**
   - `input/voice_listener.py` — faster-whisper for STT, vosk for wake word detection
   - Wire into orchestrator as alternative trigger path

4. **Phase 3 prep — Overlay UI redesign**
   - Frosted glass effect, animated token streaming, better typography
   - Eventually replace with inline ghost text

---

## Important Notes for Next Session

- **Python version: 3.14.** Any package that requires compilation or has Python version caps will fail. Always check for pre-built wheels before installing. PySide6, kokoro-onnx, and all current deps are confirmed working.
- **Ollama must be running before main.py starts.** It starts automatically when you open a terminal if PATH is set correctly. Verify: `ollama --version`. Model: `llama3.1:8b`.
- **Virtual environment:** Always activate before running. VS Code auto-activates it. Manual: `venv\Scripts\activate`
- **kokoro model files** are in `data/` but gitignored. They are NOT in the repo. If setting up on a new machine, download them:
  ```powershell
  Invoke-WebRequest -Uri "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx" -OutFile "data/kokoro-v1.0.int8.onnx"
  Invoke-WebRequest -Uri "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin" -OutFile "data/voices-v1.0.bin"
  ```
- **espeak-ng is bundled** via espeakng-loader package — no manual .msi install needed on Python 3.14.
- **comtypes generates cache** in venv on first run — normal, not an error.
- **The `data/` folder** contains watcher.log, kokoro model files, and eventually watcher.db and chroma/. All gitignored except .gitkeep.
- **ChromaDB not yet installed** — commented out in requirements.txt. Install when starting Phase 2: `pip install chromadb`
- **Tab-to-type flow:** pynput Listener with suppress=False. Tab reaches active app AND our handler. We send backspace first to delete the tab character, then type the suggestion.
- **Overlay timing:** ack template fires instantly on trigger. Suggestion streams to overlay (2-4s Ollama). ready template fires after overlay complete. accepted/dismissed fire on user action.

---

## Full File Contents

### `core/config.py`
```python
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

def _require(key: str) -> str:
    value = os.getenv(key)
    if value is None:
        raise EnvironmentError(f"[Watcher] Required config key '{key}' is missing from .env")
    return value

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_MAX_TOKENS: int = int(os.getenv("OLLAMA_MAX_TOKENS", "512"))
OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.3"))
WATCHER_HOTKEY: str = os.getenv("WATCHER_HOTKEY", "ctrl+space")
OVERLAY_OPACITY: float = float(os.getenv("OVERLAY_OPACITY", "0.92"))
OVERLAY_TIMEOUT_MS: int = int(os.getenv("OVERLAY_TIMEOUT_MS", "8000"))
TTS_ENGINE: str = os.getenv("TTS_ENGINE", "edge-tts")
TTS_VOICE: str = os.getenv("TTS_VOICE", "en-IN-NeerjaNeural")
TTS_VOLUME: float = float(os.getenv("TTS_VOLUME", "0.9"))
SCREEN_READ_TIMEOUT: int = int(os.getenv("SCREEN_READ_TIMEOUT", "3"))
SCREEN_READ_MAX_CHARS: int = int(os.getenv("SCREEN_READ_MAX_CHARS", "4000"))
TYPIST_CHAR_DELAY: float = float(os.getenv("TYPIST_CHAR_DELAY", "0.02"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG")
LOG_FILE: Path = PROJECT_ROOT / os.getenv("LOG_FILE", "data/watcher.log")
DB_PATH: Path = PROJECT_ROOT / os.getenv("DB_PATH", "data/watcher.db")
CHROMA_PATH: Path = PROJECT_ROOT / os.getenv("CHROMA_PATH", "data/chroma")
KOKORO_VOICE: str = os.getenv("KOKORO_VOICE", "bm_lewis")
KOKORO_SPEED: float = float(os.getenv("KOKORO_SPEED", "1.4"))

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
CHROMA_PATH.mkdir(parents=True, exist_ok=True)
```

### `brain/voice_templates.py`
See full file in source — template bank with ack/ready/accepted/dismissed/error stages, app-specific lines, random.choice() selection.

### `core/orchestrator.py`
Pipeline: ack() → screen read → suggestion stream → finalize_stream() → ready(). Uses voice_templates throughout. No LLM for voice.

### `output/voice_output.py`
kokoro-onnx primary (bm_lewis, speed from config). Queue-based serial player thread. edge-tts async fallback. speak_stream() built for future use.

### `output/overlay.py`
PySide6 transparent overlay. _is_alive guard. shutdown() method. Signal/slot for thread safety.

### `output/inline_suggest.py`
_raw_text / _display_text separation. finalize_stream() adds instructions. Speaks accepted/dismissed templates.

### `main.py`
Strict init order. QTimer Ctrl+C polling. overlay.shutdown() before app.quit(). voice_output passed to inline_suggestion.

---

*End of continuation document. Upload this file to the Watcher Claude Project folder before starting a new chat.*
