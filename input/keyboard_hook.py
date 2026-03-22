"""
input/keyboard_hook.py — Global Hotkey Detection
=================================================
Listens for the trigger hotkey (default: Ctrl+Space) system-wide.
Works regardless of which app is currently in focus.

WHAT IS A KEYBOARD HOOK?
    Normally, keyboard events go: physical key → OS → active app.
    A "hook" intercepts this stream at the OS level — before the active app
    sees it. This is how global hotkeys work: your app isn't in focus,
    but it still receives the keypress.

    On Windows, this uses the SetWindowsHookEx Win32 API under the hood.
    pynput wraps this so we don't have to write C code to access it.

WHAT IS A THREAD?
    Your program normally runs as a single sequence of instructions — one thread.
    A thread is a separate sequence of instructions running in parallel.
    We run the keyboard listener in a BACKGROUND THREAD so it doesn't block
    the main thread (which will be running the UI and overlay).

    Think of it like having two employees: one watching for keypresses,
    one handling the UI. They work simultaneously.

WHAT IS A CALLBACK?
    A callback is a function you pass to another function to be called later.
    We give the keyboard listener a callback function. When a key is pressed,
    the listener calls our function. We don't call it — the library does,
    when the event happens. This is called "event-driven programming".

DEBOUNCING:
    If the user holds Ctrl+Space for half a second, we'd trigger 10+ times.
    Debouncing means: after a trigger, ignore all further triggers for X seconds.
    We use time.time() to track when we last triggered and skip if too soon.
"""

import time
import threading
from typing import Callable, Optional
from pynput import keyboard
from core.config import WATCHER_HOTKEY
from core.logger import get_logger

log = get_logger(__name__)

# How many seconds to wait before allowing another trigger.
# Prevents accidental double-triggers when holding the hotkey.
DEBOUNCE_SECONDS = 1.5


class HotkeyListener:
    """
    Listens for the configured hotkey globally and calls a callback when triggered.

    The listener runs in a daemon background thread — it starts when Watcher
    starts and stops automatically when the main program exits.
    """

    def __init__(self, on_trigger: Callable[[], None]):
        """
        Args:
            on_trigger: A function with no arguments to call when the hotkey
                        is pressed. This is your callback — the orchestrator
                        will pass in its handle_trigger() method here.
        """
        self.on_trigger = on_trigger
        self._last_trigger_time: float = 0.0
        self._listener: Optional[keyboard.GlobalHotKeys] = None
        self._thread: Optional[threading.Thread] = None

        # Parse the hotkey string from config into the format pynput expects.
        # Config has: "ctrl+space"
        # pynput wants: "<ctrl>+<space>" for special keys
        self._hotkey_string = self._parse_hotkey(WATCHER_HOTKEY)
        log.debug("HotkeyListener configured for: %s", self._hotkey_string)

    def _parse_hotkey(self, hotkey_str: str) -> str:
        """
        Converts our config format to pynput's GlobalHotKeys format.

        pynput's GlobalHotKeys expects:
            - Special keys wrapped in angle brackets: <ctrl>, <space>, <shift>
            - Regular keys as-is: a, b, w
            - Combined with +: <ctrl>+<space>

        Our config format: "ctrl+space", "ctrl+shift+w"

        Examples:
            "ctrl+space" → "<ctrl>+<space>"
            "ctrl+shift+w" → "<ctrl>+<shift>+w"
            "ctrl+shift+f12" → "<ctrl>+<shift>+<f12>"
        """
        # Keys that need angle brackets in pynput
        special_keys = {
            "ctrl", "shift", "alt", "space", "tab", "enter", "esc",
            "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8",
            "f9", "f10", "f11", "f12", "up", "down", "left", "right",
            "home", "end", "pageup", "pagedown", "insert", "delete"
        }
        parts = hotkey_str.lower().split("+")
        formatted = []
        for part in parts:
            part = part.strip()
            if part in special_keys:
                formatted.append(f"<{part}>")
            else:
                formatted.append(part)  # Regular letter keys stay as-is
        return "+".join(formatted)

    def _on_hotkey_pressed(self):
        """
        Called by pynput when the hotkey is detected.
        Applies debounce logic before calling the real callback.
        """
        now = time.time()
        # time.time() returns seconds since Unix epoch as a float.
        # Subtracting two time.time() values gives elapsed seconds.
        elapsed = now - self._last_trigger_time

        if elapsed < DEBOUNCE_SECONDS:
            log.debug(
                "Hotkey ignored — debounce active (%.2fs since last trigger)",
                elapsed
            )
            return

        self._last_trigger_time = now
        log.info("Hotkey triggered: %s", WATCHER_HOTKEY)

        # Call the callback in a NEW thread so we don't block the keyboard listener.
        # If on_trigger() takes 3 seconds (waiting for Ollama), the keyboard listener
        # would be frozen for those 3 seconds without this. With a new thread,
        # the listener stays responsive while the trigger is being handled.
        trigger_thread = threading.Thread(
            target=self.on_trigger,
            name="watcher-trigger",
            daemon=True     # Daemon threads die automatically when main program exits
        )
        trigger_thread.start()

    def start(self):
        """
        Starts the hotkey listener in a background thread.
        Returns immediately — the listener runs in the background.
        """
        # GlobalHotKeys takes a dict: {hotkey_string: callback_function}
        hotkey_map = {self._hotkey_string: self._on_hotkey_pressed}

        self._listener = keyboard.GlobalHotKeys(hotkey_map)

        # Create a daemon thread running the listener's blocking loop.
        # daemon=True means: when main() exits, this thread is killed automatically.
        # Without daemon=True, the program would hang waiting for this thread to finish.
        self._thread = threading.Thread(
            target=self._listener.run,
            name="watcher-hotkey-listener",
            daemon=True
        )
        self._thread.start()
        log.info("Hotkey listener started — press %s to trigger Watcher", WATCHER_HOTKEY)

    def stop(self):
        """
        Stops the hotkey listener cleanly.
        Called when Watcher is shutting down.
        """
        if self._listener:
            self._listener.stop()
            log.info("Hotkey listener stopped")
