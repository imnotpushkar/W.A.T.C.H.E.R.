"""
output/typist.py — Automated Typing
=====================================
Types text into whatever app is currently in focus on behalf of the user.
"""

import time
import pyautogui
from core.config import TYPIST_CHAR_DELAY
from core.logger import get_logger

log = get_logger(__name__)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.01


class Typist:

    def type_text(self, text: str) -> None:
        """
        Types the given text into the active window.

        FIX — leading space/tab from Tab keypress:
            When the user presses Tab to accept, pynput's Listener with
            suppress=False means the Tab key ALSO reaches the active app,
            which inserts a tab character before we start typing.
            Fix: send a Backspace first to delete that tab character,
            then type the actual text.
        """
        if not text:
            return

        log.info("Typing %d characters", len(text))

        # Wait for overlay to hide and focus to return to the app
        time.sleep(0.15)

        try:
            # Delete the tab character that Tab keypress inserted
            # into the active app before we got here
            pyautogui.press('backspace')
            time.sleep(0.05)

            if self._is_safe_for_write(text):
                pyautogui.write(text, interval=TYPIST_CHAR_DELAY)
            else:
                self._type_via_clipboard(text)

            log.info("Typing complete")

        except pyautogui.FailSafeException:
            log.warning("pyautogui failsafe triggered")
        except Exception as e:
            log.error("Typing failed: %s", str(e))

    def _is_safe_for_write(self, text: str) -> bool:
        """ASCII only — non-ASCII needs clipboard method."""
        try:
            text.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False

    def _type_via_clipboard(self, text: str) -> None:
        """
        Pastes non-ASCII text via clipboard.
        Saves and restores original clipboard content.
        """
        import pyperclip
        log.debug("Using clipboard method for non-ASCII text")
        try:
            original = pyperclip.paste()
        except Exception:
            original = ""
        try:
            pyperclip.copy(text)
            time.sleep(0.05)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
        finally:
            try:
                time.sleep(0.1)
                pyperclip.copy(original)
            except Exception:
                pass

    def clear_line(self) -> None:
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.05)
        pyautogui.press('delete')


typist = Typist()
