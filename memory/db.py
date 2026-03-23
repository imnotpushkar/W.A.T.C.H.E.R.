"""
memory/db.py — SQLite Conversation Storage
===========================================
The raw storage layer for everything Watcher remembers.

WHAT IS SQLITE?
    SQLite is a file-based relational database — the entire database lives
    in a single file (watcher.db). No server process needed, no installation,
    no configuration. Python includes sqlite3 in its standard library.

    It's used in Firefox, Android, iOS, WhatsApp, and thousands of other
    applications. For a single-user local app like Watcher, it's the
    correct choice — simple, fast, zero overhead.

SCHEMA DESIGN:
    Three tables:

    contacts — one row per person Watcher has seen you interact with
        id, name, app, identifier (phone/email/username), first_seen, last_seen,
        message_count, notes (JSON blob for flexible extra data)

    conversations — one row per message/exchange
        id, contact_id, app, session_id, role (user/assistant/watcher),
        content, timestamp, context (what was on screen)

    sessions — one row per Watcher activation
        id, app, window_title, started_at, ended_at, trigger_count

WHY THREE TABLES?
    Normalisation — storing the same data in multiple places wastes space
    and creates inconsistency. contacts stores WHO once. conversations
    stores WHAT was said, referencing contacts by id. sessions tracks
    WHEN Watcher was active.

    If you want "all messages from John", you query conversations WHERE
    contact_id = john's id. You don't store John's name in every message row.

WHAT IS A CONTEXT MANAGER?
    The `with db:` pattern we use throughout. When you enter a `with` block,
    __enter__ is called. When you exit (normally or via exception), __exit__
    is called. For database connections, this handles commit/rollback
    automatically — if an exception occurs mid-transaction, changes are
    rolled back so the database stays consistent.
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import Optional
from core.config import DB_PATH
from core.logger import get_logger

log = get_logger(__name__)


class WatcherDB:
    """
    SQLite wrapper for all of Watcher's persistent storage.
    Handles connection management, schema creation, and all CRUD operations.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_schema()
        log.info("WatcherDB initialised at %s", self.db_path)

    def _connect(self) -> None:
        """
        Opens the SQLite connection.

        check_same_thread=False: SQLite connections are not thread-safe by
        default — only the thread that created them can use them. We set
        this to False because Watcher's pipeline runs in background threads.
        We manage thread safety ourselves by keeping operations atomic.

        detect_types: tells sqlite3 to convert stored timestamps back to
        Python datetime objects automatically when reading.
        """
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        # Row factory: makes query results accessible by column name
        # instead of index. result['content'] instead of result[3].
        self._conn.row_factory = sqlite3.Row
        # Enable WAL mode — Write-Ahead Logging.
        # Allows reads and writes to happen concurrently without blocking.
        # Better performance for our multi-threaded pipeline.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        log.debug("SQLite connection opened")

    def _create_schema(self) -> None:
        """
        Creates all tables if they don't already exist.
        Safe to call every startup — IF NOT EXISTS prevents errors.

        WHAT IS A PRIMARY KEY?
            A column that uniquely identifies each row. SQLite's INTEGER
            PRIMARY KEY is special — it auto-increments, so you never
            need to provide an id when inserting. SQLite assigns it.

        WHAT IS A FOREIGN KEY?
            A reference from one table to another. conversations.contact_id
            references contacts.id — this enforces that every conversation
            belongs to a real contact. The PRAGMA foreign_keys=ON above
            makes SQLite actually enforce this (it's off by default).

        WHAT IS AN INDEX?
            A separate data structure that makes lookups by a specific column
            fast. Without an index on contact_id, finding all conversations
            for a contact requires scanning every row. With the index, SQLite
            jumps directly to the relevant rows. We index columns we query often.
        """
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    app         TEXT NOT NULL,
                    identifier  TEXT,
                    first_seen  REAL NOT NULL,
                    last_seen   REAL NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    notes       TEXT DEFAULT '{}',
                    UNIQUE(name, app)
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_id  INTEGER,
                    app         TEXT NOT NULL,
                    session_id  INTEGER,
                    role        TEXT NOT NULL CHECK(role IN ('user','assistant','watcher')),
                    content     TEXT NOT NULL,
                    timestamp   REAL NOT NULL,
                    context     TEXT DEFAULT '',
                    FOREIGN KEY (contact_id) REFERENCES contacts(id),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    app         TEXT NOT NULL,
                    window_title TEXT DEFAULT '',
                    started_at  REAL NOT NULL,
                    ended_at    REAL,
                    trigger_count INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_contact
                    ON conversations(contact_id);
                CREATE INDEX IF NOT EXISTS idx_conversations_app
                    ON conversations(app);
                CREATE INDEX IF NOT EXISTS idx_conversations_timestamp
                    ON conversations(timestamp);
                CREATE INDEX IF NOT EXISTS idx_contacts_app_name
                    ON contacts(app, name);
            """)
        log.debug("Schema created/verified")

    # -----------------------------------------------------------------------
    # Session operations
    # -----------------------------------------------------------------------

    def start_session(self, app: str, window_title: str = "") -> int:
        """
        Records a new Watcher activation session.
        Returns the session id — used to link conversations to sessions.

        time.time() returns seconds since Unix epoch (Jan 1 1970) as float.
        Storing timestamps as floats is simpler than datetime objects —
        easy to compare, sort, and calculate differences.
        """
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO sessions (app, window_title, started_at)
                   VALUES (?, ?, ?)""",
                (app, window_title, time.time())
            )
            session_id = cursor.lastrowid
        log.debug("Session started: id=%d app=%s", session_id, app)
        return session_id

    def end_session(self, session_id: int, trigger_count: int = 1) -> None:
        """Records when a session ended and how many triggers occurred."""
        with self._conn:
            self._conn.execute(
                """UPDATE sessions
                   SET ended_at = ?, trigger_count = ?
                   WHERE id = ?""",
                (time.time(), trigger_count, session_id)
            )

    # -----------------------------------------------------------------------
    # Contact operations
    # -----------------------------------------------------------------------

    def get_or_create_contact(
        self,
        name: str,
        app: str,
        identifier: str = ""
    ) -> int:
        """
        Finds an existing contact or creates a new one.
        Returns the contact id.

        The UPSERT pattern (INSERT OR IGNORE + UPDATE) is cleaner than
        SELECT-then-INSERT because it's atomic — no race condition between
        checking if a contact exists and inserting it.

        WHAT IS UPSERT?
            A portmanteau of UPDATE + INSERT. The logic:
            - Try to INSERT the row
            - If a row with the same unique key already exists, don't error —
              instead UPDATE the existing row
            SQLite supports this via INSERT OR IGNORE + separate UPDATE,
            or via INSERT ... ON CONFLICT DO UPDATE (upsert syntax).
        """
        now = time.time()
        with self._conn:
            # Try to insert — ignore if already exists (same name + app)
            self._conn.execute(
                """INSERT OR IGNORE INTO contacts
                   (name, app, identifier, first_seen, last_seen, message_count)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (name, app, identifier, now, now)
            )
            # Update last_seen regardless
            self._conn.execute(
                """UPDATE contacts SET last_seen = ?
                   WHERE name = ? AND app = ?""",
                (now, name, app)
            )
            # Fetch the id
            row = self._conn.execute(
                "SELECT id FROM contacts WHERE name = ? AND app = ?",
                (name, app)
            ).fetchone()
        contact_id = row["id"]
        log.debug("Contact: id=%d name=%s app=%s", contact_id, name, app)
        return contact_id

    def get_contact(self, contact_id: int) -> Optional[dict]:
        """Returns a contact dict by id, or None if not found."""
        row = self._conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_contacts_for_app(self, app: str) -> list[dict]:
        """Returns all contacts seen in a given app, most recent first."""
        rows = self._conn.execute(
            """SELECT * FROM contacts WHERE app = ?
               ORDER BY last_seen DESC""",
            (app,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_contact_notes(self, contact_id: int, notes: dict) -> None:
        """Stores arbitrary JSON notes on a contact (tone, relationship, etc)."""
        with self._conn:
            self._conn.execute(
                "UPDATE contacts SET notes = ? WHERE id = ?",
                (json.dumps(notes), contact_id)
            )

    # -----------------------------------------------------------------------
    # Conversation operations
    # -----------------------------------------------------------------------

    def save_message(
        self,
        content: str,
        app: str,
        role: str = "user",
        contact_id: Optional[int] = None,
        session_id: Optional[int] = None,
        context: str = ""
    ) -> int:
        """
        Saves a single message to the conversations table.
        Returns the message id.

        Args:
            content:    The message text
            app:        Which app this came from
            role:       "user" (typed by user), "assistant" (Watcher's suggestion),
                        "watcher" (Watcher's spoken commentary)
            contact_id: Who this message is to/from (if known)
            session_id: Which Watcher session this belongs to
            context:    Brief description of what was on screen
        """
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO conversations
                   (contact_id, app, session_id, role, content, timestamp, context)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (contact_id, app, session_id, role, content, time.time(), context)
            )
            msg_id = cursor.lastrowid

        # Increment contact's message count if contact is known
        if contact_id:
            with self._conn:
                self._conn.execute(
                    """UPDATE contacts SET message_count = message_count + 1,
                       last_seen = ? WHERE id = ?""",
                    (time.time(), contact_id)
                )

        log.debug("Message saved: id=%d role=%s app=%s", msg_id, role, app)
        return msg_id

    def get_recent_messages(
        self,
        app: str,
        contact_id: Optional[int] = None,
        limit: int = 20
    ) -> list[dict]:
        """
        Returns recent messages for context building.

        If contact_id is given, returns messages for that specific person.
        Otherwise returns recent messages for the app generally.
        Ordered oldest-first so they read naturally as conversation history.

        Args:
            app:        Filter by app
            contact_id: Filter by contact (optional)
            limit:      Maximum messages to return
        """
        if contact_id:
            rows = self._conn.execute(
                """SELECT * FROM conversations
                   WHERE app = ? AND contact_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (app, contact_id, limit)
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM conversations
                   WHERE app = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (app, limit)
            ).fetchall()

        # Reverse so oldest is first (natural reading order)
        return [dict(r) for r in reversed(rows)]

    def get_conversation_summary(self, contact_id: int) -> dict:
        """
        Returns aggregate stats about a contact's conversation history.
        Used by context_builder to give Ollama a quick overview.
        """
        row = self._conn.execute(
            """SELECT
                COUNT(*) as total_messages,
                MIN(timestamp) as first_message,
                MAX(timestamp) as last_message,
                SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) as user_messages,
                SUM(CASE WHEN role='assistant' THEN 1 ELSE 0 END) as assistant_messages
               FROM conversations WHERE contact_id = ?""",
            (contact_id,)
        ).fetchone()
        return dict(row) if row else {}

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    def close(self) -> None:
        """Closes the database connection cleanly."""
        if self._conn:
            self._conn.close()
            log.debug("SQLite connection closed")

    def get_stats(self) -> dict:
        """Returns database statistics for debugging."""
        stats = {}
        stats["contacts"] = self._conn.execute(
            "SELECT COUNT(*) FROM contacts"
        ).fetchone()[0]
        stats["conversations"] = self._conn.execute(
            "SELECT COUNT(*) FROM conversations"
        ).fetchone()[0]
        stats["sessions"] = self._conn.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0]
        return stats


# Module-level singleton
db = WatcherDB()
