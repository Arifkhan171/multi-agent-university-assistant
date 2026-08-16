"""
valkey_store.py — Persistent Session Storage for UOLI Chatbot
=============================================================
WHAT THIS FILE DOES:
    Stores conversation history and summaries in Valkey (persistent database).
    Replaces in-memory storage — data survives server restarts.

WHY VALKEY NOT REDIS:
    Same API. BSD license. Future-proof. Free forever.

HOW IT WORKS:
    Every session gets two keys in Valkey:
        session:{id}:messages → list of conversation messages
        session:{id}:summary  → compressed summary of older messages

    Both keys auto-expire after 24 hours of inactivity (TTL).
    When student returns within 24h → history restored automatically.
    After 24h → session cleared → fresh start.

HOW TO USE IN workflow.py:
    # BEFORE (in-memory):
    from session_store import SessionStore
    self.store = SessionStore()

    # AFTER (persistent):
    from valkey_store import ValkeySessionStore
    self.store = ValkeySessionStore()

    Everything else stays identical — same method names, same behavior.

AUTHOR: UOLI Chatbot Project
"""

import json
import logging
from typing import List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

logger = logging.getLogger(__name__)

# ── How long a session stays alive without activity ──────────
# 24 hours = student can return next day and still have context
# Change to 3600 for 1 hour, 604800 for 1 week
SESSION_TTL_SECONDS = 86400  # 24 hours

# ── How many messages before summarization triggers ──────────
SUMMARIZE_EVERY_N = 10


class ValkeySessionStore:
    """
    Persistent session store using Valkey.

    Stores per-session:
        - Last N messages (raw conversation)
        - Summary (compressed older history)

    All data auto-expires after SESSION_TTL_SECONDS of inactivity.
    """

    def __init__(self,
                 host: str = "localhost",
                 port: int = 6379,
                 db:   int = 0):
        """
        Connect to Valkey server.

        Args:
            host: Valkey server hostname (localhost for same machine)
            port: Valkey port (6379 is default — same as Redis)
            db:   Database number (0-15, use 0 for chatbot)
        """
        try:
            import valkey
            self.client = valkey.Valkey(
                host     = host,
                port     = port,
                db       = db,
                decode_responses = True,  # return strings not bytes
            )
            # Test connection immediately
            self.client.ping()
            self.using_valkey = True
            print(f"✅ Valkey connected at {host}:{port} (db={db})")
            logger.info(f"Valkey session store connected: {host}:{port}")

        except Exception as e:
            # If Valkey is not running — fall back to in-memory
            # This ensures chatbot NEVER crashes due to storage issues
            self.client      = None
            self.using_valkey = False
            self._memory      = {}  # fallback in-memory store
            print(f"⚠️  Valkey not available ({e}). Using in-memory sessions.")
            logger.warning(f"Valkey unavailable — using in-memory fallback: {e}")

    @property
    def using_redis(self) -> bool:
        """Alias for using_valkey — keeps backward compatibility with code that checks .using_redis."""
        return self.using_valkey

    # ──────────────────────────────────────────────────────────
    #  Key builders — all Valkey keys follow a consistent pattern
    # ──────────────────────────────────────────────────────────

    def _msg_key(self, session_id: str) -> str:
        """Key for storing messages list."""
        return f"session:{session_id}:messages"

    def _sum_key(self, session_id: str) -> str:
        """Key for storing conversation summary."""
        return f"session:{session_id}:summary"

    # ──────────────────────────────────────────────────────────
    #  Message serialization — LangChain messages ↔ JSON
    # ──────────────────────────────────────────────────────────

    def _serialize_messages(self, messages: List[BaseMessage]) -> str:
        """
        Convert LangChain messages to JSON string for storage.

        WHY: Valkey stores strings. LangChain messages are Python objects.
             We serialize to JSON and deserialize on load.

        Format stored:
            [
                {"role": "human", "content": "who is VC"},
                {"role": "ai",    "content": "Engr. Prof. Dr. Ehsanullah..."}
            ]
        """
        serialized = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                serialized.append({"role": "human", "content": msg.content})
            elif isinstance(msg, AIMessage):
                serialized.append({"role": "ai", "content": msg.content})
            # Skip other message types (SystemMessage etc)
        return json.dumps(serialized, ensure_ascii=False)

    def _deserialize_messages(self, json_str: str) -> List[BaseMessage]:
        """
        Convert JSON string back to LangChain messages.
        Returns empty list if JSON is invalid (safe fallback).
        """
        try:
            data = json.loads(json_str)
            messages = []
            for item in data:
                if item["role"] == "human":
                    messages.append(HumanMessage(content=item["content"]))
                elif item["role"] == "ai":
                    messages.append(AIMessage(content=item["content"]))
            return messages
        except Exception as e:
            logger.error(f"Failed to deserialize messages: {e}")
            return []

    # ──────────────────────────────────────────────────────────
    #  Core operations — load and save
    # ──────────────────────────────────────────────────────────

    def load(self, session_id: str) -> List[BaseMessage]:
        """
        Load conversation messages for a session.
        Returns empty list if session does not exist.

        Called: At start of every chat() and stream_chat() call.
        """
        if not self.using_valkey:
            # Fallback: in-memory
            return self._memory.get(
                f"{session_id}:messages", []
            )

        try:
            key  = self._msg_key(session_id)
            data = self.client.get(key)

            if data is None:
                return []  # new session

            return self._deserialize_messages(data)

        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return []

    def save(self, session_id: str, messages: List[BaseMessage]) -> None:
        """
        Save conversation messages for a session.
        Resets TTL — session stays alive for another 24 hours.

        Called: After every chat() and stream_chat() response.
        """
        if not self.using_valkey:
            self._memory[f"{session_id}:messages"] = messages
            return

        try:
            key  = self._msg_key(session_id)
            data = self._serialize_messages(messages)
            # EX = expire after N seconds (resets on every save)
            self.client.set(key, data, ex=SESSION_TTL_SECONDS)

        except Exception as e:
            logger.error(f"Failed to save session {session_id}: {e}")

    # ──────────────────────────────────────────────────────────
    #  Summary operations
    # ──────────────────────────────────────────────────────────

    def load_summary(self, session_id: str) -> str:
        """
        Load compressed summary of older conversation.
        Returns empty string if no summary exists yet.
        """
        if not self.using_valkey:
            return self._memory.get(f"{session_id}:summary", "")

        try:
            key  = self._sum_key(session_id)
            data = self.client.get(key)
            return data if data else ""

        except Exception as e:
            logger.error(f"Failed to load summary {session_id}: {e}")
            return ""

    def save_summary(self, session_id: str, summary: str) -> None:
        """Save compressed conversation summary."""
        if not self.using_valkey:
            self._memory[f"{session_id}:summary"] = summary
            return

        try:
            key = self._sum_key(session_id)
            self.client.set(key, summary, ex=SESSION_TTL_SECONDS)

        except Exception as e:
            logger.error(f"Failed to save summary {session_id}: {e}")

    # ──────────────────────────────────────────────────────────
    #  TTL extension
    # ──────────────────────────────────────────────────────────

    def extend_ttl(self, session_id: str) -> None:
        """
        Reset the 24-hour expiry timer for both session keys.
        Called after every successful response.

        WHY: Without this, a student asking many questions over 
             several hours would have their session expire mid-conversation.
             Every message resets the 24-hour countdown.
        """
        if not self.using_valkey:
            return  # in-memory has no TTL

        try:
            self.client.expire(self._msg_key(session_id), SESSION_TTL_SECONDS)
            self.client.expire(self._sum_key(session_id), SESSION_TTL_SECONDS)
        except Exception as e:
            logger.error(f"Failed to extend TTL for {session_id}: {e}")

    # ──────────────────────────────────────────────────────────
    #  History trimming
    # ──────────────────────────────────────────────────────────

    def trim_history(self,
                     messages:     List[BaseMessage],
                     max_messages: int = 20) -> List[BaseMessage]:
        """
        Keep only the most recent N messages.

        WHY: Prevents unbounded memory/token growth.
             Even with summarization, we cap raw messages at 20.
             Older messages are already captured in the summary.
        """
        if len(messages) > max_messages:
            return messages[-max_messages:]
        return messages

    # ──────────────────────────────────────────────────────────
    #  Session deletion
    # ──────────────────────────────────────────────────────────

    def delete(self, session_id: str) -> None:
        """
        Delete all data for a session.
        Called when student clicks "Clear My Memory" button.
        """
        if not self.using_valkey:
            self._memory.pop(f"{session_id}:messages", None)
            self._memory.pop(f"{session_id}:summary",  None)
            return

        try:
            self.client.delete(
                self._msg_key(session_id),
                self._sum_key(session_id),
            )
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")

    # ──────────────────────────────────────────────────────────
    #  Summarization — runs in background thread
    # ──────────────────────────────────────────────────────────

    def maybe_summarize(self,
                        session_id: str,
                        messages:   List[BaseMessage],
                        llm) -> None:
        """
        Compress old messages into summary when conversation gets long.

        WHEN: After every chat response — but only acts when
              message count exceeds SUMMARIZE_EVERY_N (10).

        HOW:
            Messages 1-4  → summarized (compressed to text)
            Messages 5-10 → kept raw (most recent context)
            New summary   → old summary + new compressed text

        WHY BACKGROUND: This LLM call happens AFTER the answer
            is already returned to the student. User never waits for it.
            Called via threading.Thread(daemon=True) in workflow.py.
        """
        if len(messages) <= SUMMARIZE_EVERY_N:
            return  # not enough messages yet

        # Split messages — older ones get summarized, recent ones stay raw
        messages_to_summarize = messages[:-6]   # everything except last 6
        recent_messages       = messages[-6:]   # last 6 stay raw

        existing_summary = self.load_summary(session_id)

        # Build summarization prompt
        conversation_text = ""
        for msg in messages_to_summarize:
            role = "Student" if isinstance(msg, HumanMessage) else "Bot"
            conversation_text += f"{role}: {msg.content}\n"

        if existing_summary:
            prompt = (
                f"You are summarizing a university chatbot conversation.\n\n"
                f"EXISTING SUMMARY:\n{existing_summary}\n\n"
                f"NEW CONVERSATION TO ADD:\n{conversation_text}\n\n"
                f"Write an updated summary combining both. "
                f"Keep it under 150 words. "
                f"Focus on: topics asked, important facts mentioned. "
                f"Write in third person."
            )
        else:
            prompt = (
                f"You are summarizing a university chatbot conversation.\n\n"
                f"CONVERSATION:\n{conversation_text}\n\n"
                f"Write a concise summary under 100 words. "
                f"Focus on: topics asked, important facts mentioned. "
                f"Write in third person."
            )

        try:
            response    = llm.invoke([HumanMessage(content=prompt)])
            new_summary = response.content.strip()

            self.save_summary(session_id, new_summary)
            self.save(session_id, recent_messages)

            logger.info(
                f"Summary generated for {session_id}: "
                f"{new_summary[:60]}..."
            )

        except Exception as e:
            logger.error(f"Summarization failed for {session_id}: {e}")
            # Silent fail — system keeps working with old messages

    # ──────────────────────────────────────────────────────────
    #  Utility
    # ──────────────────────────────────────────────────────────

    def get_session_count(self) -> int:
        """Count active sessions. Used in Streamlit sidebar stats."""
        if not self.using_valkey:
            # Count unique session IDs from in-memory store
            ids = set()
            for key in self._memory.keys():
                session_id = key.split(":")[0]
                ids.add(session_id)
            return len(ids)

        try:
            # Count all session message keys (each session = one key)
            keys = self.client.keys("session:*:messages")
            return len(keys)
        except Exception:
            return 0

    def get_all_session_ids(self) -> list:
        """Return all active session IDs. Useful for admin dashboard."""
        if not self.using_valkey:
            ids = set()
            for key in self._memory.keys():
                ids.add(key.split(":")[0])
            return list(ids)

        try:
            keys = self.client.keys("session:*:messages")
            # Extract session_id from "session:{id}:messages"
            return [k.split(":")[1] for k in keys]
        except Exception:
            return []

    # ──────────────────────────────────────────────────────────
    #  Session Metadata — title, timestamps (for sidebar history)
    # ──────────────────────────────────────────────────────────

    def _meta_key(self, session_id: str) -> str:
        """Key for storing session metadata (title, timestamps)."""
        return f"session:{session_id}:meta"

    def save_session_meta(self, session_id: str, title: str) -> None:
        """
        Save session metadata (title + timestamps).

        Called when the FIRST user message is sent so the session
        gets a human-readable name in the sidebar history panel.

        Args:
            session_id: Unique session identifier
            title:      First user message (truncated to 50 chars)
        """
        import time as _time
        now = _time.time()

        if not self.using_valkey:
            key = f"{session_id}:meta"
            existing = self._memory.get(key)
            if existing:
                existing["last_active"] = now
                self._memory[key] = existing
            else:
                self._memory[key] = {
                    "session_id":  session_id,
                    "title":       title[:50],
                    "created_at":  now,
                    "last_active": now,
                }
            return

        try:
            meta_key = self._meta_key(session_id)
            existing_raw = self.client.get(meta_key)
            if existing_raw:
                existing = json.loads(existing_raw)
                existing["last_active"] = now
                self.client.set(meta_key, json.dumps(existing), ex=SESSION_TTL_SECONDS)
            else:
                meta = {
                    "session_id":  session_id,
                    "title":       title[:50],
                    "created_at":  now,
                    "last_active": now,
                }
                self.client.set(meta_key, json.dumps(meta), ex=SESSION_TTL_SECONDS)
        except Exception as e:
            logger.error(f"Failed to save session meta for {session_id}: {e}")

    def update_session_last_active(self, session_id: str) -> None:
        """
        Update last_active timestamp for a session.
        Called after every chat response to keep the sidebar sorted correctly.
        """
        import time as _time
        now = _time.time()

        if not self.using_valkey:
            key = f"{session_id}:meta"
            if key in self._memory:
                self._memory[key]["last_active"] = now
            return

        try:
            meta_key = self._meta_key(session_id)
            existing_raw = self.client.get(meta_key)
            if existing_raw:
                existing = json.loads(existing_raw)
                existing["last_active"] = now
                self.client.set(meta_key, json.dumps(existing), ex=SESSION_TTL_SECONDS)
        except Exception as e:
            logger.error(f"Failed to update last_active for {session_id}: {e}")

    def get_all_sessions_meta(self) -> list:
        """
        Return metadata for ALL active sessions, sorted by last_active descending.

        Returns:
            List of dicts: [{session_id, title, created_at, last_active}, ...]
            Most recent session first.
        """
        if not self.using_valkey:
            results = []
            for key, val in self._memory.items():
                if key.endswith(":meta"):
                    results.append(val)
            return sorted(results, key=lambda x: x.get("last_active", 0), reverse=True)

        try:
            keys = self.client.keys("session:*:meta")
            sessions = []
            for key in keys:
                raw = self.client.get(key)
                if raw:
                    try:
                        sessions.append(json.loads(raw))
                    except Exception:
                        pass
            return sorted(sessions, key=lambda x: x.get("last_active", 0), reverse=True)
        except Exception as e:
            logger.error(f"Failed to list all sessions: {e}")
            return []

    def delete_session_meta(self, session_id: str) -> None:
        """Delete session metadata (called alongside delete())."""
        if not self.using_valkey:
            self._memory.pop(f"{session_id}:meta", None)
            return
        try:
            self.client.delete(self._meta_key(session_id))
        except Exception as e:
            logger.error(f"Failed to delete session meta for {session_id}: {e}")