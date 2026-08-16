# ═══════════════════════════════════════════════════════════════
#  graph/workflow.py  —  Phase 6  (Active)
# ═══════════════════════════════════════════════════════════════
#
#  MULTI-AGENT GRAPH FLOW:
#
#  [START]
#     ↓
#  [chitchat_check]  ← instant keyword check, no API cost
#     ↓          ↓
#  [supervisor]  [chitchat_answer] → END
#     ↓
#  LLM classifies → agent_name = policy/faculty/admissions/campus/general
#     ↓
#  [policy_agent | faculty_agent | admissions_agent | campus_agent | general_agent]
#  Each agent: filtered KB search → E2 parent fetch → E3 compress → specialized answer
#     ↓
#  E4 self-reflection check (general + policy agents only)
#     ↓
#  Save to Redis session + maybe_summarize (every 10 messages)
#     ↓
#  END
#
#  WHY MULTI-AGENT:
#  Phase 5's single combined_node caused cross-category noise.
#  Policy questions got faculty context mixed in. Faculty questions
#  got policy PDFs. Campus questions could not handle Urdu vocabulary.
#  Phase 6 fix: each specialist searches ONLY its own category.
#
#  Phase history archived in: phases/workflow_phases_1_5_archive.py
# ═══════════════════════════════════════════════════════════════

import os
import sys
import functools
import logging
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from graph.state import BotState
from graph.nodes import (
    chitchat_check_node,
    chitchat_answer_node,
)
from graph.Supervisor import supervisor_node
from graph.Agents import (
    policy_agent_node,
    faculty_agent_node,
    _get_faculty_docs,
    admissions_agent_node,
    campus_agent_node,
    fee_agent_node,
    general_agent_node,
    GENERAL_PROMPT_WITH_GRAPH,
)

from graph_rag.graph_store    import GraphStore
from graph_rag.graph_searcher import GraphSearcher
from knowledge_base import KnowledgeBase
from valkey_store import ValkeySessionStore
import config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  GRAPH BUILDER
# ══════════════════════════════════════════════════════════════

def build_graph(kb: KnowledgeBase, checkpointer=None, graph_searcher=None):
    """
    Build and compile the Phase 6 multi-agent LangGraph.

    Routing logic:
      chitchat_check → "chitchat" → chitchat_answer → END
      chitchat_check → "real"     → supervisor → agent → END

      supervisor sets agent_name via LLM classification.
      Conditional edge routes to correct worker agent.
      Each worker agent does its own KB search + answer.
    """
    graph = StateGraph(BotState)

    # ── Register nodes ────────────────────────────────────────
    graph.add_node("chitchat_check",  chitchat_check_node)
    graph.add_node("chitchat_answer", chitchat_answer_node)
    graph.add_node("supervisor",      supervisor_node)

    # Worker agents — kb injected via functools.partial
    graph.add_node("policy_agent",     functools.partial(policy_agent_node,     kb=kb))
    graph.add_node("faculty_agent",    functools.partial(faculty_agent_node,    kb=kb,
                                                         graph_searcher=graph_searcher))
    graph.add_node("admissions_agent", functools.partial(admissions_agent_node, kb=kb))
    graph.add_node("campus_agent",     functools.partial(campus_agent_node,     kb=kb))
    graph.add_node("fee_agent",        functools.partial(fee_agent_node,        kb=kb))
    graph.add_node("general_agent",    functools.partial(general_agent_node,    kb=kb,
                                                         graph_searcher=graph_searcher))

    # ── Edges ─────────────────────────────────────────────────
    graph.add_edge(START, "chitchat_check")

    # Chitchat → instant answer; real question → supervisor
    graph.add_conditional_edges(
        "chitchat_check",
        lambda state: "chitchat" if state.get("intent") == "chitchat" else "real",
        {"chitchat": "chitchat_answer", "real": "supervisor"},
    )

    graph.add_edge("chitchat_answer", END)

    # Supervisor → route to correct agent
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state.get("agent_name", "general"),
        {
            "policy":     "policy_agent",
            "faculty":    "faculty_agent",
            "admissions": "admissions_agent",
            "campus":     "campus_agent",
            "fees":       "fee_agent",
            "general":    "general_agent",
        },
    )

    # All agents → END
    graph.add_edge("policy_agent",     END)
    graph.add_edge("faculty_agent",    END)
    graph.add_edge("admissions_agent", END)
    graph.add_edge("campus_agent",     END)
    graph.add_edge("fee_agent",        END)
    graph.add_edge("general_agent",    END)

    if checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        print("Graph compiled with checkpointing ✓")
    else:
        compiled = graph.compile()
        print("Graph compiled (no checkpointing) ✓")

    return compiled


# ══════════════════════════════════════════════════════════════
#  UNICHATBOT — Main interface used by app.py and api.py
# ══════════════════════════════════════════════════════════════

class UniChatbot:
    """
    Phase 6 chatbot — multi-agent architecture.

    Students get answers from a specialist agent, not a generic
    combined node. Each agent knows its domain deeply.

    Features:
      - 5 specialist agents (policy/faculty/admissions/campus/general)
      - Supervisor LLM classification for routing
      - Chitchat handled instantly with no API cost
      - Redis-backed sessions with in-memory fallback
      - Conversation summarization every 10 messages
      - E1 GraphRAG for relationship queries
      - E4 CRAG + self-reflection for hallucination prevention
    """

    def __init__(self, kb: KnowledgeBase):
        self.store = ValkeySessionStore(host="localhost", port=6379)
        self.kb          = kb
        self._last_docs  = []
        self.checkpointer = MemorySaver()

        # ── E1 GraphRAG setup ─────────────────────────────────
        self.graph_searcher = None
        graph_store = GraphStore()
        if graph_store.exists():
            try:
                g, summaries, entities = graph_store.load()
                self.graph_searcher = GraphSearcher(g, summaries, entities)
                print("  GraphRAG      : Enabled ✓")
            except Exception as e:
                print(f"  GraphRAG      : Disabled ({e})")
        else:
            print("  GraphRAG      : Disabled (run build_graph_rag.py to enable)")

        # ── Build graph ───────────────────────────────────────
        self.graph = build_graph(kb, self.checkpointer, self.graph_searcher)

        # LLM instance for summarization (maybe_summarize)
        from graph.nodes import get_llm
        self.llm = get_llm()

        print(f"\n{'='*52}")
        print(f"  {config.BOT_NAME} — Phase 6 ready!")
        print(f"  University   : {config.UNIVERSITY_NAME}")
        print(f"  Sessions     : {'Redis' if self.store.using_redis else 'In-memory'}")
        print(f"  Memory       : Summarization (every 10 messages)")
        print(f"  Agents       : 6 (policy/faculty/admissions/campus/fees/general)")
        print(f"{'='*52}\n")

    # ──────────────────────────────────────────────────────────
    #  chat()  —  non-streaming (used by api.py /chat endpoint)
    # ──────────────────────────────────────────────────────────

    def chat(self, user_message: str, session_id: str = "default") -> dict:
        """
        Send a message and get a complete response dict.
        Used by the FastAPI /chat endpoint.

        Returns:
            {"answer": str, "docs": list, "agent_used": str}
        """
        history = self.store.load(session_id)
        summary = self.store.load_summary(session_id)

        initial_state = {
            "messages":          history,
            "user_query":        user_message,
            "intent":            "",
            "docs":              [],
            "context":           "",
            "answer":            "",
            "session_id":        session_id,
            "retrieval_quality": "",
            "summary":           summary,
            "agent_name":        "",
        }

        final_state = self.graph.invoke(
            initial_state, 
            config={"configurable": {"thread_id": session_id}}
        )

        # Save updated history
        updated_messages = final_state.get("messages", [])
        updated_messages = self.store.trim_history(updated_messages, max_messages=20)
        # self.store.save(session_id, updated_messages)
        # self.store.extend_ttl(session_id)
        # self.store.maybe_summarize(session_id, updated_messages, self.llm)

        # return {
        #     "answer":     final_state.get("answer", "Sorry, I couldn't generate a response."),
        #     "docs":       final_state.get("docs", []),
        #     "agent_used": final_state.get("agent_name", "unknown"),
        # }
        self.store.save(session_id, updated_messages)
        self.store.extend_ttl(session_id)

        # Run summarizer in background — user does NOT wait for this
        import threading
        threading.Thread(
            target=self.store.maybe_summarize,
            args=(session_id, updated_messages, self.llm),
            daemon=True
        ).start()

        return {
            "answer":     final_state.get("answer", "Sorry, I couldn't generate a response."),
            "docs":       final_state.get("docs", []),
            "agent_used": final_state.get("agent_name", "unknown"),
        }

    # ──────────────────────────────────────────────────────────
    #  stream_chat()  —  streaming (used by app.py Streamlit UI)
    # ──────────────────────────────────────────────────────────

    def stream_chat(self, user_message: str, session_id: str = "default"):
        """
        Streams LLM response token by token for Streamlit.

        Flow:
          1. Chitchat check    → instant yield if greeting/memory
          2. Supervisor        → LLM classifies agent category
          3. Get docs          → agent-specific KB search strategy
          4. E4 CRAG grading   → drop irrelevant docs before LLM
          5. Build + stream prompt → yield tokens as they arrive
          6. E4 self-reflection → append disclaimer if not grounded
          7. Save + summarize  → persist session to Redis
        """
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, AIMessage
        from graph.nodes import chitchat_check_node, chitchat_answer_node
        from graph.Supervisor import supervisor_node
        from graph.Agents import (
            _get_llm, _search_filtered, _format_context,
            _grade_quality, _build_history, _extract_answer,
            FALLBACK_ANSWER,
            POLICY_PROMPT, FACULTY_PROMPT, ADMISSIONS_PROMPT,
            CAMPUS_PROMPT, FEE_PROMPT, GENERAL_PROMPT, REWRITE_PROMPT,
        )

        history = self.store.load(session_id)
        summary = self.store.load_summary(session_id)

        state = {
            "messages":          history,
            "user_query":        user_message,
            "intent":            "",
            "docs":              [],
            "context":           "",
            "answer":            "",
            "session_id":        session_id,
            "retrieval_quality": "",
            "summary":           summary,
            "agent_name":        "",
        }

        # ── Step 1: Chitchat check ────────────────────────────
        state.update(chitchat_check_node(state))

        if state["intent"] == "chitchat":
            result = chitchat_answer_node(state)
            answer = result.get("answer", "")
            for word in answer.split(" "):
                yield word + " "
            updated = history + [HumanMessage(content=user_message), AIMessage(content=answer)]
            self.store.save(session_id, self.store.trim_history(updated))
            self._last_docs = []
            return

        # # ── Step 2: Supervisor classifies ────────────────────
        # state.update(supervisor_node(state))
        # agent_name = state.get("agent_name", "general")
        # ── Out of scope — clean rejection, no KB search ─────
        # ── Step 2: Classifier + HyDE in parallel ────────────
        # ── Step 2: Classifier + HyDE in parallel ────────────
        from concurrent.futures import ThreadPoolExecutor

        query = user_message  # define here for use in Step 2 and Step 3

        def _run_classifier():
            return supervisor_node(state)

        def _run_hyde(q):
            expanded = self.kb._expand_query(q)  # ALWAYS expand first
            if not config.ENABLE_HYDE:
                return [expanded]  # return expanded not raw
            return self.kb._engineer_query(expanded)

        with ThreadPoolExecutor(max_workers=2) as executor:
            clf_future  = executor.submit(_run_classifier)
            hyde_future = executor.submit(_run_hyde, query)
            classifier_result      = clf_future.result()
            pre_engineered_queries = hyde_future.result()

        state.update(classifier_result)
        agent_name = state.get("agent_name", "general")

        # supervisor_node resolves follow-ups ("What are its main goals?") against
        # the history and returns the resolved question. Pick it up here so
        # retrieval searches for the topic and not just the pronoun — the
        # classifier alone seeing it would stop the out-of-scope rejection while
        # still retrieving on a bare "what are its main goals", which is the kind
        # of half-fix that reads as working and answers badly.
        #
        # The expansion computed in parallel above was built from the raw message,
        # so it has to be redone on the resolved one. That is a dictionary lookup,
        # not an API call, since ENABLE_HYDE is off.
        _resolved_query = state.get("user_query", user_message)
        if _resolved_query != user_message:
            logger.info(f"follow-up resolved: {user_message!r} → {_resolved_query!r}")
            pre_engineered_queries = _run_hyde(_resolved_query)

        if agent_name == "out_of_scope":
            pre_engineered_queries = None
            rejection = (
                "I'm UOLI Assistant Bot and can only help with "
                "University of Loralai questions — admissions, "
                "policies, faculty, fees, and campus services. "
                "How can I help you with something UOLI-related?"
            )
            for word in rejection.split(" "):
                yield word + " "
            updated = history + [
                HumanMessage(content=user_message),
                AIMessage(content=rejection)
            ]
            self.store.save(session_id, self.store.trim_history(updated))
            self._last_docs = []
            return
        # ── Step 3: Get docs based on agent ──────────────────
        # Retrieve on the resolved question (topic carried in from the previous
        # turn for a follow-up), while history and the displayed message below
        # still use the raw user_message the student actually typed.
        query = state.get("user_query", user_message)
        # None = show the LLM every retrieved doc, matching _format_context's
        # default and the chat() path used by eval_ragas.py. The faculty branch
        # below still overrides this for its list-query paths.
        _context_max_docs = None

        # ── Step 3: Get docs based on agent ──────────────────
        #
        # RETRIEVAL IS NOT IMPLEMENTED HERE.
        #
        # It used to be — this block held its own category filters and k values,
        # copied from the agent nodes and then left behind when those were
        # corrected. What the two paths were actually doing:
        #
        #   agent       chat() / eval_ragas.py                stream_chat() / Streamlit
        #   fees        admissions+overview+general+offices    fees+admissions+general
        #   campus      general+overview+offices               campus+general+students+overview
        #   admissions  admissions+policy+general+overview     admissions+courses+students(+...)
        #   policy      ["policy"], unfiltered for attendance  always unfiltered
        #
        # "fees", "campus", "courses" and "students" match ZERO rows in this DB.
        # So a student asking on Streamlit what percentage receive financial aid
        # searched fees+admissions+general, and that figure lives on /about-us
        # under "overview" — unreachable. eval_ragas.py asked the same question
        # through chat(), reached it, and scored it. The eval was not measuring
        # the product, which is the one thing an eval has to do.
        #
        # Both paths now call one function. See Agents.retrieve_for_agent for
        # what is and is not shared, and why.
        from graph.Agents import retrieve_for_agent

        docs, _context_max_docs, allow_graph = retrieve_for_agent(
            self.kb, agent_name, query,
            engineered_queries=pre_engineered_queries)

        # Graph context is only assembled for the general agent, and only when
        # the retrieval path allows it — the department-listing branch of
        # _general_docs returns allow_graph=False because it already answered
        # from the KB. This mirrors general_agent_node exactly.
        graph_context_section = ""
        if agent_name == "general":
            if allow_graph and self.graph_searcher is not None:
                if self.graph_searcher.is_graph_question(query):
                    graph_ctx = self.graph_searcher.search(query, max_results=3)
                    if graph_ctx:
                        graph_context_section = (
                            "GRAPH KNOWLEDGE (entity relationships and "
                            "community summaries):\n" + graph_ctx
                        )

        if agent_name == "policy":
            prompt_template = POLICY_PROMPT
            format_args = dict(university_name=config.UNIVERSITY_NAME)
        elif agent_name == "faculty":
            prompt_template = FACULTY_PROMPT
            format_args = dict(university_name=config.UNIVERSITY_NAME)
        elif agent_name == "admissions":
            prompt_template = ADMISSIONS_PROMPT
            format_args = dict(university_name=config.UNIVERSITY_NAME)
        elif agent_name == "campus":
            prompt_template = CAMPUS_PROMPT
            format_args = dict(university_name=config.UNIVERSITY_NAME)
        elif agent_name == "fees":
            prompt_template = FEE_PROMPT
            format_args = dict(university_name=config.UNIVERSITY_NAME)
        else:  # general
            prompt_template = GENERAL_PROMPT_WITH_GRAPH
            format_args = dict(
                bot_name              = config.BOT_NAME,
                university_name       = config.UNIVERSITY_NAME,
                graph_context_section = graph_context_section,
            )


        self._last_docs = docs
        # ── Step 4: E4 CRAG — drop irrelevant docs ───────────
        if docs:
            _grades = self.kb._grade_documents(query, docs)
            if _grades["all_irrelevant"]:
                if agent_name != "general":
                    # KB is authoritative for non-general agents → use fallback
                    self._last_docs = []
                    print(f"[E4-CRAG] All docs irrelevant → using fallback.")
                    for word in FALLBACK_ANSWER.split(" "):
                        yield word + " "
                    updated = history + [
                        HumanMessage(content=user_message),
                        AIMessage(content=FALLBACK_ANSWER)
                    ]
                    self.store.save(session_id, self.store.trim_history(updated))
                    return
                else:
                    # General agent — GraphRAG may still have useful context
                    print(f"[E4-CRAG] All KB docs irrelevant for general query "
                          f"— allowing GraphRAG to contribute.")
                    # If GraphRAG also has nothing → clean rejection
                    if not graph_context_section:
                        _rejection = (
                            "I'm UOLI Assistant Bot and can only help with "
                            "University of Loralai questions — admissions, "
                            "policies, faculty, fees, and campus services. "
                            "How can I help you with something UOLI-related?"
                        )
                        for word in _rejection.split(" "):
                            yield word + " "
                        updated = history + [
                            HumanMessage(content=user_message),
                            AIMessage(content=_rejection)
                        ]
                        self.store.save(
                            session_id,
                            self.store.trim_history(updated)
                        )
                        self._last_docs = []
                        return
            else:
                # For multi-intent fanout results, docs come from several
                # independent searches (one per intent). CRAG should NOT
                # filter them individually — it only knows the combined
                # query, so it grades harassment docs as "less relevant"
                # when attendance dominates, wrongly removing them.
                # Signal: > 2 unique source URLs = multi-intent fanout.
                _unique_sources = len(set(
                    d.metadata.get("source", "") for d in docs
                ))
                if _unique_sources <= 2:
                    # Single-intent result — safe to filter individual docs.
                    docs = _grades["relevant"]
                    self._last_docs = docs
                # else: multi-intent result — keep ALL docs; only the
                # all_irrelevant check above applies as the safety net.


        # ── Step 4b: Fallback if zero docs ───────────────────
        if not docs:
            for word in FALLBACK_ANSWER.split(" "):
                yield word + " "
            updated = history + [HumanMessage(content=user_message),
                                  AIMessage(content=FALLBACK_ANSWER)]
            self.store.save(session_id, self.store.trim_history(updated))
            return

        # ── Step 5: Build prompt and stream ──────────────────
        prompt = prompt_template.format(
            **format_args,
            context  = _format_context(docs, max_docs=_context_max_docs),
            history  = _build_history(state),
            question = query,
        )

        llm = ChatOpenAI(
            model          = config.LLM_MODEL,
            temperature    = config.LLM_TEMPERATURE,
            openai_api_key = config.OPENAI_API_KEY,
            streaming      = True,
        )

        # Stream directly — agent prompts don't use ANSWER: prefix
        full_response = ""
        for chunk in llm.stream([HumanMessage(content=prompt)]):
            token          = chunk.content
            full_response += token
            yield token

        # ── Step 6: E4 Self-reflection — grounded? ───────────
        # Runs only for general and policy agents (highest hallucination risk).
        # Runs AFTER streaming — student already sees answer.
        # If not grounded → appends a small honest disclaimer.
        if agent_name in ("general", "policy") and docs:
            _reflection = self.kb._self_reflect(query, docs, full_response)
            if (not _reflection["grounded"] 
                    and not _reflection["complete"]
                    and _reflection["issues"].lower() != "none"):
                _disclaimer = (
                    "\n\n*Note: Please verify this information directly "
                    "with the university, as some details may need confirmation.*"
                )
                yield _disclaimer
                full_response += _disclaimer

        # ── Step 7: Save and summarize ────────────────────────
        answer  = _extract_answer(full_response)
        updated = history + [HumanMessage(content=user_message), AIMessage(content=answer)]
        updated = self.store.trim_history(updated)
        # self.store.save(session_id, updated)
        # self.store.extend_ttl(session_id)
        # self.store.maybe_summarize(session_id, updated, self.llm)
        self.store.save(session_id, updated)
        self.store.extend_ttl(session_id)

        # Run summarizer in background — user does NOT wait for this
        import threading
        threading.Thread(
            target=self.store.maybe_summarize,
            args=(session_id, updated, self.llm),
            daemon=True
        ).start()
    # ──────────────────────────────────────────────────────────
    #  Utility methods
    # ──────────────────────────────────────────────────────────

    def clear_history(self, session_id: str = "default") -> None:
        """Delete conversation history AND summary for a session."""
        self.store.delete(session_id)
        logger.info(f"History and summary cleared for session: {session_id}")

    def get_session_count(self) -> int:
        """How many active sessions are stored?"""
        return self.store.get_session_count()