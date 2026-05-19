# # ═══════════════════════════════════════════════════════════════
# #  session_store.py  —  Persistent Session Storage  (NEW Phase 4)
# # ═══════════════════════════════════════════════════════════════
# #
# #  WHAT THIS FILE DOES:
# #  Manages conversation history — saving it to Redis so it
# #  survives server restarts.
# #
# #  PHASE 3 PROBLEM:
# #  self.sessions = {}   ← Python dict, lives in RAM only.
# #  Server restarts → dict is gone → students lose all history.
# #
# #  PHASE 4 SOLUTION:
# #  SessionStore saves every conversation to Redis (a database).
# #  Redis writes to disk automatically → data survives forever.
# #
# #  FALLBACK BEHAVIOUR:
# #  If Redis is not installed or not running, SessionStore
# #  automatically falls back to an in-memory dict (same as Phase 3).
# #  The chatbot still works — it just loses persistence on restart.
# #  This makes it easy to develop without Redis running.
# #
# #  KEY CONCEPTS:
# #  - Redis stores key → value pairs (like a Python dict on disk)
# #  - We store: "session:student_ali_123" → "[{role:user, content:...}, ...]"
# #  - Values must be JSON strings (Redis only stores text)
# #  - TTL (Time To Live) = auto-delete old sessions after 24 hours
# #
# # ═══════════════════════════════════════════════════════════════

# import json
# import logging
# from typing import List, Dict, Any, Optional

# from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

# import config

# logger = logging.getLogger(__name__)

# # Redis key prefix — all our session keys look like "session:ali123"
# # This namespaces our keys so they don't clash with other apps
# KEY_PREFIX = "session:"


# class SessionStore:
#     """
#     Stores and loads conversation history per session ID.

#     Usage:
#         store = SessionStore()

#         # Save history
#         store.save("ali_123", [HumanMessage("hello"), AIMessage("Hi!")])

#         # Load history (returns [] if session not found)
#         history = store.load("ali_123")

#         # Delete a session
#         store.delete("ali_123")
#     """

#     def __init__(self):
#         self._redis  = None   # Redis client (None if Redis not available)
#         self._memory = {}     # Fallback in-memory dict

#         self._connect_redis()

#     def _connect_redis(self):
#         """
#         Try to connect to Redis.
#         If connection fails, log a warning and use memory fallback.
#         """
#         try:
#             import redis

#             # decode_responses=True means Redis returns Python strings, not bytes
#             client = redis.from_url(
#                 config.REDIS_URL,
#                 decode_responses=True,
#                 socket_connect_timeout=2,  # don't wait long if Redis is down
#             )

#             # Test the connection
#             client.ping()

#             self._redis = client
#             logger.info(f"Redis connected at {config.REDIS_URL}")
#             print(f"Redis connected ✓  ({config.REDIS_URL})")

#         except Exception as e:
#             self._redis = None
#             logger.warning(
#                 f"Redis not available ({e}). "
#                 f"Using in-memory sessions (data lost on restart)."
#             )
#             print(f"Redis not available — using in-memory sessions.")
#             print(f"To enable persistence: install Redis and run it on localhost:6379")

#     @property
#     def using_redis(self) -> bool:
#         """True if Redis is connected, False if using memory fallback."""
#         return self._redis is not None

#     # ─────────────────────────────────────────────────────────
#     #  PUBLIC METHODS
#     # ─────────────────────────────────────────────────────────

#     def save(self, session_id: str, messages: List[BaseMessage]) -> None:
#         """
#         Save conversation history for a session.

#         Converts LangChain message objects → JSON string → Redis.

#         Why JSON?
#         Redis only stores text strings. Python objects (like HumanMessage)
#         must be converted to text first. json.dumps() does this.
#         json.loads() converts back when we load.
#         """
#         if not messages:
#             return

#         # Convert message objects to simple dicts
#         # [HumanMessage("hello"), AIMessage("hi")] →
#         # [{"role": "human", "content": "hello"}, {"role": "ai", "content": "hi"}]
#         serialized = _messages_to_dicts(messages)

#         # Convert to JSON string
#         json_str = json.dumps(serialized, ensure_ascii=False)

#         if self._redis:
#             try:
#                 ttl_seconds = config.SESSION_TTL_HOURS * 3600
#                 # SET key value EX seconds
#                 self._redis.set(
#                     KEY_PREFIX + session_id,
#                     json_str,
#                     ex=ttl_seconds,   # auto-delete after TTL hours
#                 )
#             except Exception as e:
#                 logger.error(f"Redis save failed for {session_id}: {e}")
#                 # Fall through to memory fallback
#                 self._memory[session_id] = serialized
#         else:
#             self._memory[session_id] = serialized

#     def load(self, session_id: str) -> List[BaseMessage]:
#         """
#         Load conversation history for a session.

#         Reads from Redis → converts JSON string → LangChain message objects.
#         Returns [] if session does not exist (new student, first message).
#         """
#         if self._redis:
#             try:
#                 json_str = self._redis.get(KEY_PREFIX + session_id)
#                 if json_str is None:
#                     return []   # new session
#                 serialized = json.loads(json_str)
#                 return _dicts_to_messages(serialized)
#             except Exception as e:
#                 logger.error(f"Redis load failed for {session_id}: {e}")
#                 return []
#         else:
#             serialized = self._memory.get(session_id, [])
#             return _dicts_to_messages(serialized)

#     def delete(self, session_id: str) -> None:
#         """Delete a session (clear conversation history)."""
#         if self._redis:
#             try:
#                 self._redis.delete(KEY_PREFIX + session_id)
#             except Exception as e:
#                 logger.error(f"Redis delete failed for {session_id}: {e}")
#         else:
#             self._memory.pop(session_id, None)

#     def extend_ttl(self, session_id: str) -> None:
#         """
#         Reset the TTL timer for a session.
#         Call this on every message so active students don't get logged out.
#         """
#         if self._redis:
#             try:
#                 ttl_seconds = config.SESSION_TTL_HOURS * 3600
#                 self._redis.expire(KEY_PREFIX + session_id, ttl_seconds)
#             except Exception:
#                 pass

#     def get_session_count(self) -> int:
#         """How many active sessions are stored?"""
#         if self._redis:
#             try:
#                 # KEYS pattern returns all keys matching the pattern
#                 return len(self._redis.keys(KEY_PREFIX + "*"))
#             except Exception:
#                 return 0
#         return len(self._memory)

#     def trim_history(self, messages: List[BaseMessage],
#                      max_messages: int = 20) -> List[BaseMessage]:
#         """
#         Keep only the last N messages to prevent sessions growing forever.
#         20 messages = 10 conversation turns.
#         """
#         if len(messages) > max_messages:
#             return messages[-max_messages:]
#         return messages


# # ─────────────────────────────────────────────────────────────
# #  SERIALISATION HELPERS
# # ─────────────────────────────────────────────────────────────

# def _messages_to_dicts(messages: List[BaseMessage]) -> List[Dict]:
#     """
#     Convert LangChain message objects to plain dicts for JSON storage.

#     HumanMessage("hello")  → {"role": "human",     "content": "hello"}
#     AIMessage("hi there")  → {"role": "assistant",  "content": "hi there"}
#     """
#     result = []
#     for msg in messages:
#         if isinstance(msg, HumanMessage):
#             result.append({"role": "human",     "content": msg.content})
#         elif isinstance(msg, AIMessage):
#             result.append({"role": "assistant", "content": msg.content})
#         # Skip any other message types (SystemMessage etc.)
#     return result


# def _dicts_to_messages(dicts: List[Dict]) -> List[BaseMessage]:
#     """
#     Convert plain dicts back to LangChain message objects.

#     {"role": "human",     "content": "hello"} → HumanMessage("hello")
#     {"role": "assistant", "content": "hi"}    → AIMessage("hi")
#     """
#     result = []
#     for d in dicts:
#         role    = d.get("role", "")
#         content = d.get("content", "")
#         if role == "human":
#             result.append(HumanMessage(content=content))
#         elif role == "assistant":
#             result.append(AIMessage(content=content))
#     return result



# ═══════════════════════════════════════════════════════════════
#  session_store.py  —  Phase 5  (D5: Summarization Memory)
# ═══════════════════════════════════════════════════════════════
#
#  WHAT CHANGED FROM PHASE 4:
#  ────────────────────────────
#  Phase 4 stored raw messages only. Problem:
#    - trim_history(max=20) silently throws away older messages
#    - Student says "my name is Arif" in message 1
#    - After 20 more messages, that fact is gone forever
#    - Bot treats every long conversation as if it just started
#
#  Phase 5 adds Summarization Memory:
#    - Every 10 messages → LLM summarizes them into 2-3 sentences
#    - Summary stored separately in Redis under "summary:session_id"
#    - When building LLM prompt: summary + last 6 messages sent
#    - Student's name, program, questions — all remembered cheaply
#
#  COST IMPACT:
#    - Summary generation: 1 LLM call every 10 messages (~once per session)
#    - Each query: +150 tokens (the summary text) — about 5% more cost
#    - Much cheaper than sending full 40-message history every time
#
#  ARCHITECTURE:
#  ┌─────────────────────────────────────────────────────────┐
#  │  Redis stores TWO things per session:                   │
#  │                                                         │
#  │  "session:ali123"  → last 20 raw messages (as before)  │
#  │  "summary:ali123"  → summary of earlier messages (NEW) │
#  │                                                         │
#  │  LLM prompt receives:                                   │
#  │    [Summary of what happened before]  ← compressed     │
#  │    [Last 6 raw messages]              ← recent detail  │
#  └─────────────────────────────────────────────────────────┘
#
# ═══════════════════════════════════════════════════════════════

import json
import logging
from typing import List, Dict, Any, Optional

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

import config

logger = logging.getLogger(__name__)

# Redis key prefixes
# WHY TWO PREFIXES: We store messages and summaries separately.
# Keeping them in separate keys makes loading/saving independent.
# We can update summary without touching messages and vice versa.
KEY_PREFIX     = "session:"   # raw messages   → "session:ali123"
SUMMARY_PREFIX = "summary:"   # text summary   → "summary:ali123"

# HOW MANY MESSAGES TRIGGER A SUMMARY?
# Every 10 messages (5 turns) we compress older history into a summary.
# Why 10? Enough context to summarize meaningfully, not so many that
# we wait too long before compressing. Tune this if needed.
SUMMARIZE_EVERY_N = 10


class SessionStore:
    """
    Stores conversation history and generates summaries for long sessions.

    TWO RESPONSIBILITIES:
    1. Message storage  — same as Phase 4 (Redis with memory fallback)
    2. Summary memory   — NEW in Phase 5 (compresses old messages)

    USAGE PATTERN IN workflow.py:
        # Load history + summary at start of every chat() call
        history = store.load(session_id)
        summary = store.load_summary(session_id)

        # After getting response — save messages
        store.save(session_id, updated_messages)

        # Check if summary needs updating
        await store.maybe_summarize(session_id, updated_messages, llm)
    """

    def __init__(self):
        self._redis  = None   # Redis client (None if Redis not available)
        self._memory = {}     # Fallback in-memory dict for messages
        self._summaries = {}  # Fallback in-memory dict for summaries

        self._connect_redis()

    def _connect_redis(self):
        """
        Try to connect to Redis.
        If connection fails, log warning and use memory fallback.
        Both messages AND summaries fall back to memory dicts.
        """
        try:
            import redis

            client = redis.from_url(
                config.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            client.ping()

            self._redis = client
            logger.info(f"Redis connected at {config.REDIS_URL}")
            print(f"Redis connected ✓  ({config.REDIS_URL})")

        except Exception as e:
            self._redis = None
            logger.warning(
                f"Redis not available ({e}). "
                f"Using in-memory sessions (data lost on restart)."
            )
            print(f"Redis not available — using in-memory sessions.")
            print(f"To enable persistence: install Redis and run it on localhost:6379")

    @property
    def using_redis(self) -> bool:
        """True if Redis is connected, False if using memory fallback."""
        return self._redis is not None

    # ═══════════════════════════════════════════════════════════
    #  MESSAGE STORAGE — same as Phase 4, no changes
    # ═══════════════════════════════════════════════════════════

    def save(self, session_id: str, messages: List[BaseMessage]) -> None:
        """
        Save conversation messages for a session.
        Identical to Phase 4 — no changes here.

        Converts LangChain message objects → JSON string → Redis/memory.
        TTL auto-deletes inactive sessions after config.SESSION_TTL_HOURS.
        """
        if not messages:
            return

        serialized = _messages_to_dicts(messages)
        json_str   = json.dumps(serialized, ensure_ascii=False)

        if self._redis:
            try:
                ttl_seconds = config.SESSION_TTL_HOURS * 3600
                self._redis.set(KEY_PREFIX + session_id, json_str, ex=ttl_seconds)
            except Exception as e:
                logger.error(f"Redis save failed for {session_id}: {e}")
                self._memory[session_id] = serialized
        else:
            self._memory[session_id] = serialized

    def load(self, session_id: str) -> List[BaseMessage]:
        """
        Load conversation messages for a session.
        Returns [] if session does not exist (new student).
        Identical to Phase 4 — no changes here.
        """
        if self._redis:
            try:
                json_str = self._redis.get(KEY_PREFIX + session_id)
                if json_str is None:
                    return []
                return _dicts_to_messages(json.loads(json_str))
            except Exception as e:
                logger.error(f"Redis load failed for {session_id}: {e}")
                return []
        else:
            return _dicts_to_messages(self._memory.get(session_id, []))

    def delete(self, session_id: str) -> None:
        """
        Delete a session — clears BOTH messages and summary.
        WHY BOTH: If student clears history, summary should also reset.
        Otherwise old summary would contaminate a fresh conversation.
        """
        if self._redis:
            try:
                self._redis.delete(KEY_PREFIX + session_id)
                self._redis.delete(SUMMARY_PREFIX + session_id)  # NEW: also delete summary
            except Exception as e:
                logger.error(f"Redis delete failed for {session_id}: {e}")
        else:
            self._memory.pop(session_id, None)
            self._summaries.pop(session_id, None)   # NEW: also delete summary

    def extend_ttl(self, session_id: str) -> None:
        """
        Reset TTL for both messages and summary keys.
        WHY BOTH: Summary TTL must match messages TTL.
        If summary expires before messages, bot loses context.
        """
        if self._redis:
            try:
                ttl_seconds = config.SESSION_TTL_HOURS * 3600
                self._redis.expire(KEY_PREFIX + session_id, ttl_seconds)
                self._redis.expire(SUMMARY_PREFIX + session_id, ttl_seconds)  # NEW
            except Exception:
                pass

    def get_session_count(self) -> int:
        """How many active sessions are stored?"""
        if self._redis:
            try:
                return len(self._redis.keys(KEY_PREFIX + "*"))
            except Exception:
                return 0
        return len(self._memory)

    def trim_history(self, messages: List[BaseMessage],
                     max_messages: int = 20) -> List[BaseMessage]:
        """
        Keep only the last N messages to prevent sessions growing forever.

        IMPORTANT PHASE 5 NOTE:
        We still trim to 20 messages. BUT now before trimming, the
        maybe_summarize() method compresses older messages into a summary.
        So nothing is truly lost — older context lives in the summary.
        trim_history() just keeps Redis storage lean.
        """
        if len(messages) > max_messages:
            return messages[-max_messages:]
        return messages

    # ═══════════════════════════════════════════════════════════
    #  SUMMARY STORAGE — NEW in Phase 5
    # ═══════════════════════════════════════════════════════════

    def save_summary(self, session_id: str, summary_text: str) -> None:
        """
        Save a text summary for a session.

        WHAT IS STORED:
        A plain text string like:
        "Student asked about CS admission requirements and fees.
         They mentioned they completed FSc with 75% marks.
         Also asked about attendance policy and hostel availability."

        This is stored under "summary:session_id" in Redis.
        The LLM generates this summary — we just store it here.
        """
        if not summary_text:
            return

        if self._redis:
            try:
                ttl_seconds = config.SESSION_TTL_HOURS * 3600
                self._redis.set(
                    SUMMARY_PREFIX + session_id,
                    summary_text,
                    ex=ttl_seconds,
                )
            except Exception as e:
                logger.error(f"Redis summary save failed for {session_id}: {e}")
                self._summaries[session_id] = summary_text
        else:
            self._summaries[session_id] = summary_text

    def load_summary(self, session_id: str) -> str:
        """
        Load the summary for a session.
        Returns "" (empty string) if no summary exists yet.

        WHEN IS SUMMARY EMPTY?
        - New student, first conversation (no summary generated yet)
        - Conversation has fewer than SUMMARIZE_EVERY_N messages
        - Student cleared their history

        WHAT HAPPENS WITH EMPTY SUMMARY?
        workflow.py checks: if summary is empty, only send raw messages.
        No error — the system degrades gracefully to Phase 4 behavior.
        """
        if self._redis:
            try:
                summary = self._redis.get(SUMMARY_PREFIX + session_id)
                return summary if summary else ""
            except Exception as e:
                logger.error(f"Redis summary load failed for {session_id}: {e}")
                return ""
        else:
            return self._summaries.get(session_id, "")

    def maybe_summarize(self, session_id: str,
                        messages: List[BaseMessage],
                        llm) -> None:
        """
        THE CORE OF D5 MEMORY.

        WHEN DOES THIS RUN?
        After every chat() call in workflow.py, AFTER saving messages.
        It checks: "Do we have enough new messages to summarize?"

        HOW IT DECIDES TO SUMMARIZE:
        If total messages > SUMMARIZE_EVERY_N (10):
          → Take the older messages (everything except last 6)
          → Ask LLM to summarize them
          → Save summary to Redis
          → Keep only last 6 messages in history
            (older ones are now captured in summary)

        WHY KEEP LAST 6 AFTER SUMMARIZING?
        The most recent messages are most relevant for the current query.
        We keep them raw (not summarized) for full detail.
        Older messages go into the summary for compressed context.

        EXAMPLE:
          Messages 1-10 exist. We have summary of msgs 1-4.
          New situation: 10 messages > SUMMARIZE_EVERY_N
          Action:
            → Summarize messages 1-4 (everything except last 6)
            → New summary = old summary + summary of msgs 1-4
            → Save new combined summary
            → Trim stored messages to last 6 only
        """
        if len(messages) <= SUMMARIZE_EVERY_N:
            return  # Not enough messages yet — nothing to do

        # Split: older messages get summarized, recent ones stay raw
        # WHY keep last 6? These are most relevant to current conversation.
        # Summarizing very recent messages loses too much detail.
        messages_to_summarize = messages[:-6]   # everything except last 6
        recent_messages       = messages[-6:]   # last 6 stay as-is

        # Load existing summary (may be empty for first summarization)
        existing_summary = self.load_summary(session_id)

        # Build the summarization prompt
        # WHY include existing summary: We are UPDATING it, not replacing.
        # New summary = compress(old summary + new messages to summarize)
        # This way the summary grows richer over time, not just replaced.
        conversation_text = ""
        for msg in messages_to_summarize:
            role = "Student" if isinstance(msg, HumanMessage) else "Bot"
            conversation_text += f"{role}: {msg.content}\n"

        if existing_summary:
            prompt = (
                f"You are summarizing a university chatbot conversation.\n\n"
                f"EXISTING SUMMARY (from earlier in the conversation):\n"
                f"{existing_summary}\n\n"
                f"NEW CONVERSATION TO ADD TO SUMMARY:\n"
                f"{conversation_text}\n\n"
                f"Write an updated summary combining both. "
                f"Keep it under 150 words. "
                f"Focus on: student's name if mentioned, their program/year, "
                f"what topics they asked about, any important facts they shared. "
                f"Write in third person. Example: 'Student asked about CS fees...'"
            )
        else:
            prompt = (
                f"You are summarizing a university chatbot conversation.\n\n"
                f"CONVERSATION:\n"
                f"{conversation_text}\n\n"
                f"Write a concise summary under 100 words. "
                f"Focus on: student's name if mentioned, their program/year, "
                f"what topics they asked about, any important facts they shared. "
                f"Write in third person. Example: 'Student asked about CS fees...'"
            )

        try:
            # ONE LLM call to generate summary
            # This is the only extra API cost in D5 — runs once every 10 messages
            response     = llm.invoke([HumanMessage(content=prompt)])
            new_summary  = response.content.strip()

            # Save the new summary
            self.save_summary(session_id, new_summary)

            # Trim stored messages to only recent ones
            # WHY: Older messages are now captured in the summary.
            # Keeping them in Redis would waste space and add noise to prompts.
            self.save(session_id, recent_messages)

            logger.info(f"Summary generated for session {session_id}: {new_summary[:80]}...")

        except Exception as e:
            # If summarization fails — no problem. System keeps working.
            # Old messages stay in Redis. Next call will try again.
            logger.error(f"Summarization failed for {session_id}: {e}")


# ─────────────────────────────────────────────────────────────
#  SERIALISATION HELPERS — unchanged from Phase 4
# ─────────────────────────────────────────────────────────────

def _messages_to_dicts(messages: List[BaseMessage]) -> List[Dict]:
    """
    Convert LangChain message objects to plain dicts for JSON storage.
    HumanMessage("hello") → {"role": "human", "content": "hello"}
    AIMessage("hi")       → {"role": "assistant", "content": "hi"}
    """
    result = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            result.append({"role": "human",     "content": msg.content})
        elif isinstance(msg, AIMessage):
            result.append({"role": "assistant", "content": msg.content})
    return result


def _dicts_to_messages(dicts: List[Dict]) -> List[BaseMessage]:
    """
    Convert plain dicts back to LangChain message objects.
    {"role": "human", "content": "hello"} → HumanMessage("hello")
    {"role": "assistant", "content": "hi"} → AIMessage("hi")
    """
    result = []
    for d in dicts:
        role    = d.get("role", "")
        content = d.get("content", "")
        if role == "human":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result