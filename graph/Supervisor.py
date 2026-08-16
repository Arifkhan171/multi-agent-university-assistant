# ═══════════════════════════════════════════════════════════════
#  graph/supervisor.py  —  Phase 6  (D6: Multi-Agent Supervisor)
# ═══════════════════════════════════════════════════════════════
#
#  WHAT THIS FILE DOES:
#  The supervisor is the brain of the multi-agent system.
#  It reads every student question and decides which specialist
#  worker agent should handle it.
#
#  TWO-STEP ROUTING LOGIC:
#
#  Step 1 — Instant keyword check (zero cost, <1ms):
#    chitchat_check_node in nodes.py already handles:
#    greetings, thanks, farewells, memory questions,
#    personal statements.
#    If chitchat → goes to chitchat_answer_node (not here).
#
#  Step 2 — LLM classification (this file, only for real questions):
#    Uses GPT-4o-mini (cheapest model, just for classification).
#    One prompt → one word answer → routes to correct agent.
#    Cost: ~50-80 tokens per query. Very cheap.
#
#  WHY LLM CLASSIFICATION INSTEAD OF MORE KEYWORDS?
#    Keywords fail on: Roman Urdu ("garee ka waqt"),
#    Pashto-English mix, unexpected phrasing.
#    LLM understands meaning, not just exact words.
#    "when does the bus leave" → campus
#    "garee kab aati hai"     → campus  (LLM understands this)
#    "mujhe admission chahiye" → admissions (LLM understands this)
#
#  COST IMPACT:
#    Current: 1 LLM call per query (answer)
#    With supervisor: 1 cheap classify call + 1 answer call
#    Extra cost: ~10-15% per real question
#    Chitchat/memory: still 0 cost (keyword check in nodes.py)
#
# ═══════════════════════════════════════════════════════════════

from typing import Dict, Any
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
import config


# ── Routing classifier client ──────────────────────────────────
# Built once and reused. Previously a new ChatOpenAI was constructed inside
# supervisor_node() on every query, so each request opened a fresh connection
# pool and paid a TCP + TLS handshake before routing could even start — on the
# critical path of every single answer, since retrieval cannot begin until the
# route is known.
#
# max_tokens is capped because the classifier returns exactly one label word.
# 8 tokens comfortably fits the longest ("out_of_scope") while preventing the
# model from ever running on into an explanation, which it occasionally did and
# which cost latency for output that supervisor_node() discards anyway (only
# raw.split()[0] is read).
_CLASSIFIER = None


def _get_classifier():
    global _CLASSIFIER
    if _CLASSIFIER is None:
        _CLASSIFIER = ChatOpenAI(
            model          = "gpt-4o-mini",
            temperature    = 0,        # zero temp = deterministic routing
            max_tokens     = 8,
            openai_api_key = config.OPENAI_API_KEY,
        )
    return _CLASSIFIER


# ── Follow-up resolution ───────────────────────────────────────
#
# THE BUG THIS FIXES
# "What are its main goals?" asked straight after a question about UOLI was
# answered with the out-of-scope rejection. The classifier only ever saw the raw
# message, and a message made entirely of a pronoun and a common noun contains
# no University of Loralai signal at all — so "out_of_scope" was, on the evidence
# it was given, the correct call. workflow.py then short-circuits on that verdict
# and returns the rejection without ever searching the knowledge base.
#
# The conversation history was already loaded and sitting in state["messages"];
# nothing was reading it. So this costs no extra API call and adds no latency:
# it is pure string work on data already in memory.
#
# WHY APPEND RATHER THAN REWRITE
# Appending the previous question keeps every word the student actually typed,
# so a false positive only adds context and can never destroy the real question.
# A rewrite would have to decide what to throw away, and getting that wrong turns
# a good question into a bad one. Given the asymmetry — a miss costs a total
# refusal, a false positive costs a few extra tokens — appending is the safer
# trade by a wide margin.

# Words that are pronouns wherever they appear — if one of these is in the
# sentence, something outside the sentence is being referred to.
_REFERENT_RE = re.compile(
    r"\b(it|it's|its|they|them|their|theirs"
    r"|he|him|his|she|her|hers)\b", re.I)

# Demonstratives are only referents when they stand alone. "this year" and
# "that department" are ordinary determiners inside a self-contained question —
# matching them appended the previous topic to questions that never needed it
# (measured on "What is the fee structure for BS programs at the University of
# Loralai this year?"). So require the word to end the clause or to be followed
# by a verb, which is what "what does that mean" and "is this correct" look like.
_DEMONSTRATIVE_RE = re.compile(
    r"\b(this|that|these|those|there)\b"
    r"\s*(?:[?.!,]|$|(?:is|are|was|were|do|does|did|mean|means|work|works)\b)",
    re.I)

# Fragments that are plainly continuations rather than questions in their own
# right — "not in table" was a real example from testing.
#
# Bare interrogatives are matched only when they are the whole message. An
# earlier version accepted any query opening with "how" or "why", which swallowed
# "How many departments are there in UOLI?" — a complete question that needs no
# help from the previous turn.
_FRAGMENT_RE = re.compile(
    r"^\s*(?:"
    r"(?:and|also|what about|how about|more|tell me more|explain|elaborate"
    r"|details?|in detail|not in|in table|as table|as a table|in a table"
    r"|give me more|continue|go on|same|ok now)\b"
    r"|(?:why|how|when|where|who|which|what)\s*(?:not|else|so)?\s*[?.!]*\s*$"
    r")", re.I)

# Below this a demonstrative is almost certainly doing referring work rather than
# determining a noun — "is this correct?" has no other topic available.
_SHORT_QUERY_CHARS = 30
_DEMONSTRATIVE_ANY_RE = re.compile(r"\b(this|that|these|those|there)\b", re.I)

# Beyond this a message is a question in its own right, not a fragment leaning
# on the previous turn. Also makes the function naturally idempotent: once the
# topic has been appended the result is far longer than this and is returned
# untouched on any second call.
_MAX_FOLLOWUP_CHARS = 90


def resolve_followup(query: str, messages) -> str:
    """
    Append the previous question's topic when this one cannot stand alone.

    Returns the query unchanged unless it is short AND either carries a bare
    referent or opens like a fragment. Deterministic, no API call.
    """
    text = (query or "").strip()
    if not text or len(text) > _MAX_FOLLOWUP_CHARS or not messages:
        return query

    _short_demonstrative = (len(text) <= _SHORT_QUERY_CHARS
                            and _DEMONSTRATIVE_ANY_RE.search(text))
    if not (_REFERENT_RE.search(text)
            or _DEMONSTRATIVE_RE.search(text)
            or _short_demonstrative
            or _FRAGMENT_RE.match(text)):
        return query

    # The most recent thing the student asked is the antecedent.
    previous = ""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            previous = (message.content or "").strip()
            if previous:
                break
    if not previous:
        return query

    # Already carrying its own topic — nothing to add.
    if previous.lower() in text.lower():
        return query

    return f"{text} (in the context of: {previous[:120]})"


# ── Classification prompt ──────────────────────────────────────
# WHY so specific: Vague categories cause wrong routing.
# Each category has concrete examples so LLM has no ambiguity.
# "Reply ONE word only" prevents LLM from explaining itself.

CLASSIFY_PROMPT = """You are classifying a university student's question for routing.
Choose ONE category. Read ALL definitions carefully before choosing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CATEGORY DEFINITIONS AND EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POLICY — Rules, regulations, punishments, procedures a student must follow.
  ✓ attendance percentage, absence penalty, readmission procedure
  ✓ cheating punishment, exam regulations, probation, CGPA rules
  ✓ semester freeze, drug policy, harassment complaint procedure
  ✓ conduct violations, disciplinary action, weapons policy
  ✓ "what happens if I miss class", "can I improve my grade"
  ✗ NOT org structure, NOT who manages who, NOT department hierarchy

FACULTY — People, contacts, offices, leadership at the university.
  ✓ HOD name, teacher name, lecturer, professor, staff contact
  ✓ VC, Pro-VC, Registrar, Dean, Treasurer, Controller, ORIC Director
  ✓ department email, phone number, office location, extension number
  ✓ list of departments, how many departments, department names
  ✓ person's name alone ("Ismail Khan", "Noor Ul Huda")
  ✓ "email of [name]", "contact of [name]", "number of [name]"
  ✓ leadership messages (VC message, dean message, registrar message)
  ✓ "who is [university person]", "contact of [any office]", "email of [person]"
  ✗ NOT fictional characters — "who is batman", "who is spiderman" → out_of_scope
  ✗ NOT celebrities, politicians, or public figures unrelated to UOLI  ✗ NOT rules, NOT org hierarchy, NOT how university is structured

ADMISSIONS — Joining the university, programs, eligibility, applications.
  ✓ how to apply, required documents, merit criteria, selection process
  ✓ BS/MS/MPhil/PhD programs offered, undergraduate, graduate programs
  ✓ eligibility percentage, application form, fee challan, deadline
  ✓ "can I get admission", "what programs does UOLI offer"
  ✗ NOT existing student rules, NOT faculty contacts

CAMPUS — Physical campus services: transport, library, facilities.
  ✓ bus routes, transport timing, vehicle schedule, bus stops
  ✓ garee, gaadi (Urdu/Pashto for bus/vehicle)
  ✓ library timings, books, reading room, lab facilities
  ✓ "when does the bus leave", "is there transport from [area]"
  ✗ NOT admissions, NOT faculty, NOT university news

FEES — Cost, fee structure, scholarships, financial aid.
  ✓ tuition fee, semester fee, admission fee, hostel fee
  ✓ "how much does BS CS cost", "is there any scholarship"
  ✓ "what are the charges", "fee structure"
  ✓ "what is bs fee", "what is ms fee", "what is phd fee"
  ✓ "bs fee", "ms fee", "processing fee", "challan"
  ✓ "fee for bs", "fee for ms", "cost of program"
  ✗ NOT program eligibility, NOT how to apply
  
GENERAL — University overview, news, events, organizational structure.
  ✓ university history, about UOLI, latest news, events, announcements
  ✓ research activities, MoU, workshops, seminars, ORIC activities
  ✓ ORGANIZATIONAL HIERARCHY: who reports to whom, chain of command
  ✓ management structure, who manages what, university governance
  ✓ how departments connect, relationship between offices
  ✓ who is above [role], what does [office] oversee
  ✓ "tell me about the university", "what is new at UOLI"
  ✓ POPULATION STATISTICS: total students, total faculty count, total programs
  ✓ "how many students are enrolled", "university ki strength kitni hai"
  ✓ "kitne students hain", "total students", "student strength"
  ✓ "how many faculty members does UOLI have" (overall count, not a name)
  ✓ anything that does not clearly fit the 4 categories above

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL DISTINCTIONS — These are commonly confused:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"How do I report harassment?"        → POLICY  (procedure to follow)
"Who reports to whom in university?" → GENERAL (org hierarchy)
"Who report to whom?"               → GENERAL (org hierarchy)
"What is attendance policy and who is HOD of CS?" → FACULTY (mixed queries: person takes priority)
"Tell me about VC and what is fee structure?"     → FACULTY (when person + other topic combined)
"Who is the HOD of CS?"              → FACULTY (person contact)
"Who manages the CS department?"     → GENERAL (org structure)
"What is the attendance rule?"       → POLICY  (student rule)
"Who is in charge of attendance?"    → FACULTY (person contact)
"How are departments structured?"    → GENERAL (org overview)
"What happens if I miss 5 classes?"  → POLICY  (consequence/rule)
"UOLI mein kitne department hain?" → FACULTY (department list question)
"kitne department hain?"           → FACULTY (department list question)
"university ki strength kitni hai?" → GENERAL (student population count)
"kitne students hain UOLI mein?"   → GENERAL (student enrollment count)
"total students kitne hain?"       → GENERAL (population statistics)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━



OUT_OF_SCOPE — Question has nothing to do with University of Loralai.
  ✓ math calculations ("2+2", "what is 5 times 7")
  ✓ general knowledge ("capital of Pakistan", "who invented electricity")
  ✓ random symbols, gibberish, or meaningless input
  ✓ requests the bot cannot fulfill ("book a flight", "tell me a joke")
  ✓ questions about other universities not related to UOLI
  ✗ NOT Urdu/Pashto university questions — those go to correct category
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Question: "{query}"
Reply with ONE word only: policy, faculty, admissions, campus, fees, general, or out_of_scope"""


def supervisor_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve any follow-up, then classify.

    Kept as a thin wrapper around _classify_node so the resolved question is
    returned in the state update rather than mutated in place. The chat() path
    runs through LangGraph, which merges what a node *returns* — an in-place edit
    to the state dict would have reached the classifier and then been silently
    dropped before the agent nodes retrieved, which is the half-fix that would
    have looked like it worked in testing and failed in production.
    """
    raw      = state.get("user_query", "")
    resolved = resolve_followup(raw, state.get("messages") or [])

    if resolved == raw:
        return _classify_node(state)

    result = _classify_node({**state, "user_query": resolved})
    # Hand the resolved question downstream so retrieval sees the topic too.
    result["user_query"] = resolved
    return result


def _classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classifies the student's question and sets agent_name.

    This runs ONLY for real questions — chitchat, greetings,
    and memory questions are already handled by chitchat_check_node
    in nodes.py before this node is reached.

    Uses gpt-4o-mini for classification:
    WHY mini: Classification needs intelligence, not creativity.
              gpt-4o-mini is 10x cheaper than gpt-4o and just
              as accurate for simple classification tasks.
    """
    query = state["user_query"]

    # ── Pre-classification: deterministic rules for high-confidence patterns ──
    # WHY: Some query types consistently fool the LLM because one word
    # (e.g. "report") has multiple meanings. Keyword rules are faster,
    # cheaper, and 100% reliable for these known patterns.
    # LLM classification runs only if no keyword rule matches.

    query_lower = query.lower()

    # Organizational hierarchy → always GENERAL
    # "report to whom" = accountability, not filing a complaint
    _HIERARCHY = [
        "report to whom", "report to who", "who report",
        "refers to whom", "who refers to", "refer to whom",
        "who reports to", "chain of command", "who manages",
        "who oversees", "who is above", "who is below",
        "management structure", "org structure", "organizational",
        "hierarchy", "answers to whom", "accountable to",
        "who controls", "governance structure", "who is in charge of",
        "who heads", "university structure", "how is university",
        "how university is", "university governed",
    ]
    if any(p in query_lower for p in _HIERARCHY):
        return {"agent_name": "general"}
    # Out of scope — deterministic detection before LLM
    import re
    # Math: contains digits with operators
    _is_math = bool(re.search(r'\d+\s*[\+\-\*\/]\s*\d+', query_lower))
    # Symbols: mostly non-alphanumeric characters
    _alpha_ratio = sum(c.isalnum() or c.isspace() for c in query) / max(len(query), 1)
    _is_gibberish = _alpha_ratio < 0.5 and len(query) > 2
    if _is_math or _is_gibberish:
        return {"agent_name": "out_of_scope"}
    
    # Urdu/mixed department list queries → always FACULTY
    _URDU_DEPT_LIST = [
        "kitne department", "tamam department", "saray department",
        "sab department", "kitne dept", "department hain",
        "departments hain", "uoli mein kitne",
    ]
    if any(p in query_lower for p in _URDU_DEPT_LIST):
        return {"agent_name": "faculty"}

    # FIX 5 Gap B: Urdu/Roman-Urdu student population count queries → GENERAL
    # "university ka kitna strength" / "kitne students hain" = enrollment count.
    # The general agent's _general_docs already handles _asked_populations,
    # but only if the query actually reaches it. Without this rule these queries
    # either hit the faculty fast-path (no match → LLM call) or the LLM may
    # misclassify them. Deterministic rule is cheaper and always correct.
    _URDU_POPULATION = [
        "kitni strength", "kitna strength", "student strength",
        "kitne students", "students kitne", "total students",
        "students ki tadad", "students ki tabdad",
        "kitne log", "enrollment kitna", "enrolment kitna",
        "university mein kitne", "uoli mein kitne student",
    ]
    if any(p in query_lower for p in _URDU_POPULATION):
        return {"agent_name": "general"}

    # Harassment complaint procedure → always POLICY
    # These are procedurally specific — must NOT go to general
    _HARASSMENT_PROCEDURE = [
        "how do i report", "how to report harassment",
        "how can i report", "complaint procedure",
        "file a complaint", "report harassment",
        "sexual harassment complaint",
    ]
    if any(p in query_lower for p in _HARASSMENT_PROCEDURE):
        return {"agent_name": "policy"}

    # Readmission → always POLICY
    if "readmission" in query_lower or "re-admission" in query_lower:
        return {"agent_name": "policy"}

    # "tell me about X" → route to faculty
    # WHY: LLM does not know UOLI staff names so it classifies
    # "tell me about shahji ahmed" as out_of_scope.
    # Faculty agent has name detection that searches KB directly.
    # If person not found there → gives clean "not found" message.
    # Better than out_of_scope rejection for a real UOLI dean.
    _TELL_ABOUT = ["tell me about", "tell about", "bata about", "batao mujhe"]
    _CLEAR_OOS = [
        "cricket", "flight", "book flight", "weather",
        "world cup", "politics", "movie", "song", "pakistan army"
    ]
    if any(p in query_lower for p in _TELL_ABOUT):
        if not any(oos in query_lower for oos in _CLEAR_OOS):
            return {"agent_name": "faculty"}

    # ── Single-topic fast path ────────────────────────────────────────────────
    # Skips the routing LLM when the question unambiguously belongs to exactly
    # one topic family. Measured: the LLM call costs 0.59-2.85s (mean 0.87s) and
    # it sat on the critical path of EVERY question — retrieval cannot start
    # until the route is known — because none of the rules above fired for any
    # of the 37 evaluation questions.
    #
    # THE SAFETY RULE, and why it is the whole design: a route decides which
    # categories get searched, so a wrong route silently destroys recall for
    # that question. So this fires only when the query matches ONE family. Two
    # or more matches means the question is genuinely multi-intent ("What is the
    # BS application fee and what documents are required?" is both fees and
    # admissions) and those go to the LLM, which is better at weighing which
    # intent dominates. Zero matches also goes to the LLM — that is how open
    # questions ("What is ORIC and what does it do?"), bare counts and anything
    # phrased unexpectedly still get classified properly, including the
    # Roman-Urdu and Pashto queries this fast path deliberately does not model.
    #
    # Verified against the LLM's own decisions on all 37 evaluation questions:
    # every question this path answers gets the same route the LLM assigned, and
    # the rest fall through untouched. Re-run check_fastpath.py after editing
    # these lists — a rule that disagrees with the LLM is a regression.
    # "email address" is one concept — an email — and must not be read as a
    # location question. Collapsing it first lets "address" mean a postal
    # address in the campus family below without stealing email questions from
    # the faculty family.
    _norm = query_lower.replace("email address", "email") \
                       .replace("e-mail address", "email")

    _families = {
        # A person or an office-holder: identity, email, phone, extension.
        # The faculty agent is the directory agent — it owns the staff-name
        # lookup and the main-contact logic — so contact details route here.
        # NOTE: bare "faculty" is deliberately NOT a keyword. "How many
        # students, faculty, and programs does UoL have?" is a statistics
        # question, not a directory lookup, and the LLM routes it to general.
        "faculty": [
            "who is", "who's", "kaun hai", "vice chancellor", "vice-chancellor",
            "vicechanclor", "pro vc", "pro-vc", "registrar", "treasurer",
            "controller of examination", "hod", "head of the", "head of department",
            "dean", "chairman", "email", "phone", "extension", "contact",
        ],
        # Rules that govern an enrolled student's progression.
        "policy": [
            "attendance", "cgpa", "freeze", "probation", "promotion",
            "supplementary", "re-appear", "reappear", "plagiar", "disciplinary",
            "discipline", "conduct rules", "withdraw", "grading",
        ],
        # Getting in: process, eligibility, documents, programme structure.
        # "application" is listed separately because "apply" is not a substring
        # of it — without it, "What is the BS application fee and what documents
        # are required?" matched only the fees family and was wrongly treated as
        # single-topic. Likewise "documents" is matched on its own: the phrasing
        # "what documents are required" does not contain "documents required".
        "admissions": [
            "admission", "admitted", "apply", "application", "eligibility",
            "entry requirement", "merit", "documents", "how long is",
        ],
        # Money.
        "fees": [
            "fee", "fees", "scholarship", "financial aid", "stipend",
            "tuition", "challan", "cost of",
        ],
        # Physical campus, location and student services.
        # "address" belongs here — a postal address is a location question, and
        # the campus profile searches general/overview/offices, which is exactly
        # where the contact and about pages live. The normalisation above is what
        # makes this safe: without it, "What is the main email address of ...?"
        # would match both faculty and campus and be deferred as ambiguous.
        "campus": [
            "library", "bus", "transport", "hostel", "cafeteria", "canteen",
            "sports", "gym", "laboratory", "facilities", "wifi", "wi-fi",
            "medical", "mosque", "timings", "address", "located", "location",
        ],
    }
    _matched = [name for name, kws in _families.items()
                if any(kw in _norm for kw in kws)]
    if len(_matched) == 1:
        return {"agent_name": _matched[0]}

    # Use cheap model — classification only needs understanding,
    # not generation quality
    llm = _get_classifier()

    prompt   = CLASSIFY_PROMPT.format(query=query)
    response = llm.invoke([HumanMessage(content=prompt)])
    raw      = response.content.strip().lower()

    # Validate — LLM should return one of these 6 words
    valid = {"policy", "faculty", "admissions", "campus", "fees", "general", "out_of_scope"}

    # Extract just the first word in case LLM adds extra text
    first_word = raw.split()[0] if raw else "general"
    agent_name = first_word if first_word in valid else "general"

    return {"agent_name": agent_name}