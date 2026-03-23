"""
memory/vector_store.py — ChromaDB Semantic Search
===================================================
Enables "find me messages similar to this topic" — something raw SQL cannot do.

WHAT IS A VECTOR / EMBEDDING?
    A vector is a list of numbers that represents the meaning of a piece of text.
    Similar texts produce similar vectors — "How are you?" and "How's it going?"
    would have vectors that are close together in mathematical space.

    An embedding model converts text → vector. ChromaDB's default model
    (all-MiniLM-L6-v2) produces 384-dimensional vectors — each text becomes
    a list of 384 floats.

WHAT IS SEMANTIC SEARCH?
    Regular SQL search: WHERE content LIKE '%budget%'
    This only finds exact word matches. "finances" wouldn't match "budget".

    Semantic search: find messages that MEAN something similar to "budget"
    This finds "money", "expenses", "Q3 projections", "invoice" — even if
    the word "budget" never appears. It searches by meaning, not keywords.

    ChromaDB stores vectors alongside the text. When you query, it converts
    your query to a vector and finds the stored vectors closest to it.
    "Closeness" is measured by cosine similarity — the angle between vectors.

WHY BOTH SQLITE AND CHROMADB?
    They serve different purposes:
    - SQLite: reliable structured storage, relationships, exact queries
      "give me the last 20 messages from this contact"
    - ChromaDB: semantic similarity search
      "find past conversations where budget was discussed"

    In Phase 2, context_builder.py will use BOTH:
    SQLite for recent history, ChromaDB for relevant past context.

CHROMADB COLLECTIONS:
    A collection is like a table — a named group of vectors.
    We use one collection: "watcher_conversations"
    Each item has: id (str), document (text), metadata (dict), embedding (auto)
"""

import time
from typing import Optional
from pathlib import Path
from core.config import CHROMA_PATH
from core.logger import get_logger

log = get_logger(__name__)


class VectorStore:
    """
    ChromaDB wrapper for semantic search over Watcher's conversation history.
    """

    COLLECTION_NAME = "watcher_conversations"

    def __init__(self):
        self._client = None
        self._collection = None
        self._available = False
        self._setup()

    def _setup(self) -> None:
        """
        Initialises ChromaDB client and collection.
        Fails gracefully — if ChromaDB has any issue, Watcher still runs,
        just without semantic search capability.

        PersistentClient stores data at CHROMA_PATH (data/chroma/).
        Data persists across restarts — vectors are saved to disk.

        WHAT IS AN EMBEDDING FUNCTION?
            ChromaDB needs to convert text to vectors. By default it uses
            all-MiniLM-L6-v2 from sentence-transformers — a lightweight
            model that runs locally. On first run it downloads ~90MB.
            After that it's cached in the ChromaDB directory.
        """
        try:
            import chromadb
            from chromadb.utils import embedding_functions

            self._client = chromadb.PersistentClient(path=str(CHROMA_PATH))

            # Default embedding function — all-MiniLM-L6-v2
            # Downloads on first use (~90MB), cached after that
            ef = embedding_functions.DefaultEmbeddingFunction()

            # get_or_create_collection: creates if doesn't exist, loads if it does
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"}
                # cosine similarity is better than L2 distance for text
            )

            self._available = True
            count = self._collection.count()
            log.info("VectorStore ready — %d documents indexed", count)

        except ImportError:
            log.warning("chromadb not installed — semantic search unavailable")
        except Exception as e:
            log.warning("VectorStore setup failed: %s — semantic search unavailable", str(e))

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def add_message(
        self,
        message_id: int,
        content: str,
        app: str,
        contact_name: str = "",
        role: str = "user",
        timestamp: Optional[float] = None
    ) -> bool:
        """
        Adds a message to the vector store for future semantic search.

        Each document needs a unique string id. We use "msg_{message_id}"
        so we can cross-reference with SQLite's conversations table.

        Metadata is stored alongside vectors — it's used to filter results.
        For example: query similar messages BUT only from WhatsApp.
        ChromaDB supports metadata filtering in the same query.

        Args:
            message_id:   SQLite conversation id (for cross-referencing)
            content:      The message text to embed and store
            app:          Which app this came from
            contact_name: Who this message is from/to
            role:         user / assistant / watcher
            timestamp:    When the message was sent

        Returns:
            True if stored successfully, False if vector store unavailable
        """
        if not self._available or not content.strip():
            return False

        try:
            self._collection.add(
                ids=[f"msg_{message_id}"],
                documents=[content],
                metadatas=[{
                    "app": app,
                    "contact": contact_name,
                    "role": role,
                    "timestamp": timestamp or time.time(),
                    "message_id": message_id
                }]
            )
            log.debug("Vector stored: msg_%d (%d chars)", message_id, len(content))
            return True
        except Exception as e:
            log.error("Failed to store vector: %s", str(e))
            return False

    def search(
        self,
        query: str,
        app: str = "",
        contact_name: str = "",
        limit: int = 5
    ) -> list[dict]:
        """
        Finds messages semantically similar to the query.

        ChromaDB converts the query to a vector, then finds the closest
        stored vectors using cosine similarity. Returns the most relevant
        past messages regardless of exact word matches.

        Args:
            query:        The text to search for similar content
            app:          Optional — filter to one app only
            contact_name: Optional — filter to one contact only
            limit:        Maximum results to return

        Returns:
            List of dicts with keys: content, app, contact, role,
            timestamp, message_id, distance (similarity score)

        WHAT IS 'where' IN CHROMADB?
            Metadata filtering. `{"app": {"$eq": "WhatsApp"}}` means:
            only return results where metadata["app"] == "WhatsApp".
            This narrows the search before computing similarity.
        """
        if not self._available or not query.strip():
            return []

        try:
            # Build metadata filter
            where = {}
            if app and contact_name:
                where = {"$and": [
                    {"app": {"$eq": app}},
                    {"contact": {"$eq": contact_name}}
                ]}
            elif app:
                where = {"app": {"$eq": app}}
            elif contact_name:
                where = {"contact": {"$eq": contact_name}}

            # query() converts query text to vector and finds nearest neighbours
            kwargs = {
                "query_texts": [query],
                "n_results": min(limit, self._collection.count() or 1),
                "include": ["documents", "metadatas", "distances"]
            }
            if where:
                kwargs["where"] = where

            results = self._collection.query(**kwargs)

            # Unpack ChromaDB's nested result structure
            # results["documents"][0] = list of matching documents (for query 0)
            # results["metadatas"][0] = list of metadata dicts
            # results["distances"][0] = list of similarity scores
            output = []
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]

            for doc, meta, dist in zip(docs, metas, dists):
                output.append({
                    "content": doc,
                    "app": meta.get("app", ""),
                    "contact": meta.get("contact", ""),
                    "role": meta.get("role", ""),
                    "timestamp": meta.get("timestamp", 0),
                    "message_id": meta.get("message_id", 0),
                    "distance": round(dist, 4)
                    # distance: 0.0 = identical, 1.0 = completely different
                })

            log.debug("Semantic search: query='%s' → %d results", query[:40], len(output))
            return output

        except Exception as e:
            log.error("Semantic search failed: %s", str(e))
            return []

    def delete_message(self, message_id: int) -> bool:
        """Removes a message from the vector store by its SQLite id."""
        if not self._available:
            return False
        try:
            self._collection.delete(ids=[f"msg_{message_id}"])
            return True
        except Exception as e:
            log.error("Failed to delete vector: %s", str(e))
            return False

    def count(self) -> int:
        """Returns number of documents in the vector store."""
        if not self._available:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    @property
    def available(self) -> bool:
        """True if ChromaDB is running and accessible."""
        return self._available


# Module-level singleton
vector_store = VectorStore()
