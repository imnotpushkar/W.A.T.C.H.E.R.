"""
brain/context_builder.py — Memory-Aware Prompt Assembly
=========================================================
Assembles enriched prompts by combining screen content with memory.

WITHOUT context_builder (Phase 1):
    Prompt = screen text + instruction
    Ollama knows nothing about who you are or who you're talking to.
    Every trigger is a blank slate.

WITH context_builder (Phase 2):
    Prompt = screen text + recent history + relevant past + contact profile + instruction
    Ollama knows: who this person is, how you normally write to them,
    what you last discussed, and any relevant past context.

THE ASSEMBLY PIPELINE:
    1. Detect contact from screen content (who is this message to/from?)
    2. Get or create contact record in SQLite
    3. Fetch recent message history for this contact
    4. Semantic search for relevant past messages
    5. Build contact profile (tone, relationship, message count)
    6. Assemble all of this into a structured prompt

CONTACT DETECTION:
    This is hard to do perfectly in Phase 2. WhatsApp Desktop puts the
    contact name in the window title ("John — WhatsApp"). Gmail shows
    the sender. We parse what we can and fall back to "Unknown contact"
    when we can't detect a name.

    Phase 3 will improve this with app-specific parsers per app.

TOKEN BUDGET:
    LLM context windows have limits. Llama 3.1 8B supports 128k tokens
    but longer prompts = slower generation. We keep the enriched prompt
    under a reasonable size by truncating history and limiting results.
    Approximate token count: 1 token ≈ 0.75 words ≈ 4 characters.
"""

import re
import time
from typing import Optional
from core.logger import get_logger
from memory.db import db
from memory.vector_store import vector_store

log = get_logger(__name__)

# How much history to include in prompts — balance between context and speed
MAX_RECENT_MESSAGES = 10     # Last N messages with this contact
MAX_SEMANTIC_RESULTS = 3     # Top N semantically similar past messages
MAX_HISTORY_CHARS = 1500     # Total char budget for history section
MAX_SCREEN_CHARS = 1500      # Total char budget for screen content


class ContextBuilder:
    """
    Builds enriched prompts by combining screen content with memory.
    Called by orchestrator instead of building prompts directly.
    """

    def __init__(self):
        self._current_session_id: Optional[int] = None
        self._current_contact_id: Optional[int] = None
        self._current_app: str = ""
        log.debug("ContextBuilder initialised")

    def start_session(self, app: str, window_title: str = "") -> None:
        """
        Called when Watcher activates. Records the session and sets context.
        Should be called once per trigger, before build_prompt().
        """
        self._current_app = app
        self._current_session_id = db.start_session(app, window_title)
        self._current_contact_id = None
        log.debug("Context session started: app=%s session=%d",
                  app, self._current_session_id)

    def end_session(self) -> None:
        """Called when pipeline completes. Records session end."""
        if self._current_session_id:
            db.end_session(self._current_session_id, trigger_count=1)

    def build_prompt(
        self,
        app_name: str,
        window_title: str,
        text_content: str,
        focused_text: str
    ) -> str:
        """
        Main method — assembles the full enriched prompt.
        Replaces orchestrator's _build_suggestion_prompt() in Phase 2.

        Returns a prompt string ready to send to Ollama.
        """
        parts = []

        # ---------------------------------------------------------------
        # Section 1: App context
        # ---------------------------------------------------------------
        parts.append(f"APP: {app_name}")
        if window_title and window_title != app_name:
            parts.append(f"WINDOW: {window_title}")

        # ---------------------------------------------------------------
        # Section 2: Contact detection and profile
        # ---------------------------------------------------------------
        contact_name = self._detect_contact(app_name, window_title, text_content)

        if contact_name:
            self._current_contact_id = db.get_or_create_contact(
                name=contact_name,
                app=app_name
            )
            profile_section = self._build_contact_profile(
                contact_name, self._current_contact_id
            )
            if profile_section:
                parts.append(profile_section)

        # ---------------------------------------------------------------
        # Section 3: Conversation history from memory
        # ---------------------------------------------------------------
        history_section = self._build_history_section(
            app_name, self._current_contact_id
        )
        if history_section:
            parts.append(history_section)

        # ---------------------------------------------------------------
        # Section 4: Semantic context (relevant past messages)
        # ---------------------------------------------------------------
        if text_content and vector_store.available:
            semantic_section = self._build_semantic_section(
                query=text_content[-300:],  # Use recent screen content as query
                app_name=app_name,
                contact_name=contact_name or ""
            )
            if semantic_section:
                parts.append(semantic_section)

        # ---------------------------------------------------------------
        # Section 5: Current screen content
        # ---------------------------------------------------------------
        if text_content and not text_content.startswith("[Screen"):
            content = text_content[-MAX_SCREEN_CHARS:] if \
                len(text_content) > MAX_SCREEN_CHARS else text_content
            parts.append(f"CURRENT SCREEN:\n{content}")

        if focused_text:
            parts.append(f"USER IS TYPING: {focused_text}")

        # ---------------------------------------------------------------
        # Section 6: Instruction
        # ---------------------------------------------------------------
        parts.append(
            "Output ONLY the suggested reply or text completion. "
            "Spell every word correctly. "
            "Match the tone and style shown in the conversation history. "
            "No explanation. No preamble. Just the text itself."
        )

        prompt = "\n\n".join(parts)
        log.debug("Context prompt built: %d chars, contact=%s",
                  len(prompt), contact_name or "unknown")
        return prompt

    def save_interaction(
        self,
        screen_content: str,
        suggestion: str,
        accepted: bool
    ) -> None:
        """
        Saves this interaction to memory after it completes.
        Called by orchestrator after the user accepts or dismisses.

        We save both what was on screen (user role) and what Watcher
        suggested (assistant role). This builds up the conversation
        history that future triggers will use as context.

        Args:
            screen_content: What was visible on screen (the "user" message)
            suggestion:     What Watcher suggested
            accepted:       Whether the user pressed Tab to accept
        """
        if not self._current_session_id:
            return

        context_note = f"accepted={accepted}"

        # Save the screen content as the user's message
        if screen_content and not screen_content.startswith("[Screen"):
            msg_id = db.save_message(
                content=screen_content[-500:],  # Truncate very long content
                app=self._current_app,
                role="user",
                contact_id=self._current_contact_id,
                session_id=self._current_session_id,
                context=context_note
            )
            # Also index in vector store for future semantic search
            if vector_store.available and msg_id:
                vector_store.add_message(
                    message_id=msg_id,
                    content=screen_content[-500:],
                    app=self._current_app,
                    contact_name=self._get_contact_name(),
                    role="user"
                )

        # Save the suggestion as Watcher's response
        if suggestion and not suggestion.startswith("[Watcher:"):
            msg_id = db.save_message(
                content=suggestion,
                app=self._current_app,
                role="assistant",
                contact_id=self._current_contact_id,
                session_id=self._current_session_id,
                context=context_note
            )
            if vector_store.available and msg_id:
                vector_store.add_message(
                    message_id=msg_id,
                    content=suggestion,
                    app=self._current_app,
                    contact_name=self._get_contact_name(),
                    role="assistant"
                )

        log.debug("Interaction saved to memory (accepted=%s)", accepted)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _detect_contact(
        self,
        app_name: str,
        window_title: str,
        text_content: str
    ) -> Optional[str]:
        """
        Attempts to extract a contact name from available context.

        DETECTION STRATEGIES BY APP:

        WhatsApp Desktop: window title is "Contact Name — WhatsApp"
            e.g. "John Smith — WhatsApp" → "John Smith"

        Gmail/Outlook: window title contains email subject and sender
            e.g. "Re: Meeting - john@example.com - Gmail" → parse sender

        Discord: window title is "#channel-name" or "Username"
            e.g. "john_doe - Discord" → "john_doe"

        Generic: try the last segment of the title before the app name.

        This is a best-effort heuristic. Phase 3 will add proper
        per-app parsers with deeper accessibility tree reading.
        """
        app_lower = app_name.lower()
        title_lower = window_title.lower()

        # WhatsApp: "Contact Name — WhatsApp" or "Contact Name - WhatsApp"
        if "whatsapp" in app_lower:
            for sep in [" — ", " - "]:
                if sep in window_title:
                    candidate = window_title.split(sep)[0].strip()
                    # Filter out WhatsApp UI elements that aren't contacts
                    if candidate and candidate.lower() not in {
                        "whatsapp", "chats", "status", "calls", "settings"
                    }:
                        return candidate
            return None

        # Discord: "username - Discord"
        if "discord" in app_lower:
            for sep in [" - Discord", " — Discord"]:
                if sep in window_title:
                    candidate = window_title.replace(sep, "").strip()
                    # Discord channels start with #
                    if candidate and not candidate.startswith("#"):
                        return candidate
            return None

        # Telegram: "First Last - Telegram"
        if "telegram" in app_lower:
            for sep in [" - Telegram", " — Telegram"]:
                if sep in window_title:
                    return window_title.replace(sep, "").strip()
            return None

        # Gmail: try to extract sender from title
        # Gmail titles: "Subject - sender@email.com - Gmail"
        if "gmail" in app_lower or "mail" in app_lower:
            parts = window_title.split(" - ")
            for part in parts:
                if "@" in part and "gmail" not in part.lower():
                    return part.strip()
            return None

        # No contact detected for this app
        return None

    def _build_contact_profile(
        self,
        contact_name: str,
        contact_id: int
    ) -> str:
        """
        Builds a brief profile section from the contact's stored data.
        Tells Ollama who this person is and how many times they've interacted.
        """
        contact = db.get_contact(contact_id)
        if not contact:
            return ""

        summary = db.get_conversation_summary(contact_id)
        msg_count = summary.get("total_messages", 0)

        if msg_count == 0:
            return f"CONTACT: {contact_name} (first interaction)"

        # Calculate days since first interaction
        first_seen = contact.get("first_seen", 0)
        days_known = int((time.time() - first_seen) / 86400) if first_seen else 0

        # Load any stored notes (tone profile, relationship, etc)
        notes = {}
        try:
            import json
            notes = json.loads(contact.get("notes", "{}"))
        except Exception:
            pass

        profile_parts = [f"CONTACT: {contact_name}"]
        profile_parts.append(f"  Known for {days_known} days, "
                              f"{msg_count} past interactions")

        if notes.get("tone"):
            profile_parts.append(f"  Typical tone: {notes['tone']}")
        if notes.get("relationship"):
            profile_parts.append(f"  Relationship: {notes['relationship']}")

        return "\n".join(profile_parts)

    def _build_history_section(
        self,
        app_name: str,
        contact_id: Optional[int]
    ) -> str:
        """
        Fetches and formats recent conversation history.
        Keeps within MAX_HISTORY_CHARS budget by truncating older messages.
        """
        messages = db.get_recent_messages(
            app=app_name,
            contact_id=contact_id,
            limit=MAX_RECENT_MESSAGES
        )

        if not messages:
            return ""

        lines = ["CONVERSATION HISTORY (recent):"]
        total_chars = 0

        for msg in messages:
            role_label = {
                "user": "You",
                "assistant": "Watcher suggested",
                "watcher": "Watcher said"
            }.get(msg["role"], msg["role"])

            line = f"  [{role_label}]: {msg['content']}"

            # Stay within char budget
            if total_chars + len(line) > MAX_HISTORY_CHARS:
                lines.append("  [earlier history truncated]")
                break

            lines.append(line)
            total_chars += len(line)

        return "\n".join(lines)

    def _build_semantic_section(
        self,
        query: str,
        app_name: str,
        contact_name: str
    ) -> str:
        """
        Fetches semantically relevant past messages and formats them.
        These are messages from the past that are topically related to
        what's currently on screen — even if not recent.
        """
        results = vector_store.search(
            query=query,
            app=app_name,
            contact_name=contact_name,
            limit=MAX_SEMANTIC_RESULTS
        )

        if not results:
            return ""

        # Filter out very dissimilar results (distance > 0.7 means not very relevant)
        relevant = [r for r in results if r["distance"] < 0.7]
        if not relevant:
            return ""

        lines = ["RELEVANT PAST CONTEXT:"]
        for r in relevant:
            role_label = "You" if r["role"] == "user" else "Watcher"
            lines.append(f"  [{role_label}]: {r['content'][:150]}")

        return "\n".join(lines)

    def _get_contact_name(self) -> str:
        """Returns current contact name from SQLite, or empty string."""
        if not self._current_contact_id:
            return ""
        contact = db.get_contact(self._current_contact_id)
        return contact["name"] if contact else ""


# Module-level singleton
context_builder = ContextBuilder()
