"""
output/typist.py — Automated Typing
=====================================
Types text into whatever app is currently in focus, on behalf of the user.

HOW PYAUTOGUI TYPING WORKS:
    pyautogui uses Windows' SendInput API under the hood to simulate
    keyboard events at the OS level. These events are indistinguishable
    from real keypresses to the receiving application.

    pyautogui.write() — types a string character by character with a delay
    pyautogui.hotkey() — presses a key combination (e.g. ctrl+a)
    pyautogui.press() — presses and releases a single key

WHY NOT JUST USE THE CLIPBOARD (CTRL+V)?
    Clipboard paste is faster but has side effects:
    1. It overwrites whatever the user has copied — losing their clipboard
    2. Some apps (password fields, certain editors) block paste
    3. Some apps format pasted text differently than typed text
    Typing character by character is slower but more reliable and non-destructive.
    We'll add a clipboard option as an alternative in a later phase.

IMPORTANT — FOCUS:
    pyautogui types into WHICHEVER WINDOW HAS FOCUS at the moment of typing.
    The overlay is shown with WA_ShowWithoutActivating, so focus stays
    in the original app. When the user presses Tab to accept, focus is
    still in their original app — so typing goes there correctly.
    This is why WA_ShowWithoutActivating in overlay.py is critical.
"""

import time
import pyautogui
from core.config import TYPIST_CHAR_DELAY
from core.logger import get_logger

log = get_logger(__name__)

# Safety setting — if pyautogui detects the mouse in the corner, it stops.
# This is pyautogui's built-in emergency stop. We keep it enabled.
pyautogui.FAILSAFE = True

# Pause between pyautogui actions (seconds). Prevents overwhelming some apps.
pyautogui.PAUSE = 0.01


class Typist:
    """
    Types text into the currently focused application.
    """

    def type_text(self, text: str) -> None:
        """
        Types the given text into the active window character by character.

        Args:
            text: The text to type.

        INTERVAL PARAMETER:
            pyautogui.write(text, interval=X) waits X seconds between each
            character. Too fast (0.0) can lose characters in some apps that
            don't process keypresses fast enough. 0.02 (20ms) is reliable.
        """
        if not text:
            return

        log.info("Typing %d characters", len(text))

        # Small pause before typing — gives focus a moment to settle
        # after the overlay hides and before we start sending keypresses
        time.sleep(0.1)

        try:
            # pyautogui.write() handles standard ASCII characters well.
            # For text with special characters, emojis, or Unicode,
            # we use a clipboard-based approach as fallback.
            if self._is_safe_for_write(text):
                pyautogui.write(text, interval=TYPIST_CHAR_DELAY)
            else:
                self._type_via_clipboard(text)

            log.info("Typing complete")

        except pyautogui.FailSafeException:
            log.warning("pyautogui failsafe triggered — mouse moved to corner")
        except Exception as e:
            log.error("Typing failed: %s", str(e))

    def _is_safe_for_write(self, text: str) -> bool:
        """
        Checks if text can be safely typed with pyautogui.write().
        pyautogui.write() only handles ASCII characters reliably.
        Non-ASCII (Hindi, emoji, special symbols) need clipboard method.
        """
        try:
            text.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False

    def _type_via_clipboard(self, text: str) -> None:
        """
        Types text by placing it in the clipboard and pasting.
        Used for non-ASCII text that pyautogui.write() can't handle.

        We save the current clipboard, paste our text, then restore
        the original clipboard content so the user doesn't lose it.
        """
        import pyperclip  # pyperclip is installed as a dependency of pyautogui

        log.debug("Using clipboard method for non-ASCII text")

        # Save current clipboard content
        try:
            original_clipboard = pyperclip.paste()
        except Exception:
            original_clipboard = ""

        try:
            # Put our text in the clipboard
            pyperclip.copy(text)
            time.sleep(0.05)  # Brief pause for clipboard to update

            # Paste it (Ctrl+V)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)

        finally:
            # Always restore original clipboard
            try:
                time.sleep(0.1)
                pyperclip.copy(original_clipboard)
            except Exception:
                pass

    def clear_line(self) -> None:
        """
        Clears the current line in the focused input field.
        Uses Ctrl+A then Delete — works in most text inputs.
        """
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.05)
        pyautogui.press('delete')


# Module-level singleton
typist = Typist()
