# ═══════════════════════════════════════════════════════════════
#  graph/nodes.py  —  Phase 6  (Active)
# ═══════════════════════════════════════════════════════════════
#
#  WHAT THIS FILE CONTAINS:
#    - get_llm()              : shared LLM factory
#    - chitchat_check_node()  : instant keyword classifier (no API)
#    - chitchat_answer_node() : instant pre-written responses (no API)
#    - retrieval_node()       : hybrid KB search (used by legacy paths)
#    - grade_documents_node() : CRAG quality gate (used by legacy paths)
#    - combined_node()        : legacy single-agent answer node
#    - fallback_answer_node() : honest "I don't know" response
#    - make_university_tools(): tool definitions (not used in Phase 6 graph)
#
#  NOTE: chitchat_check_node and chitchat_answer_node are used by
#  the Phase 6 graph (workflow.py). The other nodes are legacy —
#  kept for compatibility but not called by the active graph.
#
#  Phase history archived in: phases/nodes_phases_3_4_5_archive.py
# ═══════════════════════════════════════════════════════════════

import json
from typing import Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool

import config
from knowledge_base import KnowledgeBase


# ════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS (D2)
#  Kept for compatibility. Not used in Phase 6 multi-agent graph.
#  Phase 6 agents do direct KB search instead of tool-calling.
# ════════════════════════════════════════════════════════════════

def make_university_tools(kb):
    """
    WHAT: Creates tool functions that have access to the KB.
    WHY:  Tools need the knowledge base to search.
          We pass kb in at runtime so tools are not hardcoded.
    NOTE: Not used in Phase 6 graph — kept for compatibility.
    """

    @tool
    def search_university_kb(query: str, category: str = "all") -> str:
        """
        Search the University of Loralai knowledge base.
        Use for ANY question about the university including:
        faculty, departments, HOD, staff contacts, admissions,
        fees, courses, schedules, university policies, offices,
        bus routes, transport, library, hostel, or any general
        university information.

        Args:
            query:    What you are searching for.
            category: Content filter. Use:
                      "faculty"    for staff, HOD, department info
                      "policy"     for rules, regulations, conduct
                      "admissions" for admission requirements
                      "offices"    for office information
                      "all"        when unsure (searches everything)
        """
        if category != "all":
            results = kb.db.similarity_search(
                query,
                k=10,
                filter={"category": {"$eq": category}}
            )
        else:
            results = kb.search(query)

        if not results:
            return "No relevant information found in university records."

        output = []
        for i, doc in enumerate(results[:5], 1):
            source = doc.metadata.get("source", "unknown")
            if source.startswith("http"):
                source = source.replace("https://uoli.edu.pk/", "")
            output.append(f"[Source: {source}]\n{doc.page_content}")

        return "\n---\n".join(output)

    @tool
    def get_contact_info(person_or_office: str) -> str:
        """
        Get contact information for a specific person or office
        at the University of Loralai.
        Use when the student asks for email, phone, or office
        location of any university staff member or department.

        Args:
            person_or_office: Name of person or office to find.
                             Examples: "Ismail Khan", "Registrar",
                             "HOD Computer Science", "ORIC office"
        """
        query = f"contact email phone {person_or_office}"
        results = kb.db.similarity_search(
            query,
            k=5,
            filter={"contains_person": {"$eq": True}}
        )

        if not results:
            return f"No contact information found for {person_or_office}."

        output = []
        for doc in results[:3]:
            output.append(doc.page_content)

        return "\n---\n".join(output)

    @tool
    def get_policy_details(policy_topic: str) -> str:
        """
        Get specific policy rules and regulations from university
        official policy documents.
        Use when student asks about rules, punishments, procedures,
        attendance requirements, disciplinary actions, anti-drug
        policy, harassment policy, or any official university rule.

        Args:
            policy_topic: The specific policy to look up.
                         Examples: "attendance 75%", "cheating exam",
                         "readmission", "smoking", "weapons campus"
        """
        results = kb.db.similarity_search(
            policy_topic,
            k=8,
            filter={"category": {"$eq": "policy"}}
        )

        if not results:
            return f"No policy information found for: {policy_topic}"

        output = []
        for doc in results[:5]:
            source = doc.metadata.get("source", "unknown")
            output.append(f"[{source}]\n{doc.page_content}")

        return "\n---\n".join(output)

    return [
        search_university_kb,
        get_contact_info,
        get_policy_details,
    ]


# ──────────────────────────────────────────────────────────────
#  LLM HELPER
# ──────────────────────────────────────────────────────────────

def get_llm():
    """Shared LLM factory. Used by workflow.py for summarization."""
    return ChatOpenAI(
        model=config.LLM_MODEL,
        temperature=config.LLM_TEMPERATURE,
        openai_api_key=config.OPENAI_API_KEY,
    )


# ══════════════════════════════════════════════════════════════
#  NODE 1 — CHITCHAT CHECK  (no API call — instant)
# ══════════════════════════════════════════════════════════════

def chitchat_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Checks if the message is a simple greeting, personal statement,
    memory question, or non-university topic.
    Does this with plain Python — NO API call. Takes < 1 millisecond.

    If it IS chitchat → sets intent="chitchat" → router skips
    retrieval and goes straight to chitchat_answer_node.

    If it is NOT chitchat → sets intent="" → router goes to supervisor.

    WHY split(): We check whole WORDS not substrings.
      "hi" in "vehicles" → True (WRONG substring match)
      "hi" in ["what","is","vehicles"] → False (CORRECT word match)
    """
    query = state["user_query"].lower().strip()
    if not query:
        return {"intent": "chitchat"}

    GREETINGS = [
        "hello", "hi", "hey", "salam", "assalam",
        "good morning", "good evening", "good afternoon",
    ]
    THANKS    = ["thank", "thanks", "shukriya", "jazakallah", "شکریہ"]
    FAREWELLS = ["bye", "goodbye", "allah hafiz", "khuda hafiz"]
    ABOUT_BOT = ["who are you", "what is your name", "your name",
                 "how are you", "kaisa ho"]

    # Non-university questions — handle gracefully without KB search
    NOT_UNIVERSITY = [
        "what is 2+2", "what is 2 + 2", "what is 1+1", "what is 1 + 1",
        "2+2", "2 + 2", "1+1", "times 7", "times 5", "times 3",
        "divided by", "multiply", "solve this", "what is the answer to",
        "who invented", "history of the world", "population of",
        "currency of", "capital of pakistan", "capital of india",
        "tell me a joke", "write a poem", "sing a song",
        "calculate", "what is the weather", "tell me a joke", "sing a song",
        "write a poem", "what time is it", "what is today", "translate",
        "capital of", "who invented", "what is the meaning of",
    ]

    # Personal statements — no KB match, acknowledge and move on
    PERSONAL = ["my name is", "i am in", "i study", "i'm in",
                "i am a student", "call me"]

    # Memory questions — answer from session history, not KB
    MEMORY_Q = [
        "what did i", "do you remember", "i told you",
        "as i said", "i mentioned", "what did i tell",
        "what is my name", "my name", "what am i",
        "who am i", "what year am i",
        "what type of student", "what kind of student",
        "which university am i", "what did i say",
        "summarize", "summary of", "what have i asked",
        "what did we discuss", "recap",
    ]

    query_words = query.split()

    is_chitchat = (
        any(w in query_words for w in GREETINGS)  or
        any(w in query for w in THANKS)           or
        any(w in query_words for w in FAREWELLS)  or
        any(w in query for w in ABOUT_BOT)        or
        any(w in query for w in PERSONAL)         or
        any(w in query for w in MEMORY_Q)         or
        any(w in query for w in NOT_UNIVERSITY)
    )
    return {"intent": "chitchat" if is_chitchat else ""}


# ══════════════════════════════════════════════════════════════
#  NODE 2a — CHITCHAT ANSWER  (no API call — instant)
# ══════════════════════════════════════════════════════════════

def chitchat_answer_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns instant responses for greetings, personal statements,
    and memory questions. No API call. No KB search. Instant.
    """
    query       = state["user_query"].lower().strip()
    query_words = query.split()

    # ── Greetings ─────────────────────────────────────────────
    if any(w in query_words for w in ["hello", "hi", "hey", "salam", "assalam"]) or \
       any(w in query for w in ["good morning", "good evening", "good afternoon"]):
        answer = (
            f"Hello! Welcome to {config.UNIVERSITY_NAME}. "
            f"I'm {config.BOT_NAME}, your university assistant 😊\n\n"
            f"I can help you with:\n"
            f"• Admissions & requirements\n"
            f"• Fees & scholarships\n"
            f"• Courses & programs\n"
            f"• Schedules & exams\n"
            f"• University policies\n\n"
            f"What would you like to know?"
        )

    # ── Thanks ────────────────────────────────────────────────
    elif any(w in query for w in ["thank", "thanks", "shukriya", "jazakallah"]):
        answer = "You're very welcome! Feel free to ask anything else. 😊"

    # ── Farewells ─────────────────────────────────────────────
    elif any(w in query for w in ["bye", "goodbye", "allah hafiz", "khuda hafiz"]):
        answer = "Goodbye! Best of luck with your studies. Come back anytime! 👋"

    # ── How are you ───────────────────────────────────────────
    elif any(w in query for w in ["how are you", "kaisa ho"]):
        answer = "I'm doing great, thanks! Ready to help with any university questions."

    # ── About bot ─────────────────────────────────────────────
    elif any(w in query for w in ["who are you", "your name", "what is your name"]):
        answer = (
            f"I'm {config.BOT_NAME}, the AI assistant for {config.UNIVERSITY_NAME}. "
            f"I'm here to answer your questions about admissions, fees, courses, and policies."
        )

    # ── Non-university questions ───────────────────────────────
    elif any(w in query for w in [
        "what is 2+2", "what is 2 + 2", "calculate",
        "what is the weather", "tell me a joke", "sing a song",
        "write a poem", "what time is it", "what is today",
        "translate", "capital of", "who invented",
    ]):
        answer = (
            f"I'm {config.BOT_NAME}, a university assistant for "
            f"{config.UNIVERSITY_NAME}. I can only help with "
            f"university-related questions such as admissions, "
            f"policies, faculty, and campus services.\n\n"
            f"Is there anything about UOLI I can help you with?"
        )

    # ── Personal statements ────────────────────────────────────
    elif any(w in query for w in ["my name is", "i am in", "i study", "i'm in",
                                   "my program", "call me", "i am a student",
                                   "my semester"]):
        answer = (
            f"Got it, I've noted: \"{state['user_query']}\"\n"
            f"Feel free to ask me anything about {config.UNIVERSITY_NAME}!"
        )

    # ── Memory questions — scan history ───────────────────────
    elif any(w in query for w in [
        "what did i", "do you remember", "i told you",
        "as i said", "i mentioned", "what did i tell",
        "what is my name", "my name", "what am i",
        "who am i", "what year am i", "what program",
        "what type of student", "what kind of student",
        "which university am i", "what did i say",
    ]):
        summary  = state.get("summary", "")
        messages = state.get("messages", [])

        if summary:
            answer = (
                f"Based on our conversation:\n{summary}\n\n"
                f"Is there anything specific you'd like me to clarify?"
            )
        elif messages:
            personal_info = []
            for msg in messages:
                if isinstance(msg, HumanMessage):
                    content = msg.content.lower()
                    if any(w in content for w in ["my name is", "i am in", "i study",
                                                   "i'm in", "my program", "call me"]):
                        personal_info.append(msg.content)
            if personal_info:
                answer = (
                    "From our conversation, you told me:\n" +
                    "\n".join(f"• {info}" for info in personal_info)
                )
            else:
                answer = (
                    "I don't have any personal information from you yet. "
                    "Feel free to tell me your name, program, or year!"
                )
        else:
            answer = "This seems to be the start of our conversation. Feel free to introduce yourself!"

    # ── Summarize conversation ─────────────────────────────────
    elif any(w in query for w in [
        "summarize", "summary of", "what have i asked", "recap", "what did we discuss",
    ]):
        summary  = state.get("summary", "")
        messages = state.get("messages", [])

        if summary:
            answer = f"Here's a summary of our conversation:\n\n{summary}"
        elif messages:
            topics = []
            for msg in messages:
                if isinstance(msg, HumanMessage):
                    topics.append(f"• {msg.content}")
            answer = (
                "Here's what you asked me today:\n\n" + "\n".join(topics)
            ) if topics else "Our conversation just started — nothing to summarize yet!"
        else:
            answer = "Our conversation just started — nothing to summarize yet!"

    # ── Default fallback ──────────────────────────────────────
    else:
        answer = (
            f"I'm {config.BOT_NAME}! How can I help you with university information today?"
        )

    # Return empty messages — LangGraph operator.add would duplicate them.
    # stream_chat() in workflow.py builds: history + [Human, AI] and saves.
    # If we also return messages here, they get appended TWICE. (D3 fix)
    return {
        "answer":   answer,
        "messages": [],   # ← D3 FIX: empty — workflow.py manages persistence
    }


# ══════════════════════════════════════════════════════════════
#  NODE 2b — RETRIEVAL  (legacy — not called by Phase 6 graph)
#  Phase 6 agents do their own targeted search inside Agents.py.
#  Kept for compatibility with any external callers.
# ══════════════════════════════════════════════════════════════

def retrieval_node(state: Dict[str, Any], kb: KnowledgeBase) -> Dict[str, Any]:
    """
    Searches the knowledge base for relevant documents.
    Legacy node — not used in Phase 6 multi-agent graph.
    Phase 6 agents do their own search inside Agents.py.
    """
    query = state["user_query"]
    docs  = kb.search(query)

    if not docs:
        return {
            "docs":    [],
            "context": "No relevant documents found in the knowledge base.",
        }

    parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "University Document")
        parts.append(f"[Source {i}: {source}]\n{doc.page_content}")

    context = "\n---\n".join(parts)
    return {"docs": docs, "context": context}


# ══════════════════════════════════════════════════════════════
#  NODE 2c — GRADE DOCUMENTS  (legacy — not called by Phase 6 graph)
# ══════════════════════════════════════════════════════════════

def grade_documents_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    CRAG quality gate — legacy node, not used in Phase 6 graph.
    Phase 6 uses kb._grade_documents() inline in stream_chat().
    """
    docs = state.get("docs", [])
    if not docs:
        return {"retrieval_quality": "empty"}
    return {"retrieval_quality": "good"}


# ══════════════════════════════════════════════════════════════
#  NODE 3 — COMBINED NODE  (legacy — not called by Phase 6 graph)
#  Phase 6 uses 5 specialist agents instead.
# ══════════════════════════════════════════════════════════════

COMBINED_PROMPT = """You are {bot_name}, a professional and helpful AI assistant for {university_name}.
Your goal is to provide accurate, specific, and structured information to students.

UNIVERSITY DOCUMENTS:
{context}

PREVIOUS CONVERSATION:
{history}

STUDENT'S QUESTION:
{question}
{caution_note}
YOUR TASK:
1. Identify the intent of the question.
2. Search the UNIVERSITY DOCUMENTS above for the answer.
3. If the answer is present, provide a clear and structured response.
4. If the answer is NOT present, say "I don't have that information. Please contact the university office directly."

STRICT RULES:
- Answer ONLY using the provided documents. Do NOT use your general knowledge to answer.
- VOCABULARY BRIDGING: Bridge vocabulary gaps intelligently — do not refuse just because the student's exact word is not in the document.
- Names of officials (VC, HOD, Dean) are often found in signature blocks. Look carefully.
- If asked about "faculty" members, prioritize listing names of teachers/lecturers.
- CRITICAL: If a user asks about a specific named person, only answer if that EXACT name appears in the documents.
- Be professional, polite, and use bullet points for lists.

RESPONSE FORMAT (Plain Text Only):
INTENT: <admissions/fees/courses/schedule/policy/general/other>
ANSWER: <your detailed answer>"""


def combined_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Legacy single-agent answer node. Not used in Phase 6 graph.
    Phase 6 uses 5 specialist agents (policy/faculty/admissions/campus/general).
    """
    llm   = get_llm()
    query = state["user_query"]

    messages = state.get("messages", [])
    recent   = messages[-6:] if len(messages) > 6 else messages
    summary  = state.get("summary", "")

    history = ""
    if summary:
        history += f"[EARLIER CONVERSATION SUMMARY]\n{summary}\n\n"
        history += "[RECENT MESSAGES]\n"
    for msg in recent:
        role     = "Student" if isinstance(msg, HumanMessage) else "UniBot"
        history += f"{role}: {msg.content}\n"
    if not history:
        history = "This is the start of the conversation."

    quality = state.get("retrieval_quality", "good")
    caution_note = (
        "\nCAUTION: Retrieved documents have low relevance scores. "
        "If the answer is not clearly supported by the documents, "
        "say so honestly rather than guessing.\n"
    ) if quality == "partial" else ""

    prompt = COMBINED_PROMPT.format(
        bot_name        = config.BOT_NAME,
        university_name = config.UNIVERSITY_NAME,
        context         = state.get("context", "No documents found."),
        history         = history,
        question        = query,
        caution_note    = caution_note,
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    raw      = response.content.strip()
    intent, answer = _parse_combined_response(raw)

    new_messages = [
        HumanMessage(content=query),
        AIMessage(content=answer),
    ]
    return {
        "intent":   intent,
        "answer":   answer,
        "messages": new_messages,
    }


def _parse_combined_response(raw: str) -> tuple:
    """
    Parses LLM response into (intent, answer).
    Handles: plain text INTENT:/ANSWER:, JSON object, or plain fallback.
    """
    import json as _json

    valid_intents = {"admissions", "fees", "courses", "schedule",
                     "policy", "general", "other"}
    intent = "general"
    answer = raw.strip()
    cleaned = raw.strip()

    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break

    if cleaned.startswith("{"):
        try:
            data = _json.loads(cleaned)
            for k in data:
                if k.upper() == "INTENT":
                    intent = str(data[k]).strip().lower()
                if k.upper() == "ANSWER":
                    answer = str(data[k]).strip()
            if answer and answer != raw.strip():
                if intent not in valid_intents:
                    intent = "general"
                return intent, answer
        except Exception:
            pass

    lines = raw.strip().split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("INTENT:"):
            intent = stripped.split(":", 1)[1].strip().lower()
        elif stripped.upper().startswith("ANSWER:"):
            first  = stripped.split(":", 1)[1].strip()
            rest   = lines[i + 1:]
            answer = (first + "\n" + "\n".join(rest)).strip() if rest else first
            break

    if intent not in valid_intents:
        intent = "general"

    return intent, answer


# ══════════════════════════════════════════════════════════════
#  NODE 4 — FALLBACK ANSWER  (legacy — not called by Phase 6 graph)
#  Phase 6 uses FALLBACK_ANSWER constant in Agents.py directly.
# ══════════════════════════════════════════════════════════════

def fallback_answer_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns honest "I don't know" response.
    Legacy node — Phase 6 uses FALLBACK_ANSWER from Agents.py inline.
    """
    answer = (
        "I don't have enough information in the university knowledge base "
        "to answer this question accurately.\n\n"
        "Please contact the university directly:\n"
        "• 📞 Phone: (082) 2441174\n"
        "• 🌐 Website: uoli.edu.pk\n"
        "• 📍 Visit: University of Loralai, Loralai, Balochistan"
    )
    new_messages = [
        HumanMessage(content=state["user_query"]),
        AIMessage(content=answer),
    ]
    return {
        "answer":            answer,
        "messages":          new_messages,
        "retrieval_quality": state.get("retrieval_quality", "empty"),
    }