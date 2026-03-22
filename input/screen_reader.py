"""
input/screen_reader.py — Windows Screen Text Reader
=====================================================
Reads visible text from whichever app is currently in focus,
using Windows Accessibility APIs (UI Automation / uiautomation).

WHAT ARE WINDOWS ACCESSIBILITY APIS?
    Windows has a built-in framework called UI Automation (UIA).
    Every app that follows Windows guidelines exposes a "tree" of UI elements —
    buttons, text boxes, labels, list items — each with metadata like:
    - What type of control it is (TextBlock, Edit, Button, ListItem...)
    - What text it contains
    - Its position on screen
    - Whether it's enabled, focused, visible

    Screen readers (like NVDA for blind users) use this exact same API.
    uiautomation is a Python library that gives us access to this tree.

    Think of it like the DOM in a web browser: just as you can call
    document.querySelectorAll('p') to find all paragraph elements,
    uiautomation lets you query any Windows app's UI element tree.

WHY NOT JUST TAKE A SCREENSHOT AND OCR IT?
    1. Speed: accessibility tree reads are near-instant. OCR takes 300-800ms.
    2. Accuracy: you get the actual text. OCR makes errors on unusual fonts.
    3. Structure: you know WHAT the text is (a chat bubble, an email body, a title).
       OCR gives you a flat blob of characters with no semantic meaning.
    4. No GPU needed: accessibility reads are pure CPU, low overhead.

    OCR is kept as a fallback for apps that block accessibility APIs (rare).

WHAT IS THE ACTIVE WINDOW?
    At any moment, one window has "focus" — keyboard input goes to it.
    GetForegroundWindow() (Windows API) returns a handle to this window.
    uiautomation can find the corresponding UI automation element from this handle,
    giving us a root node to walk the accessibility tree from.
"""

import time
from typing import Optional
from core.config import SCREEN_READ_TIMEOUT, SCREEN_READ_MAX_CHARS
from core.logger import get_logger

log = get_logger(__name__)

# We import uiautomation inside functions rather than at module level.
# Reason: uiautomation is Windows-only. If someone imports this module on
# a non-Windows machine (e.g., running tests on Linux), the module-level
# import would crash immediately. Lazy imports give a better error message.


class ScreenReader:
    """
    Reads text from the currently active Windows application.

    Tries uiautomation (accessibility API) first.
    Falls back to a basic window title read if uiautomation fails.
    (Full OCR fallback will be added in a later phase.)
    """

    def __init__(self):
        self._uia_available = self._check_uia()

    def _check_uia(self) -> bool:
        """
        Tests whether uiautomation can be imported and is functional.
        Logs a clear message if it's missing so the user knows what to install.
        """
        try:
            import uiautomation  # noqa: F401 — imported just to test availability
            log.debug("uiautomation is available")
            return True
        except ImportError:
            log.warning(
                "uiautomation not installed. Screen reading degraded.\n"
                "Fix: pip install uiautomation"
            )
            return False

    # -----------------------------------------------------------------------
    # Main public method
    # -----------------------------------------------------------------------

    def read_active_window(self) -> dict:
        """
        Reads the currently focused window and returns structured text data.

        Returns a dict with:
            {
                "app_name": str,        # e.g. "WhatsApp", "Google Chrome"
                "window_title": str,    # Full title bar text
                "text_content": str,    # All readable text in the window
                "focused_text": str,    # Text in the currently focused input field
                "source": str,          # "uia" or "fallback"
                "timestamp": float      # When this was read (time.time())
            }

        WHY RETURN A DICT INSTEAD OF JUST A STRING?
            Later phases need more than just the raw text. The orchestrator
            needs to know WHICH app we're in (WhatsApp vs Gmail vs Word)
            to decide how to respond. The focused_text tells us what the
            user is currently typing, which is different from what they're reading.
            Structured data from day one means we don't rewrite this later.
        """
        if self._uia_available:
            result = self._read_via_uia()
            if result:
                return result

        # Fallback: at minimum return the window title
        return self._read_fallback()

    # -----------------------------------------------------------------------
    # Primary reader: Windows Accessibility API
    # -----------------------------------------------------------------------

    def _read_via_uia(self) -> Optional[dict]:
        """
        Uses uiautomation to walk the active window's UI element tree
        and extract all readable text.

        HOW THE ACCESSIBILITY TREE WALK WORKS:
            The tree has a root (the window itself) and children (panels, lists, etc.)
            We do a depth-first walk: go deep into each branch before moving sideways.
            At each node, we check if it has readable text and collect it.

            We limit depth to avoid going infinitely deep in complex apps,
            and we stop collecting if we've hit SCREEN_READ_MAX_CHARS.
        """
        try:
            import uiautomation as auto

            # GetFocusedControl() returns the UI element that currently has keyboard focus.
            # This is typically the text input field the user is typing in.
            focused_control = auto.GetFocusedControl()
            focused_text = ""
            if focused_control:
                focused_text = focused_control.Name or ""
                # For Edit controls (text boxes), get the actual typed content
                if hasattr(focused_control, 'GetValuePattern'):
                    try:
                        pattern = focused_control.GetValuePattern()
                        if pattern:
                            focused_text = pattern.Value or focused_text
                    except Exception:
                        pass  # Not all controls support ValuePattern — that's fine

            # GetForegroundControl() returns the top-level window that has focus.
            # This is the root of the tree we'll walk.
            window = auto.GetForegroundControl()
            if not window:
                log.debug("No foreground window found")
                return None

            window_title = window.Name or "Unknown Window"
            app_name = self._extract_app_name(window_title)

            log.debug("Reading window: %s", window_title)

            # Walk the accessibility tree and collect text
            text_parts = []
            self._walk_tree(window, text_parts, depth=0, max_depth=8)

            # Join all text parts, remove duplicates caused by parent/child overlap
            raw_text = "\n".join(filter(None, text_parts))
            cleaned_text = self._deduplicate_lines(raw_text)

            # Truncate to max chars — keep the END of the text (most recent = most relevant)
            if len(cleaned_text) > SCREEN_READ_MAX_CHARS:
                cleaned_text = cleaned_text[-SCREEN_READ_MAX_CHARS:]
                log.debug("Screen text truncated to %d chars", SCREEN_READ_MAX_CHARS)

            return {
                "app_name": app_name,
                "window_title": window_title,
                "text_content": cleaned_text,
                "focused_text": focused_text,
                "source": "uia",
                "timestamp": time.time()
            }

        except Exception as e:
            log.warning("uiautomation read failed: %s", str(e))
            return None

    def _walk_tree(
        self,
        control,
        text_parts: list,
        depth: int,
        max_depth: int
    ) -> None:
        """
        Recursively walks the UI automation element tree, collecting text.

        RECURSION EXPLAINED:
            This function calls itself on each child element.
            depth tracks how deep we are. When depth >= max_depth, we stop.
            This prevents infinite loops and keeps performance reasonable.

            Visual: imagine a file tree. Walking it recursively means:
            open folder → look inside → if subfolder, open that too → etc.
            max_depth is like saying "only go 8 folders deep maximum".

        Args:
            control: Current UI automation element
            text_parts: List we append text to (modified in place)
            depth: Current recursion depth
            max_depth: Maximum depth to recurse to
        """
        if depth > max_depth:
            return
        if len("".join(text_parts)) > SCREEN_READ_MAX_CHARS:
            return  # Already have enough text

        try:
            # Get the text name of this control
            name = control.Name
            if name and len(name.strip()) > 1:  # Skip single-char noise
                text_parts.append(name.strip())

            # GetChildren() returns the direct children of this element
            children = control.GetChildren()
            for child in children:
                self._walk_tree(child, text_parts, depth + 1, max_depth)

        except Exception:
            # Individual element failures are common and non-fatal.
            # Some elements are transient (tooltips, menus) and disappear
            # while we're reading them. We skip silently.
            pass

    # -----------------------------------------------------------------------
    # Fallback reader: window title only
    # -----------------------------------------------------------------------

    def _read_fallback(self) -> dict:
        """
        Last resort: reads just the active window title using ctypes.
        ctypes is Python's built-in way to call Windows DLL functions directly.
        GetForegroundWindow() + GetWindowText() are Win32 API functions
        available in user32.dll on every Windows installation.
        """
        try:
            import ctypes
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()  # Get handle to foreground window

            # Allocate a buffer for the window title (512 chars max)
            title_buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title_buffer, 512)
            window_title = title_buffer.value or "Unknown Window"

            log.debug("Fallback read: window title = %s", window_title)

            return {
                "app_name": self._extract_app_name(window_title),
                "window_title": window_title,
                "text_content": f"[Screen content unavailable — window: {window_title}]",
                "focused_text": "",
                "source": "fallback",
                "timestamp": time.time()
            }
        except Exception as e:
            log.error("Fallback screen read failed: %s", str(e))
            return {
                "app_name": "Unknown",
                "window_title": "Unknown",
                "text_content": "[Screen content unavailable]",
                "focused_text": "",
                "source": "error",
                "timestamp": time.time()
            }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _extract_app_name(self, window_title: str) -> str:
        """
        Extracts a clean app name from a window title.

        Window titles often look like:
            "Chat with John — WhatsApp"
            "Inbox — Gmail - Google Chrome"
            "Untitled - Notepad"

        We take the LAST segment after the last separator as the app name.
        This is a heuristic — not perfect but works for most cases.
        """
        for separator in [" — ", " - ", " | ", " – "]:
            if separator in window_title:
                return window_title.split(separator)[-1].strip()
        return window_title.strip()

    def _deduplicate_lines(self, text: str) -> str:
        """
        Removes duplicate adjacent lines from the collected text.
        Accessibility trees often return parent container text AND child text,
        causing the same string to appear multiple times in sequence.
        """
        lines = text.split("\n")
        seen = set()
        deduped = []
        for line in lines:
            stripped = line.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                deduped.append(stripped)
        return "\n".join(deduped)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

screen_reader = ScreenReader()
