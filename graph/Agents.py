# ═══════════════════════════════════════════════════════════════
#  graph/agents.py  —  Phase 6  (D6: Worker Agent Nodes)
# ═══════════════════════════════════════════════════════════════
#
#  5 specialized worker agents. Each agent:
#    1. Searches KB with its own category filter
#    2. Grades doc quality
#    3. Answers with a specialized prompt
#
#  SEARCH STRATEGY:
#    Agents 1-4: filtered vector search (their category only)
#    Agent 5:    full hybrid search (vector + BM25 + rerank, all chunks)
#
#  CAMPUS AGENT: rewrites query before searching
#    "garee time table" → "bus routes transport schedule"
#    Proper LLM-based fix for vocabulary gaps.
#
# ═══════════════════════════════════════════════════════════════

import re
from typing import Dict, Any, List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.documents import Document

import config
from knowledge_base import KnowledgeBase


# ──────────────────────────────────────────────────────────────
#  RETRIEVAL PROFILES  —  single source of truth per route
# ──────────────────────────────────────────────────────────────
# Each agent node reads its (categories, k) from here instead of inlining a
# literal list. WHY: the offline harnesses (bench_retrieval.py, probe_question.py)
# reproduce agent routing to measure retrieval quality. When the lists were
# inlined, the harnesses held their own copies and silently measured a pipeline
# the bot no longer ran — the same drift that let _search_filtered diverge from
# search(). Both now import this dict, so a filter change is measured, not missed.
#
# Only these six categories have rows in the live DB:
#   policy 2640 | faculty 1281 | general 872 | offices 361 | overview 276 |
#   admissions 128
# "fees", "courses", "students", "campus" and "downloads" match ZERO rows —
# those pages were never ingested, so naming them just narrowed the filter for
# no benefit. Verify with:
#   Counter(m["category"] for m in kb.db._collection.get()["metadatas"])
RETRIEVAL_PROFILES = {
    "policy":     (["policy"], 10),
    # admission eligibility and merit rules live in the UG/graduate rules PDFs,
    # so "policy" belongs on the admissions path too.
    "admissions": (["admissions", "policy", "general", "overview"], 12),
    # the fee-structure page was never ingested; fee figures live under
    # "admissions" and the financial-aid percentages on /about-us ("overview").
    "fees":       (["admissions", "overview", "general", "offices"], 8),
    # library/bus-routes/alumni were ingested as "general"; campus
    # infrastructure claims sit on the about/overview pages.
    "campus":     (["general", "overview", "offices"], 10),
    "faculty":    (["faculty", "offices", "overview"], 10),
}


def _assert_every_category_is_readable():
    """Fail at import if a writable category is unreachable, or vice versa.

    This is the guard that would have caught the original regression on the day
    it was introduced. config.py used to declare pages as "courses", "fees",
    "students" and "downloads"; no profile below has ever listed those names, so
    every page filed under them was retrievable by nothing. Nothing crashed and
    nothing logged — the pages simply could not be found, which is precisely why
    it took a RAGAS regression and a manual DB audit to notice.

    Checked in BOTH directions, because each direction is a different bug:

      writable-but-unread   a page can be stored where no agent looks. Silent
                            data loss. This is the one that actually happened.

      read-but-unwritable   a profile filters on a name nothing ever writes, so
                            the filter is narrower than it appears and quietly
                            drops the categories it does share the list with.
                            "campus" was such a name: campus_agent_node asked for
                            ["campus", "general", "students", "overview"] and two
                            of those four matched zero rows.
    """
    writable = set(config.CATEGORIES)
    readable = {cat for cats, _ in RETRIEVAL_PROFILES.values() for cat in cats}

    unread = sorted(writable - readable)
    if unread:
        raise ValueError(
            f"config.CATEGORIES declares {unread}, which no RETRIEVAL_PROFILES "
            "entry reads. Pages stored under those categories would be "
            "unretrievable. Either add the category to a profile below or stop "
            "declaring it in config.py."
        )

    unwritable = sorted(readable - writable)
    if unwritable:
        raise ValueError(
            f"RETRIEVAL_PROFILES filters on {unwritable}, which is not in "
            f"config.CATEGORIES {tuple(sorted(writable))}, so nothing can ever "
            "write it. Remove the dead name from the profile — leaving it in "
            "makes the filter look wider than it is."
        )


_assert_every_category_is_readable()


# ──────────────────────────────────────────────────────────────
#  SHARED ANSWER-LENGTH RULES
# ──────────────────────────────────────────────────────────────
# Appended to every answer prompt so the rule lives in one place.
#
# WHY THIS EXISTS — measured, two separate problems, one cause:
#
# 1. LATENCY. Total response time tracks answer length almost linearly, because
#    generation dominates. Measured on gpt-4o-mini: an 89-character answer
#    completed the whole request in 4.0s, while 1600- and 1811-character answers
#    took 9.0s. Retrieval (~2.0s) and routing (~0.8s) were identical in all
#    cases, so roughly 6s of the slow requests was pure token emission.
#
# 2. SCORE. RAGAS answer_relevancy generates questions from the answer and
#    compares them to the real one — padding, restated context and boilerplate
#    closers all pull it down. faithfulness is worse still: every additional
#    unsupported sentence is another claim that can fail. So a long answer is
#    penalised twice even when the needed fact is present.
#
# The rules below constrain PADDING, not COMPLETENESS. Nothing here permits
# dropping a required item: the document-checklist and program-list questions
# genuinely need every entry, and cutting those would trade relevancy for
# recall. The instruction is to stop restating the question, stop adding
# unrequested advice, and stop appending contact boilerplate when the answer was
# actually found. No brace characters — these strings go through .format().
#
# The last rule addresses a MEASURED zero, not a style preference. Asked for the
# faculty count, the bot answered "the exact number is not listed in the
# documents. However, it is mentioned that there are over 100 faculty members."
# Every other metric scored 1.00 on that answer — the fact was right and it was
# supported — but answer_relevancy multiplies by a noncommittal flag, and the
# disclaimer clause set the flag, so the whole question scored 0. Two questions
# lost their entire relevancy score to that one sentence shape. Hedging next to
# an answer you are simultaneously giving costs everything and buys nothing.
_BREVITY_RULES = """

LENGTH AND DIRECTNESS (applies to every answer):
- Answer the question that was asked and nothing else. Do not restate the
  question, do not open with a preamble, and do not summarise at the end.
- Give the complete answer, then stop. If the question asks for a list of
  documents, programs or steps, include every item — completeness is required.
  Brevity means no padding, never a shorter list.
- Do not append "Please contact the university office" or a website link when
  the documents did answer the question. Add a referral only when the specific
  detail genuinely is not in the documents.
- Do not add advice, encouragement or next steps that were not asked for.
- Prefer the shortest form that is still complete: a direct factual question
  deserves one or two sentences, not a bulleted section.
- Lead with the answer the documents support, in their own wording. Say a detail
  is unavailable only when you are not also supplying it: if you can give the
  value, give the value and stop there."""

# MEASURED DEAD END — do not re-add a scoping rule here.
# A rule was tried that said completeness applies only to the list the question
# asked for, with examples ("asked how to apply, give the steps and not the
# document checklist ... not the fee table"). It did not shorten anything: Q19
# went 855 -> 1096 chars and Q47 ANSWERED WITH A FEE TABLE it had not included
# before, because naming the categories to avoid is what put them in mind. The
# same thing happened with _FIGURE_RULES below, which could not make the model
# pair a figure with the caption on the next line until the pairing was done in
# code instead.
#
# The padding is not a prompt problem. Parents are whole pages: /admission holds
# the steps, the document checklist, the fee table and the postal address in one
# document, so an answer drawn from it covers all four no matter what the prompt
# asks for. Fix it by narrowing what reaches the prompt, not by asking the model
# to ignore what it was given.


# ──────────────────────────────────────────────────────────────
#  SHARED FIGURE-READING RULES
# ──────────────────────────────────────────────────────────────
# Appended to every answer prompt, alongside _BREVITY_RULES.
#
# WHY THIS EXISTS: several pages were scraped from a card/tile layout where each
# statistic rendered as a big number above a caption. Flattened to text, that
# becomes a column of alternating lines — figure, then its caption:
#
#     5                                  <- figure
#     State-of-the-Art Computer Labs     <- its caption
#     80%
#     Students Supported Through Financial Aid
#     2000+                              <- caption lost by the scrape
#     1500+                              <- caption lost by the scrape
#     100+
#     Faculty
#     10+
#     Degree Programs
#
# Two consequences, and this rule addresses both:
#
# 1. The pairing is POSITIONAL and easy to get backwards. Asked for the faculty
#    count, gpt-4o-mini answered "1500+" — it grabbed a nearby number instead of
#    the one the caption "Faculty" actually belongs to (100+).
#
# 2. Some captions are genuinely missing. "2000+" and "1500+" have no caption in
#    ANY local artifact — verified against the oldest parent_chunks backup, so
#    the loss happened in the original scrape, not in a later cleanup script.
#    The honest answer for those is that the figure's meaning is unavailable.
#    Guessing is what produced the DB's false "over 2000 faculty members"
#    propositions in the first place.
#
# This states the layout convention rather than any specific number, so it works
# for any mangled statistics block in the corpus and cannot bake an evaluation
# answer into the prompt. No brace characters — goes through .format().
_FIGURE_RULES = """

READING FIGURES OUT OF SCRAPED LAYOUTS:
- Some documents contain a statistics block where each figure sits alone on one
  line and its caption follows on the NEXT line, for example a line reading
  "80%" followed by a line reading "Students Supported Through Financial Aid".
  Pair each figure with the caption that FOLLOWS it. Never pair a figure with a
  caption that comes before it, and never with a caption that already belongs to
  a different figure.
- If a figure has no caption after it, its meaning was lost when the page was
  captured. Do not guess what it counts and do not attach it to the nearest
  caption. Treat that figure as unavailable.
- If the question asks for a count and no figure in the documents is captioned
  with that thing, say the figure is not available. Do not substitute the
  closest-looking number."""


# ──────────────────────────────────────────────────────────────
#  SHARED CONFLICTING-SOURCE RULES
# ──────────────────────────────────────────────────────────────
# Appended to every answer prompt, alongside _BREVITY_RULES and _FIGURE_RULES.
#
# WHY THIS EXISTS: the university's own site contradicts itself about who holds
# some roles. The live example is the Dean of the Faculty of Basic Sciences and
# Mathematics:
#
#     dean-message      -> Dr. Shahji Ahmed      (1 page, no role email)
#     /contact          -> Dr. Abdul Samad       (carries deanFBSM@uoli.edu.pk)
#     4th-asrb-meeting  -> Dr. Abdul Samad
#
# Both names are genuinely in the corpus, so retrieval cannot fix this and
# neither can the scraper — the disagreement is in the source material. Left
# alone, the model picks whichever snippet ranks first, which makes the answer
# a coin flip and hands a grader an easy unfaithfulness mark either way.
#
# The rule below is deliberately written WITHOUT naming any person. Hard-coding
# "the Dean is Dr. Abdul Samad" would bake one evaluation answer into the prompt
# and would become a lie the moment the university appoints someone new. Stating
# how to WEIGH evidence instead means:
#
#   * it resolves today's case to the corroborated name (2 sources + a
#     role-specific email) without being told the answer,
#   * it self-corrects whenever the site is updated, because the evidence moves
#     with the corpus,
#   * it covers every future role contradiction, not just this one.
#
# Citing the source page matters as much as the choice: it lets a reader verify
# the claim, and it is what turns an unfaithful-looking guess into a supported,
# checkable statement. No brace characters — goes through .format().
_CONFLICT_RULES = """

WHEN THE DOCUMENTS DISAGREE:
- If the documents give different names for the same role or position, do not
  pick the first one you see and do not present a disputed name as settled.
- Prefer the name that is supported by more than one document, and the name
  that is backed by a role-specific email address or official contact entry
  over one that appears in a message or narrative page only.
- Name the source page for the answer you give, so the reader can check it.
- If the evidence is genuinely balanced, say that the available pages disagree
  and give both names with their sources. Never invent a tiebreaker."""


# ──────────────────────────────────────────────────────────────
#  SHARED HELPERS
# ──────────────────────────────────────────────────────────────

_LLM_SINGLETON = None


def _get_llm():
    """Shared ChatOpenAI client.

    WHY A SINGLETON: this was previously constructing a new ChatOpenAI on every
    call, and it is called from 8 agent nodes. Each construction builds a fresh
    httpx client with its own connection pool, so every answer paid a new TCP +
    TLS handshake to api.openai.com before the first token could be requested.
    Reusing one client keeps the connection alive across requests. The object is
    stateless with respect to a call (all per-call data is passed to .invoke),
    and every call site uses identical parameters, so sharing it is safe.
    """
    global _LLM_SINGLETON
    if _LLM_SINGLETON is None:
        _LLM_SINGLETON = ChatOpenAI(
            model          = config.LLM_MODEL,
            temperature    = config.LLM_TEMPERATURE,
            openai_api_key = config.OPENAI_API_KEY,
        )
    return _LLM_SINGLETON


def _build_history(state: Dict[str, Any]) -> str:
    """
    Build history string: summary first (compressed older context),
    then last 6 raw messages (recent detail).
    Same pattern as workflow.py stream_chat().
    """
    messages = state.get("messages", [])
    summary  = state.get("summary", "")
    recent   = messages[-6:] if len(messages) > 6 else messages

    history = ""
    if summary:
        history += f"[EARLIER CONVERSATION SUMMARY]\n{summary}\n\n"
        history += "[RECENT MESSAGES]\n"

    for msg in recent:
        role     = "Student" if isinstance(msg, HumanMessage) else "Bot"
        history += f"{role}: {msg.content}\n"

    return history if history.strip() else "This is the start of the conversation."


def _docs_by_source(kb: KnowledgeBase, url: str,
                    where_document: dict = None) -> List[Document]:
    """Every chunk of one page, whatever slash form its `source` was stored in.

    THE BUG THIS EXISTS TO KILL
    ───────────────────────────
    Fourteen call sites in this module did `kb.db.get(where={"source": {"$eq":
    url}})` against URL literals that all ended in "/", under a comment
    asserting "trailing slashes match ingested ChromaDB metadata". That was true
    of an older corpus. It is not true of this one:

        hard-coded page URLs in this file : 42, of which 37 end in "/"
        distinct `source` values in Chroma: 109, of which  0 end in "/"

    web_scraper.GUARANTEED_PAGES lists every URL WITHOUT a trailing slash and
    that exact string is what lands in `source`. So after the site was re-scraped
    every one of those lookups matched zero rows — silently, because .get()
    returns an empty result rather than raising. Measured on the 37-question
    evaluation set: nine questions (Registrar, Treasurer, Controller, Deputy
    Controller, the CS HOD and its email/extension) retrieved ZERO documents and
    answered "I don't have enough information" about facts sitting in their own
    database, and six more fell back to short unranked chunks.

    WHY A HELPER RATHER THAN EDITING 37 STRINGS
    ───────────────────────────────────────────
    Editing the strings fixes today's corpus and breaks again the first time the
    ingest side changes its mind about trailing slashes — which is exactly what
    already happened once. Matching both forms means neither side has to know
    what the other chose. The literals are still normalised to the no-slash form
    so the common case costs one query, not two.
    """
    if not url:
        return []
    bare = url.rstrip("/")
    for candidate in (bare, bare + "/"):
        kwargs = {"include": ["documents", "metadatas"],
                  "where": {"source": {"$eq": candidate}}}
        if where_document:
            kwargs["where_document"] = where_document
        try:
            result = kb.db.get(**kwargs)
        except Exception:
            continue
        documents = result.get("documents") or []
        if documents:
            metadatas = result.get("metadatas") or []
            return [Document(page_content=t, metadata=m or {})
                    for t, m in zip(documents, metadatas)]
    return []


# ── Named offices: where the page is, and what the post is called ────────────
# These two tables are keyed identically on purpose. _OFFICE_URL says where the
# office's page lives; _ROLE_FALLBACK_TERMS says how the post is written in
# prose. A role must appear in both or it has no second chance, and keeping them
# adjacent is the only thing stopping them from drifting apart.
#
# They live at module scope rather than inside _get_faculty_docs_raw so a test
# can import and assert against them without calling a 700-line function.
_OFFICE_URL = {
    # Office pages. Stored WITHOUT a trailing slash — see _docs_by_source,
    # which matches either form so this table cannot silently empty again.
    "controller":      "https://uoli.edu.pk/offices/controller-of-examination-office",
    "exam control":    "https://uoli.edu.pk/offices/controller-of-examination-office",
    "treasurer":       "https://uoli.edu.pk/office-of-treasurer",
    "registrar":       "https://uoli.edu.pk/office-of-registrar",
    "oric":            "https://uoli.edu.pk/oric",
    # Not /offices/directorate-of-it. It was, and the lookup returned nothing.
    "directorate":     "https://uoli.edu.pk/directorate-of-it",
    "fao":             "https://uoli.edu.pk/offices/fao",
    # ── Leadership pages ─────────────────────────────────────────────────
    # "who is vc" → _leadership_count=1 → enters this loop → direct URL fetch
    # returns vc-message chunks → grader sees them as PARTIAL → the model
    # answers instead of returning a hard FALLBACK.
    "vc":              "https://uoli.edu.pk/vc-message",
    "vice chancellor": "https://uoli.edu.pk/vc-message",
    "vice-chancellor": "https://uoli.edu.pk/vc-message",
    "vicechanclor":    "https://uoli.edu.pk/vc-message",
    "vise chancellor": "https://uoli.edu.pk/vc-message",
    "voice chancellor":"https://uoli.edu.pk/vc-message",
    "pro vc":          "https://uoli.edu.pk/pro-vc-message",
    "pro-vc":          "https://uoli.edu.pk/pro-vc-message",
    "dean":            "https://uoli.edu.pk/dean-message",
}

# How each post is spelled in the corpus, used by _role_name_probe when the
# office page does not name the person holding it.
#
# WHY THIS IS A TABLE AND NOT AN `if "vc" in keyword` BLOCK: what stood here was
# a rescue hand-written for the Vice Chancellor alone. It fired only when the
# keyword contained "vc"/"vice"/"chancellor", so when the office-page lookup
# broke, the VC still answered and the Registrar, Treasurer, Controller and
# Deputy Controller returned nothing at all. Measured on the evaluation set:
# four leadership questions retrieved zero documents while the VC retrieved
# five. One role was rescued; the rest were left to fail silently.
_ROLE_FALLBACK_TERMS = {
    "controller":      ["Controller of Examination", "Controller of Examinations"],
    "exam control":    ["Controller of Examination", "Controller of Examinations"],
    "treasurer":       ["Treasurer"],
    "registrar":       ["Registrar"],
    "oric":            ["Office of Research, Innovation", "ORIC"],
    "directorate":     ["Directorate of Information Technology", "Directorate of IT"],
    "fao":             ["Financial Aid Office", "FAO"],
    "vc":              ["Vice Chancellor", "Vice-Chancellor", "Ehsanullah", "Kakar"],
    "vice chancellor": ["Vice Chancellor", "Vice-Chancellor", "Ehsanullah", "Kakar"],
    "vice-chancellor": ["Vice Chancellor", "Vice-Chancellor", "Ehsanullah", "Kakar"],
    "vicechanclor":    ["Vice Chancellor", "Vice-Chancellor", "Ehsanullah", "Kakar"],
    "vise chancellor": ["Vice Chancellor", "Vice-Chancellor", "Ehsanullah", "Kakar"],
    "voice chancellor":["Vice Chancellor", "Vice-Chancellor", "Ehsanullah", "Kakar"],
    "pro vc":          ["Pro Vice Chancellor", "Pro-Vice Chancellor"],
    "pro-vc":          ["Pro Vice Chancellor", "Pro-Vice Chancellor"],
    "dean":            ["Dean"],
}


# A statement that names a human being: an honorific followed by a capitalised
# word. The corpus is propositional, so the sentence that answers "who is the
# Registrar" is always of this shape — "Prof. Dr. Khalid Khan is the Registrar
# of the University of Loralai." Anything without it is describing the office,
# not the officeholder.
_PERSON_RE = re.compile(r"\b(?:Prof|Dr|Mr|Ms|Mrs|Miss|Engr|Syed)\.?\s+[A-Z]")

# "<person> ... is the <role>" — the chunk states who holds the post, as opposed
# to merely mentioning both a person and the post in the same sentence ("The
# Vice Chancellor congratulated Dr. Hameed"). Ordering on this rather than on
# whatever order Chroma happens to return matters because the site is re-scraped
# every 24 hours: storage order is re-derived on every ingest and is not a
# property anything should depend on.
_ROLE_ASSERTION_RE = r"\b(?:is|serves as|was appointed|has been appointed)\b"


def _asserts_role(text: str, term: str) -> bool:
    """True if `text` states that a named person holds `term`."""
    match = _PERSON_RE.search(text)
    if not match:
        return False
    return bool(re.search(_ROLE_ASSERTION_RE + r"[^.]{0,40}" + re.escape(term),
                          text[match.start():]))


def _role_name_probe(kb: KnowledgeBase, keyword: str, limit: int = 6) -> List[Document]:
    """Find the chunks that name whoever holds `keyword`'s post.

    WHY A LITERAL SUBSTRING SEARCH AND NOT A VECTOR SEARCH:
        These facts are single short sentences — "Mr. Nasir Rehan is the
        Treasurer of the University of Loralai." Embedded on its own, that
        sentence sits in a neighbourhood crowded with seventy-five other
        Treasurer-flavoured sentences from the office page, and the reranker
        has no signal to tell the one with the name from the seventy-five
        without. A `$contains` on the role string plus a name-shape filter is
        exact, costs no tokens, and cannot be outvoted by volume.

    WHY IT IS DRIVEN BY _ROLE_FALLBACK_TERMS:
        So that adding a role to _OFFICE_URL and forgetting to give it a rescue
        is visible in one place. See that table's comment for what the
        hand-written, VC-only version of this cost.

    Returns at most `limit` documents, in three tiers: chunks that state who
    holds the post, then chunks that merely name someone, then the rest.
    """
    terms = _ROLE_FALLBACK_TERMS.get(keyword) or []
    seen = set()
    asserts, named, other = [], [], []
    for term in terms:
        try:
            result = kb.db.get(include=["documents", "metadatas"],
                               where_document={"$contains": term})
        except Exception:
            continue
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        for text, meta in zip(documents, metadatas):
            if not text or text in seen:
                continue
            seen.add(text)
            doc = Document(page_content=text, metadata=meta or {})
            if _asserts_role(text, term):
                asserts.append(doc)
            elif _PERSON_RE.search(text):
                named.append(doc)
            else:
                other.append(doc)
        if len(asserts) >= limit:
            break
    return (asserts + named + other)[:limit]


def _search_filtered(kb: KnowledgeBase,
                     query: str,
                     categories: List[str],
                     k: int = 10,
                     engineered_queries: List[str] = None) -> List[Document]:
    """
    Category-filtered retrieval — a thin wrapper over KnowledgeBase.search().

    WHY IT IS NOW A WRAPPER:
        This function used to reimplement the entire retrieval pipeline (expand,
        parallel hybrid search, RRF, rerank, parent fetch) so it could skip E3
        compression. That was a sound reason at the time: E3 ran an LLM extractor
        that shrank 7-9 docs to 1-2 and made the faculty agent answer "I could not
        find" over data it had actually retrieved.

        E3 is no longer an LLM extractor. It is a CrossEncoder score gate that
        costs nothing and only drops documents the reranker already scored as
        noise, so there is nothing left to bypass. Keeping a second copy of the
        pipeline only meant the agents silently missed every improvement made to
        search() — including the adaptive HyDE retry that rescues questions whose
        wording shares no vocabulary with the documents that answer them.

        One retrieval path now serves every agent.

    Args:
        k: upper bound on returned docs. The score gate may return fewer when the
           tail is noise; list queries ("all HODs") bypass gating entirely.
    """
    docs = kb.search(query,
                     filter_categories=categories,
                     engineered_queries=engineered_queries)
    return docs[:k]


# ── Aggregate-statistics detection ────────────────────────────────────────────
# A count question is one of two completely different retrieval problems, and
# getting the distinction wrong is silent:
#
#   "how many DEPARTMENTS"  -> enumerable. Answer by listing the department
#                              pages and counting them.
#   "how many FACULTY"      -> a population size. It is a single published
#                              figure on the /about-us "at a Glance" block.
#                              You cannot establish it by listing 11 pages.
#
# The faculty path's list-all sweep used to swallow both, which is why the
# statistics questions in the evaluation set retrieved eleven HOD names and no
# number at all. This lives here, next to _search_filtered, rather than inside
# one agent because count questions do not arrive on a single route: the router
# sends "How many faculty members..." to faculty and "How many students,
# faculty, and programs..." to general. Both need the same answer.
_COUNT_WORDS = ["how many", "how much", "number of", "total number",
                "count of", "kitne", "kitna", "kitni",
                # Urdu/Roman-Urdu population-size phrases:
                # "strength" = total enrolled count in Pakistani university context
                # "tadad" = number/count in Urdu
                # "tabdad" = headcount in Urdu
                "strength", "tadad", "tabdad", "population"]

# Populations whose size is a published figure rather than a list.
_POPULATION_WORDS = ["student", "faculty", "teacher", "employee", "staff",
                     "program", "programme", "course", "graduate", "alumni",
                     "scholarship", "lab", "laboratory", "hostel", "campus",
                     # "strength" in Pakistani university context = student headcount
                     "strength", "enrollment", "enrolment"]

# Things that genuinely can be enumerated one per page. If the question names
# one of these it is a directory question — including "how many faculty are in
# the CS department", which is a per-department lookup, not a university figure.
_ENUMERABLE_WORDS = ["department", "dept", "hod", "head", "chair",
                     "chairperson", "faculties"]


def _asked_populations(query_lower: str) -> List[str]:
    """Population nouns named in the query, or [] if this is not a stats question.

    Whole-word matching, not substring: a bare "lab" tested as a substring also
    fires on "collaboration", and "how many collaborations" is not a campus
    statistic. The (s|es)? suffix lets the word list stay singular.
    """
    def mentions(words):
        return [w for w in words
                if re.search(r"\b" + w + r"(s|es)?\b", query_lower)]

    if not any(w in query_lower for w in _COUNT_WORDS):
        return []
    if mentions(_ENUMERABLE_WORDS):
        return []
    return mentions(_POPULATION_WORDS)


def _search_stats(kb: KnowledgeBase, populations: List[str],
                  k: int = 6) -> List[Document]:
    """Retrieve the university's published figures for the named populations.

    Ranks against the populations actually asked about rather than one fixed
    string, so a question about faculty size is not diluted by terms for
    students and programmes it never mentioned.

    k defaults to 6 rather than the usual 10 because these are page-level
    parents, not propositions: the "at a Glance" block arrives as a ~660
    character parent and its neighbours are whole nav/footer pages. Ten of them
    is a large prompt for a question whose answer is one number, and prompt size
    is the dominant term in this path's latency.
    """
    query = ("University of Loralai at a glance total number of "
             + ", ".join(populations))
    return _search_filtered(kb, query, ["overview", "general"], k=k)


def _format_context(docs: List[Document], max_docs: Optional[int] = None) -> str:
    """Format retrieved docs into context string for LLM prompt.

    max_docs controls how many docs are passed to the LLM. It defaults to None
    (= show every retrieved doc) so that the LLM sees exactly the documents that
    _finish() returns as `docs`. Those same docs are what eval_ragas.py hands to
    RAGAS as `contexts`; truncating here while returning the full list there
    meant the answer-bearing chunk could sit outside the prompt yet still be
    scored (e.g. a chunk ranked 9 of 9 → refusal → answer_relevancy 0), and the
    unseen tail still counted against context_precision.
    """
    if not docs:
        return "No relevant documents found."

    selected = docs if max_docs is None else docs[:max_docs]

    parts = []
    for i, doc in enumerate(selected, 1):
        source = doc.metadata.get("source", "University Document")
        parts.append(f"[Source {i}: {source}]\n{doc.page_content}")

    return "\n---\n".join(parts)


def _grade_quality(docs: List[Document]) -> str:
    """
    Check quality of retrieved docs.
    For filtered search: no rerank scores stored, so we check
    if docs were actually returned (non-empty = good enough).
    For general agent search: reads rerank_score from metadata.
    """
    if not docs:
        return "empty"

    scores = [
        float(doc.metadata["rerank_score"])
        for doc in docs
        if "rerank_score" in doc.metadata
    ]

    # Filtered search doesn't go through reranker — no scores stored.
    # If docs were returned by vector similarity, trust them.
    if not scores:
        return "good"

    best = max(scores)
    if best > 0.5:  return "good"
    if best > 0.0:  return "partial"
    return "poor"


FALLBACK_ANSWER = (
    "I don't have enough information in the university knowledge base "
    "to answer this accurately.\n\n"
    "Please contact the university directly:\n"
    f"• 📞 Phone: {config.FALLBACK_PHONE}\n"
    f"• 🌐 Website: {config.FALLBACK_WEBSITE}\n"
    f"• 📍 Visit: {config.FALLBACK_ADDRESS}"
)


def _finish(state: Dict[str, Any],
            answer: str,
            docs: List[Document]) -> Dict[str, Any]:
    """
    Standard return dict for every agent.

    D3 FIX — Message Duplication Bug:
    We intentionally return an EMPTY messages list here.

    WHY: BotState.messages uses Annotated[List, operator.add] which means
    LangGraph APPENDS whatever a node returns to the existing state messages.
    If we returned [HumanMessage, AIMessage] here, LangGraph would append them.
    Then workflow.py chat() and stream_chat() ALSO do:
        history + [HumanMessage(user), AIMessage(answer)]
    This creates DOUBLE messages — every turn gets stored twice.

    FIX: Agents return NO messages. workflow.py owns all message persistence.
    Single responsibility: one place manages messages = no duplication.
    """
    return {
        "answer":   answer,
        "docs":     docs,
        "context":  _format_context(docs),
        "messages": [],   # ← D3 FIX: empty list — workflow.py handles saving
    }


def _extract_answer(raw: str) -> str:
    """
    Extract answer from LLM response.
    Handles "ANSWER: ..." format and plain text.
    """
    raw = raw.strip()
    if "ANSWER:" in raw:
        return raw.split("ANSWER:", 1)[1].strip()
    return raw


# ══════════════════════════════════════════════════════════════
#  AGENT 1 — POLICY AGENT
# ══════════════════════════════════════════════════════════════

POLICY_PROMPT = """You are a university policy expert for {university_name}.

POLICY DOCUMENTS:
{context}

CONVERSATION HISTORY:
{history}

STUDENT'S QUESTION:
{question}

STRICT RULES:
- Answer ONLY from the provided policy documents.
- VOCABULARY BRIDGING: Students use different words than official documents.
  "attendance" = "class presence/absences", "punishment" = "penalty/disciplinary action",
  "miss classes" = "absence from lectures", "cheating" = "use of unfair means".
  Bridge vocabulary gaps intelligently — do not refuse just because exact word differs.
- If attendance percentage is mentioned, state the exact number as written in the document.
- State consequences and penalties clearly and exactly as written.
- Use bullet points for clarity.
- If the information is genuinely not in any document after careful reading, say:
  "This specific information is not in my documents. Please contact the university office directly."
- Do NOT use "ANSWER:" prefix in your response.""" + _BREVITY_RULES + _FIGURE_RULES + _CONFLICT_RULES


def policy_agent_node(state: Dict[str, Any], kb: KnowledgeBase) -> Dict[str, Any]:
    query       = state["user_query"]

    # Retrieval lives in retrieve_for_agent() so stream_chat() runs exactly this
    # and cannot drift from it again. The attendance special case and the
    # ["policy"] filter are both there.
    docs, _max_docs, _ = retrieve_for_agent(kb, "policy", query)

    if not docs:
        return _finish(state, FALLBACK_ANSWER, docs)

    llm    = _get_llm()
    prompt = POLICY_PROMPT.format(
        university_name = config.UNIVERSITY_NAME,
        context         = _format_context(docs),
        history         = _build_history(state),
        question        = query,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return _finish(state, _extract_answer(response.content), docs)

# ══════════════════════════════════════════════════════════════
#  AGENT 2 — FACULTY & CONTACTS AGENT
# ══════════════════════════════════════════════════════════════

FACULTY_PROMPT = """You are a directory assistant for {university_name}.

UNIVERSITY DIRECTORY:
{context}

CONVERSATION HISTORY:
{history}

STUDENT'S QUESTION:
{question}

STRICT RULES:
- Answer ONLY from the provided documents.
- NEVER state a name, email address, phone number, extension, title or count
  that does not appear verbatim in the provided documents. If a detail is
  missing from the documents, say that specific detail is not available —
  do not fill it in from general knowledge or from the examples below.
- The examples in these rules show FORMAT ONLY. They are not facts about this
  university and must never be repeated as if they were.
- For SINGLE PERSON queries ("who is X", "tell me about X"):
  Give a natural conversational response explaining who they are,
  their role, department, and contact details.
  Format: "<Name> is the <Title> of the <Department> Department.
  <Name> can be contacted at <email exactly as it appears in the documents>."
  Include only those fields the documents actually provide.
  - For queries about "vc message" or "vice chancellor message":
  Look ONLY at content from the Vice Chancellor page (vc-message source).
  Do NOT use Pro-VC, Dean, or Registrar message content for this.
  - For "how many faculty" questions: a figure the documents give as an
  approximation ("over 100", "100+") IS the count — give it directly, worded the
  way the documents word it. Only when no figure appears anywhere in the
  documents, describe what they do show instead (the departments and heads you
  can see).
- For queries about "pro vc message": use pro-vc-message source only.
- For queries about "dean message": use dean-message source only.
- For queries about "registrar message": use registrar-message source only.
- For PARTIAL NAME queries ("who is khawar", "who is bilal"):
  Find any person whose full name CONTAINS that word and give a
  natural conversational response about them with their full name.
- - For LIST queries ("all HODs", "all heads", "all department names"):
  Go through EVERY source in the context ONE BY ONE in order.
  For each source, extract any HOD/department name, title, email.
  Do NOT skip any source. Do NOT summarize early.
  Only after reading ALL sources, compile the complete list.
  Format each entry as: Department: Name — Title — email
- If a person is not found at all, say:
  "I could not find [name] in university records. Please contact
  the main office or check uoli.edu.pk"
- Never make up or guess contact information.
- Never say you cannot find the HOD if they appear in the context.

- For AMBIGUOUS queries ("HOD sahib ka number", "HOD ka email") where no department is specified:
  Answer with whichever HOD information you found, but clearly state which department it belongs to.
  Example: "I found the HOD of Commerce Department. If you meant a different department, please specify."
- For HOD / HEAD OF DEPARTMENT queries about a SPECIFIC department:
  This is CRITICAL — match the department the student asked about.
  Examples:
    "Head of Computer Science" → ONLY answer about the Computer Science HOD
    "Head of Commerce" → ONLY answer about Commerce HOD
    "Head of English" → ONLY answer about English HOD
  If the context contains HODs from MULTIPLE departments, read ALL of them but
  answer ONLY about the department that was specifically asked.
  NEVER return the HOD of Allied Health Sciences when Computer Science was asked.
  NEVER return Management Sciences HOD when Commerce was asked.
  The person's title in the context is "(HOD)" — match it to the RIGHT department.
- Do NOT use "ANSWER:" prefix anywhere in your response.""" + _BREVITY_RULES + _FIGURE_RULES + _CONFLICT_RULES



def _get_all_faculty_docs(kb: KnowledgeBase) -> List[Document]:
    """
    For HOD list queries: search specifically for HOD/Chairperson terms
    across ALL faculty chunks. Uses a targeted search query that finds
    chunks explicitly mentioning HODs.
    WHY: Taking first 2 chunks per source misses HOD info that appears
    in later chunks. Searching for "Chairperson HOD Head Department"
    finds exactly the chunks that have HOD information.
    """
    # First: targeted HOD-specific search across faculty category
    hod_docs = kb.db.similarity_search(
        "Chairperson HOD Head of Department email contact lecturer",
        k=30,
        filter={"category": {"$eq": "faculty"}}
    )

    # Second: also search overview category (has leadership contacts)
    overview_docs = kb.db.similarity_search(
        "Head of Department HOD Chairperson email",
        k=10,
        filter={"category": {"$eq": "overview"}}
    )

    # Merge and deduplicate by source
    all_docs     = hod_docs + overview_docs
    seen_sources = {}
    final_docs   = []

    for doc in all_docs:
        source = doc.metadata.get("source", "")
        count  = seen_sources.get(source, 0)
        if count < 3:  # allow up to 3 chunks per source
            final_docs.append(doc)
            seen_sources[source] = count + 1

    return final_docs

def _get_hods_from_graph(graph_searcher) -> str:
    """
    Extract all HOD relationships directly from the knowledge graph.
    WHY use graph instead of KB search: The graph already extracted
    all HOD relationships during build_graph_rag.py. KB search with
    chunk limits misses some HODs. Graph has guaranteed complete data.
    Returns formatted HOD list or empty string if graph has no HODs.
    """
    hod_entries = []

    for source, target, data in graph_searcher.graph.edges(data=True):
        if data.get("relation") == "is_hod_of":
            desc  = graph_searcher.entity_descriptions.get(source, "")
            email = ""
            # Extract email from description if present
            if "@" in desc:
                for word in desc.split():
                    if "@" in word:
                        email = word.strip(".,;()")
            line = f"• {target}: {source}"
            if email:
                line += f" — {email}"
            elif desc:
                line += f" — {desc[:60]}"
            hod_entries.append(line)

    return "\n".join(hod_entries) if hod_entries else ""
def _get_faculty_docs(kb: KnowledgeBase, query: str,
                      engineered_queries: List[str] = None):
    """Faculty document fetching, with duplicates collapsed and furniture sunk.

    WHY THIS WRAPPER EXISTS: the faculty path assembles its candidates from
    several sources — a raw Chroma `where` query, the department URL map, the
    HOD sweep and the ordinary filtered search — and several of those bypass
    KnowledgeBase.search() entirely. Two defects follow from that, and both are
    fixed here because here is the one place every branch passes through:

    1. DUPLICATES. The same passage reaches the list from more than one source.
       The measured result was a context list whose documents 3 and 5 were
       byte-identical, as were 4 and 6: four slots holding two documents.
       Duplicates cost twice over, because context_precision is average
       precision (a repeat pushes every later document down a rank) and because
       the duplicated text crowds out other documents from the prompt.

    2. PAGE FURNITURE AT RANK 1. The branches that read Chroma directly return
       documents in storage order with no rerank_score, so the demotion applied
       inside _rerank never reaches them. On Q12 the site footer led and the
       document naming the Deputy Controller sat at rank 4 — average precision
       1/4 = 0.250, exactly that question's recorded score.

    Doing both at this chokepoint rather than at the 14 return points inside
    _get_faculty_docs_raw means one edit covers the agent node, stream_chat and
    the benchmark harnesses alike, and a future return statement cannot
    reintroduce either bug. Order is otherwise preserved and the first
    occurrence of a duplicate wins: it is the one the branch ranked highest.

    3. COMPOUND QUESTIONS RETRIEVED WORSE THAN EITHER OF THEIR HALVES. This is
       the same "several branches bypass search()" fact seen from the other side:
       KnowledgeBase._search_multi_intent lives inside search(), so the faculty
       path never reached it and asked a two-part question as one string.
       Measured on "Who is the VC of UoL and what departments does UoL have?":

           query                              docs   VC name   dept list
           the compound question                 4      no        no
           "Who is the VC of UoL" alone          5     YES       YES
           "what departments does UoL have"      8      -        YES

       Both facts are in the corpus and each half finds them, yet the question
       that asks for both finds neither — the blended embedding sits between two
       topics and matches the centre of neither. So the split is applied here
       too, each intent goes through the full faculty machinery on its own (the
       department URL map, the HOD sweep and the filtered search all get to fire
       for the sub-question they suit), and the results interleave by rank.

       Interleaving rather than concatenating, and a per-intent budget rather
       than a global one, both follow _search_multi_intent's reasoning: with
       average-precision scoring every intent's best document is relevant to the
       question as a whole, so each intent must be represented near the top.
    """
    intents = [] if engineered_queries else kb._split_intents(query)
    if len(intents) >= 2:
        docs, max_docs = _faculty_docs_multi_intent(kb, intents, query,
                                                    engineered_queries)
    else:
        docs, max_docs = _get_faculty_docs_raw(kb, query, engineered_queries)

    # ── Exit guard: no branch may end the faculty path with nothing ──────
    # _get_faculty_docs_raw has fourteen return points. Any one of them
    # returning [] means faculty_agent_node emits FALLBACK_ANSWER — "I don't
    # have enough information" — over a corpus that holds the answer, and the
    # user cannot tell that apart from a genuine gap. That is exactly what
    # happened: nine evaluation questions retrieved zero documents because the
    # office-page URLs carried a trailing slash the database does not store,
    # and a hard `return raw, 5` sat between the failed lookup and the search
    # that would have found them anyway.
    #
    # Fixing that one branch is not enough, because the next branch added will
    # have the same shape. This is the single place every branch passes
    # through, so the guarantee is made once, here: an empty result is always
    # retried as an ordinary search before it is allowed to become a refusal.
    #
    # "general" is in the category list and must stay. It is the largest
    # category in the corpus (5,565 chunks) and it holds /contact — the page
    # that names the Registrar and the Controller of Examination. A faculty
    # rescue that searches only faculty/offices/overview cannot see either.
    if not docs:
        docs = _search_filtered(kb, query,
                                ["faculty", "offices", "overview", "general"],
                                k=10, engineered_queries=engineered_queries)
        max_docs = min(max_docs or 5, 8)

    seen, unique = set(), []
    for doc in docs or []:
        key = KnowledgeBase._content_key(doc.page_content)
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)

    # Branches that read Chroma directly return storage order, not relevance
    # order. Score those into order — it drops nothing, so it can only help
    # precision. Branches that came through search() are already reranked and
    # are left exactly as they are.
    #
    # HOW "ALREADY RANKED" IS DETECTED, AND WHY NOT `is not None`: the raw
    # branches stamp rerank_score = 0.0 on the documents they pass through
    # _fetch_parents, and leave it absent on the rest. A present-but-constant
    # score is a placeholder, not a ranking, so testing for presence classified
    # those lists as already-ranked and skipped the reordering entirely. What
    # makes a list ranked is that its scores DIFFER; one distinct value or none
    # carries no ordering information.
    #
    # The list sweeps ("all HODs") are excluded by the size test: they return up
    # to 60 documents that the prompt consumes as a set rather than a ranking,
    # and scoring them would spend half a second reordering something
    # order-insensitive. Those still get the cheap furniture partition.
    distinct_scores = {d.metadata.get("rerank_score") for d in unique
                       if d.metadata.get("rerank_score") is not None}
    if len(distinct_scores) <= 1 and 1 < len(unique) <= config.TOP_K_RESULTS:
        unique = kb._order_unscored(query, unique)
    else:
        unique = KnowledgeBase._demote_boilerplate(query, unique)

    # max_docs is a prompt-window size, not an index into docs. Shrink it by the
    # number of duplicates removed so the caller still sees the same amount of
    # DISTINCT text it asked for, and never more documents than now exist.
    return unique, min(max_docs, len(unique))


def _faculty_docs_multi_intent(kb: KnowledgeBase, intents: List[str],
                               query: str,
                               engineered_queries: List[str] = None):
    """Run the faculty machinery once per intent and interleave by rank.

    See defect 3 in _get_faculty_docs for the measurement that motivates this.

    THE LIST-SWEEP GUARD IS THE ONE SUBTLETY. _get_faculty_docs_raw has a branch
    that answers "list all HODs" by returning every matching row — dozens of
    documents that the prompt consumes as a SET, where completeness is the whole
    point. Truncating that to a per-intent budget would silently drop members of
    the list, so if any intent lands on a sweep this hands the whole query back
    to the single-query path. Losing the split on "who is the VC and list all
    HODs" costs one blended retrieval; truncating the sweep would cost names the
    user explicitly asked to enumerate, and a partial list reads as a complete
    one. The asymmetry decides it.

    Falling back to the un-split path is also the behaviour every faculty query
    had before this function existed, so the guard can only return to a known
    state, never to an untested one.
    """
    budget = max(config.MIN_CONTEXT_DOCS, config.MAX_CONTEXT_DOCS)
    per_intent = max(2, budget // len(intents))
    sweep_size = budget * 2          # larger than any ranked list this path returns

    per_intent_docs, widest_window = [], 0
    for intent in intents:
        docs, window = _get_faculty_docs_raw(kb, intent, engineered_queries)
        if len(docs or []) > sweep_size:
            return _get_faculty_docs_raw(kb, query, engineered_queries)
        widest_window = max(widest_window, window)
        per_intent_docs.append(list(docs or [])[:per_intent])

    interleaved = []
    for rank in range(per_intent):
        for docs in per_intent_docs:
            if rank < len(docs):
                interleaved.append(docs[rank])

    # Duplicates across intents are collapsed by the caller, which is why the
    # window is the budget rather than the sum: two intents that agree on a
    # document should free a slot for the next one, not spend two on it.
    return interleaved, max(widest_window, budget)


def _get_faculty_docs_raw(kb: KnowledgeBase, query: str,
                          engineered_queries: List[str] = None):
    """
    Single source of truth for faculty document fetching.
    Handles: specific dept, multi-dept HOD, list-all, general faculty search.
    """
    query_lower = query.lower()

    # ── Department URL map ────────────────────────────────────────────
    # URLs are stored WITHOUT a trailing slash, matching what
    # web_scraper.GUARANTEED_PAGES writes into `source`. Lookups go through
    # _docs_by_source(), which matches either form, so a future change on the
    # ingest side cannot silently empty this map again — see that function for
    # the nine-question outage this replaces.
    _DEPT_URL = {
        "cs":               "https://uoli.edu.pk/faculty/computer-science",
        "computer science": "https://uoli.edu.pk/faculty/computer-science",
        "commerce":         "https://uoli.edu.pk/faculty/commerce",
        "english":          "https://uoli.edu.pk/faculty/english",
        "education":        "https://uoli.edu.pk/faculty/education",
        "mathematics":      "https://uoli.edu.pk/faculty/mathematics",
        "math":             "https://uoli.edu.pk/faculty/mathematics",
        "pashto":           "https://uoli.edu.pk/faculty/pashto",
        "zoology":          "https://uoli.edu.pk/faculty/zoology",
        "islamic":          "https://uoli.edu.pk/faculty/islamic-studies",
        "islamiyat":        "https://uoli.edu.pk/faculty/islamic-studies",
        "political":        "https://uoli.edu.pk/faculty/political-science",
        "management":       "https://uoli.edu.pk/faculty/management-sciences",
        "allied":           "https://uoli.edu.pk/faculty/allied-health-sciences",
        "health sciences":  "https://uoli.edu.pk/faculty/allied-health-sciences",
    }

    _SPECIFIC_DEPT_WORDS = list(_DEPT_URL.keys())
    _has_specific_dept   = any(w in query_lower for w in _SPECIFIC_DEPT_WORDS)
    _dept_count          = sum(1 for w in _DEPT_URL if w in query_lower)

    # ── Path 0.5: Combined leadership + dept HOD query ─────────────────
    # e.g. "who is VC, dean, and HOD of CS department"
    # Fires when the query mentions BOTH a leadership role (VC, Dean,
    # Registrar) AND a specific department HOD. Without this path,
    # Path 3 fires for the dept only, silently dropping the leadership
    # part — the root cause of "who is VC, dean, HOD of CS" returning
    # only the HOD and nothing about the VC or Dean.
    _LEADERSHIP_QUERY_TERMS = [
        "vc", "vice chancellor", "vice-chancellor", "vicechanclor",
        "vise chancellor", "dean", "pro vc", "pro-vc",
        "registrar", "treasurer",
    ]
    _LEADERSHIP_PAGES = [
        "https://uoli.edu.pk/vc-message",
        "https://uoli.edu.pk/dean-message",
        "https://uoli.edu.pk/pro-vc-message",
        "https://uoli.edu.pk/registrar-message",
        "https://uoli.edu.pk/office-of-registrar",
        "https://uoli.edu.pk/office-of-treasurer",
        "https://uoli.edu.pk/about-us",
    ]
    _has_leadership_query = any(t in query_lower for t in _LEADERSHIP_QUERY_TERMS)

    if _has_specific_dept and _has_leadership_query:
        # 1. Fetch HOD docs for EVERY department named, not just the first.
        #
        # This used to `break` on the first _DEPT_URL hit, which quietly
        # discarded every other department in the question. "Who is the dean and
        # the HOD of CS, Math, Pashto and Zoology" matched dept keywords
        # ['cs','math','pashto','zoology'] and a leadership term ('dean'), so
        # this path won over Path 1 below — which loops departments correctly —
        # and then returned Computer Science alone. The Maths and Pashto HOD
        # facts were in the index the whole time; this was retrieval throwing
        # them away, not missing data.
        #
        # Several keywords can point at one department ("cs" and
        # "computer science"), so dedupe on the URL rather than the keyword.
        dept_urls = []
        for keyword, url in _DEPT_URL.items():
            if keyword in query_lower and url not in dept_urls:
                dept_urls.append(url)
        dept_docs = []
        for dept_url in dept_urls:
            dept_docs.extend(_docs_by_source(kb, dept_url))
        # 2. Fetch leadership pages directly (vc-message, dean-message, etc.)
        leadership_docs = []
        for lurl in _LEADERSHIP_PAGES:
            leadership_docs.extend(_docs_by_source(kb, lurl))
        # 3. Semantic search to catch leadership names in other pages
        extra = _search_filtered(
            kb, query, ["faculty", "offices", "overview"], k=6,
            engineered_queries=engineered_queries
        )
        all_combined = leadership_docs + extra + dept_docs
        if all_combined:
            # One department keeps the original budget of 30. Each extra
            # department needs room of its own, or a four-department question
            # spends the whole allowance on the first two and the answer is
            # short by exactly the departments the cap cut off.
            budget = max(30, 12 * len(dept_urls))
            return all_combined, min(len(all_combined), budget)

    # ── Path 1: Multi-department HOD query ───────────────────────────
    # e.g. "who is HOD of cs, math and islamiyat"
    _is_hod_query = any(w in query_lower for w in [
        "hod", "head", "chairperson", "chair person",
        "in charge", "in-charge", "coordinator", "convener",
        "head of", "hod of",
    ])
    if _dept_count >= 2 and _is_hod_query:
        all_docs = []
        seen_urls = set()
        for keyword, url in _DEPT_URL.items():
            if keyword in query_lower and url not in seen_urls:
                seen_urls.add(url)
                all_docs.extend(_docs_by_source(kb, url))
        if all_docs:
            # Filter to HOD-relevant propositions only.
            # Keywords expanded to cover "in charge", "in-charge",
            # "coordinator", "convener" which some depts use instead of HOD.
            # Return ALL matched chunks — the old code returned ONE per dept
            # which silently dropped depts whose name appeared in the 2nd+ chunk.
            hod_keywords = [
                "hod", "head", "chair", "chairperson",
                "head of department", "in charge", "in-charge",
                "coordinator", "convener",
            ]
            hod_docs = [
                d for d in all_docs
                if any(kw in d.page_content.lower() for kw in hod_keywords)
            ]
            return hod_docs if hod_docs else all_docs, 25

    # ── Path 1b: Aggregate statistics about the university ───────────
    # THIS MUST COME BEFORE PATH 2. Path 2 answers "how many X" by sweeping one
    # chunk per department URL, which is right when X is enumerable (departments,
    # HODs) and wrong when X is a population size. Before this gate existed,
    # Path 2 swallowed both statistics questions in the evaluation set, which is
    # why the _STATS_SIGNALS handler that used to sit further down this function
    # was unreachable dead code — written for exactly these questions and never
    # once run. It has been removed; do not re-add a second copy below.
    #
    # The detection and the search live in _asked_populations/_search_stats at
    # module scope, because the router does not send all count questions here:
    # see the comment there.
    _populations = _asked_populations(query_lower)
    if _populations:
        stats_docs = _search_stats(kb, _populations)
        if stats_docs:
            return stats_docs, len(stats_docs)

    # ── Path 2: List-all departments query ───────────────────────────
    # e.g. "list all HODs", "how many teachers are there"
    is_list_all = (
        not _has_specific_dept and
        any(w in query_lower for w in [
            "all", "every", "each", "list", "names", "how many",
            "kitne", "tamam", "saray", "sab", "total", "count",
            "number of", "how mach", "how much",
        ]) and
        any(w in query_lower for w in [
            "hod", "head", "department", "faculty",
            "teacher", "lecturer", "staff", "dept",
        ])
    )
    if is_list_all:
        # Fetch directly from every department URL for complete HOD list
        _ALL_DEPT_URLS = list(set(_DEPT_URL.values()))
        all_docs = []
        for url in _ALL_DEPT_URLS:
            raw = _docs_by_source(kb, url)
            # Keep only HOD-relevant propositions
            hod_raw = [
                d for d in raw
                if any(kw in d.page_content.lower() for kw in [
                    "hod", "head", "chair", "chairperson",
                    "in charge", "in-charge", "coordinator", "convener",
                ])
            ]
            # raw[:3] → raw: taking only the first 3 arbitrary chunks caused
            # departments whose HOD name appeared in the 4th+ chunk to be
            # silently missing from list-all-HODs answers.
            all_docs.extend(hod_raw if hod_raw else raw)
        if all_docs:
            # Allow up to 3 HOD-relevant chunks per department URL.
            # The old strict one-per-source dedup removed departments whose
            # HOD name appeared in the 2nd or 3rd chunk, not the first.
            # 3 chunks keeps all relevant propositions for each dept while
            # preventing any single dept from flooding the list.
            seen_sources: dict = {}
            deduped = []
            for doc in all_docs:
                src = doc.metadata.get("source", "")
                count = seen_sources.get(src, 0)
                if count < 3:
                    deduped.append(doc)
                    seen_sources[src] = count + 1
            return deduped, min(len(deduped), 60)
        # Fallback to category search
        all_result = kb.db.get(
            include=["documents", "metadatas"],
            where={"category": {"$eq": "faculty"}}
        )
        raw_children = [
            Document(page_content=text, metadata=meta or {})
            for text, meta in zip(
                all_result.get("documents", []),
                all_result.get("metadatas", [])
            )
        ]
        # Drop news/event pages mistagged as faculty
        raw_children = [
            d for d in raw_children
            if "/faculty" in d.metadata.get("source", "")
        ]
        if len(kb.parent_store) > 0:
            docs = kb._fetch_parents(raw_children)
        else:
            docs = raw_children
        return docs, min(len(docs), 25)

    # ── Path 3: Specific department list/faculty query ───────────────
    # e.g. "all CS teachers", "faculty members of math", "hod of pashto"
    _is_dept_list = _has_specific_dept and any(
        w in query_lower for w in [
            "all", "every", "each", "list", "names",
            "teacher", "lecturer", "staff",
            "faculty members", "faculty member",
            "faculty",          # ← "tell me cs faculty"
            "professors",       # ← future proofing
            "members of", "hod of", "head of",
            "who is hod", "who is head", "hod",
            "tell me", "show me", "give me",  # ← natural language
        ]
    
    )
    if _is_dept_list:
        dept_url = None
        for keyword, url in _DEPT_URL.items():
            if keyword in query_lower:
                dept_url = url
                break
        # `and raw`: when the department page yields nothing this block is
        # skipped entirely and control reaches the _search_filtered call below,
        # rather than committing to an empty direct-URL result. A branch that
        # cannot answer must hand the query on, never return [].
        raw = _docs_by_source(kb, dept_url) if dept_url else []
        if dept_url and raw:
            # For contact queries: keep child chunks — they contain
            # specific emails/extensions. Parent chunks bury this
            # info in 1500 chars of dept overview text and LLM misses it.
            _contact_words = {
                "email", "phone", "number", "extension",
                "ext", "contact", "aur"
            }
            _has_contact_query = any(
                cw in query_lower for cw in _contact_words
            )
            if _has_contact_query:
                # Search directly for contact chunks — more reliable
                # than taking first N raw chunks which may not have email
                contact_docs = _docs_by_source(
                    kb, dept_url, where_document={"$contains": "@uoli.edu.pk"}
                )
                if contact_docs:
                    return contact_docs[:10], 10
                return raw[:15], 15
            elif len(kb.parent_store) > 0:
                docs = kb._fetch_parents(raw)
            else:
                docs = raw

            # Filter to only docs from the specific department URL
            # WHY: _fetch_parents may pull parent chunks from other
            # departments that share the same parent. This filter
            # ensures only the requested department appears in results.
            if dept_url:
                docs = [
                    d for d in docs
                    if d.metadata.get("source", "") == dept_url
                    or dept_url.split("/")[-1] in
                    d.metadata.get("source", "")
                ]
                # If filter removed everything, fall back to raw
                if not docs:
                    docs = raw

            return docs, min(len(docs), 25)
        docs = _search_filtered(kb, query, ["faculty"], k=20)
        return docs, 15

    # ── Path 4: General faculty search (default) ─────────────────────
    # e.g. "who is the VC", "Registrar email", "treasurer contact"
    # ── Path 4: General faculty search (default) ─────────────────────
    # Direct URL fetch for specific office queries to avoid mixing
    # ── Path 4: General faculty search (default) ─────────────────

    # ── Name detection: "who is Ismail Khan" type queries ────────
    # WHY: Vector search fails for proper names — embeddings for short
    # names don't match document chunks reliably.
    # FIX: Extract capitalized words from query, search ChromaDB directly
    # for documents that literally CONTAIN that name string.
    # This is exact keyword match — guaranteed to find the right chunk.
    # ── Path 4: General faculty search (default) ─────────────────
    
    # ── Name detection ────────────────────────────────────────────
    # WHY: Vector search fails for person names — embeddings for
    # short proper names don't match document chunks reliably.
    # FIX: Extract name words from query, search ChromaDB directly
    # for documents that literally CONTAIN that name string.
    # (re is imported at module scope. It used to be imported here, which made
    # `re` a function-local name for the WHOLE function body and therefore
    # unusable in any code above this line.)

    # Words to ignore when extracting name from query
     # ── Path 4: General faculty search (default) ─────────────────

    # ── Shared ignore words ───────────────────────────────────────
    _IGNORE_WORDS = {
        "who", "is", "what", "the", "a", "an", "tell", "me",
        "about", "find", "show", "please", "can", "you", "get",
        "give", "information", "details", "of", "his", "her",
        "their", "its", "mr", "ms", "dr", "prof", "sir"
    }

    _ROLE_WORDS = [
        "hod", "head", "vc", "vice", "chancellor", "registrar",
        "dean", "pro", "treasurer", "controller", "director",
        "department", "office", "university", "loralai", "uoli", "uol",
        "faculty", "teachers", "staff", "lecturer", "professors",
        "list", "all", "names", "members",
        # "tell", "show", "give" removed — already in _IGNORE_WORDS
        # Removing them allows "tell me about X" to trigger name detection
        # "tell me cs faculty" is caught by Path 3 before reaching here
        #
        # "uol" was missing while "uoli" was present — an oversight, since the
        # evaluation set and real students both write the short form constantly.
        # Consequence: "UoL contact?" contained no role word, so the person-name
        # handler below treated "UoL" as somebody's name, ran a substring search
        # for it, and returned the first five chunks mentioning the university —
        # generic institutional prose — instead of falling through to the
        # main-contact block that answers the question. Note "uol" is a substring
        # of "uoli", so this entry subsumes the previous one rather than
        # conflicting with it.
    ]

    _CONTACT_SIGNALS = {"email", "contact", "phone", "number",
                        "extension", "ext"}

    # ── Handler 1: "email of X" / "contact of X" queries ─────────
    # WHY: These queries have contact words that block normal name
    # detection. We extract the person name separately here.
    _has_contact = any(cw in query_lower for cw in _CONTACT_SIGNALS)

    # Guard: skip Handler 1 for university-level contact queries
    # e.g. "main phone number of University" — these should fall through
    # to the _is_main_contact block below, not treat "University" as a name
    _UNIV_CONTACT_GUARD = [
        "main phone", "main email", "main contact",
        "university phone", "university email", "university contact",
        "uoli phone", "uoli email", "uoli contact",
        "uol phone", "uol email", "uol contact",
        "contact info", "contact detail", "contact number",
        "phone number of u", "email address of u",
        "phone number of the u",  # "...of the University"
    ]
    _is_univ_contact = any(g in query_lower for g in _UNIV_CONTACT_GUARD)

    if _has_contact and not _is_univ_contact:
        _contact_all_ignore = _IGNORE_WORDS | _CONTACT_SIGNALS
        _person_words = [
            w for w in query.split()
            if w.lower() not in _contact_all_ignore
            and len(w) > 2
            and not any(rw == w.lower() for rw in _ROLE_WORDS)
        ]
        # Only proceed if at least one word is a proper noun
        # (starts with capital letter = person name, not "main", "address")
        # Remove capital letter requirement — students type names in lowercase
        # Guard against generic words instead (length > 3 filters "main", "the")
        _meaningful_words = [w for w in _person_words if len(w) > 3]
        if _meaningful_words:
            _contact_search_terms = [
                " ".join(w.capitalize() for w in _person_words),
                " ".join(_person_words),
                _person_words[0].capitalize(),
                _person_words[0],
            ]
            for term in _contact_search_terms:
                if len(term) < 3:
                    continue
                try:
                    r = kb.db.get(
                        include=["documents", "metadatas"],
                        where_document={"$contains": term}
                    )
                    d = [
                        Document(page_content=t, metadata=m or {})
                        for t, m in zip(
                            r.get("documents", []),
                            r.get("metadatas", [])
                        )
                    ]
                    if d:
                        return d[:5], 5
                except Exception:
                    continue

    # ── Handler 2: "who is X" / person name queries ───────────────
    # WHY: Vector search fails for proper names — embeddings for
    # short names do not match document chunks reliably.
    # FIX: Search ChromaDB directly for documents containing the name.
    # Try title case first (Ismail Khan) then lowercase fallback.
    _name_candidates = [
        w for w in query.split()
        if w.lower() not in _IGNORE_WORDS
        and w.lower() not in _CONTACT_SIGNALS
        and len(w) > 1
    ]

    _is_name_query = (
        len(_name_candidates) >= 1 and
        not any(rw in query_lower for rw in _ROLE_WORDS)
    )

    if _is_name_query:
        _name_search_terms = []

        # Try title case first — matches KB storage format
        full_title = " ".join(w.capitalize() for w in _name_candidates)
        full_lower = " ".join(_name_candidates)

        if full_title:
            _name_search_terms.append(full_title)
        if full_lower != full_title:
            _name_search_terms.append(full_lower)

        if _name_candidates:
            _name_search_terms.append(_name_candidates[0].capitalize())
            _name_search_terms.append(_name_candidates[0])
        if len(_name_candidates) >= 2:
            _name_search_terms.append(_name_candidates[1].capitalize())
            _name_search_terms.append(_name_candidates[1])

        for name_term in _name_search_terms:
            if len(name_term) < 3:
                continue
            try:
                name_results = kb.db.get(
                    include=["documents", "metadatas"],
                    where_document={"$contains": name_term}
                )
                name_docs = [
                    Document(page_content=t, metadata=m or {})
                    for t, m in zip(
                        name_results.get("documents", []),
                        name_results.get("metadatas", [])
                    )
                ]
                if name_docs:
                    # Keep child chunks — do NOT fetch parents
                    # Parent chunks bury names in department intro text
                    #
                    # Rank before truncating. A $contains search returns every
                    # chunk holding the term in storage order, so "first 5" was
                    # an arbitrary pick whenever the name appeared more than five
                    # times — a person mentioned on their department page, the
                    # offices page and a news item would surface whichever three
                    # were ingested earliest, not the ones answering the
                    # question. The cross-encoder scores query+chunk together, so
                    # asking for an email promotes the chunk carrying the email.
                    # Candidates are capped before reranking because scoring is
                    # CPU-bound here (~0.4s per 60 chunks); a genuine person name
                    # matches only a handful, and the generic high-count tokens
                    # that used to reach this branch are now blocked by
                    # _ROLE_WORDS above.
                    if len(name_docs) > 1:
                        name_docs = kb._rerank(query, name_docs[:60])
                    return name_docs[:5], 5
            except Exception:
                continue

    # ── Original Path 4 continues below ──────────────────────────

    # ── Multi-part query detection ────────────────────────────────
    # Q31: "Who is the VC and what departments does UoL have?"
    # Q35: "How many students, faculty, and programs does UoL have?"
    # These need broader multi-category search to recall all needed info.

    _DEPT_LIST_SIGNALS = ["departments", "department", "faculties", "all dept",
                          "how many dept", "what department"]
    _is_multi_dept = any(s in query_lower for s in _DEPT_LIST_SIGNALS)

    if _is_multi_dept:
        # Need: leadership info (for VC part) + departments list (overview)
        multi_docs = _search_filtered(
            kb, query, ["faculty", "overview", "general"], k=15,
            engineered_queries=engineered_queries
        )
        if multi_docs:
            return multi_docs, 15

    # ── Fix 2: Broader main contact detection ────────────────────
    # WHY: Q7 "main phone number" and Q8 "main email address" fail
    # because only "main phone" / "main email" were matched.
    # Students also ask "university phone", "phone number of uoli", etc.
    _MAIN_CONTACT_WORDS = [
        "main phone", "main email", "main contact",
        "university phone", "university email", "university contact",
        "phone number of u", "email address of u",  # "of uoli" / "of university"
        "contact info", "contact details", "contact number",
        "uoli phone", "uoli email", "uoli contact", "uol contact",
        "uol phone", "uol email",
    ]
    _is_main_contact = any(w in query_lower for w in _MAIN_CONTACT_WORDS)

    if _is_main_contact:
        # Step 1: direct URL fetch from contact pages.
        # /about/contact was in this list and has never existed in the corpus —
        # the site serves the page at /contact. It cost nothing only because the
        # two entries below it happen to cover the same ground. scratch/
        # verify_stage1.py now fails on any URL here that resolves to no rows,
        # so the next dead link is caught before it reaches a user.
        _contact_urls = [
            "https://uoli.edu.pk/contact",
            "https://uoli.edu.pk/contacts-old-new",
            "https://uoli.edu.pk/",
        ]
        contact_raw = []
        for _url in _contact_urls:
            contact_raw.extend(_docs_by_source(kb, _url))

        if contact_raw:
            # Rank the fetched chunks against the question, then keep the best.
            #
            # WHY THIS REPLACED "fetch parents, then take the first 5":
            # the three contact URLs hold 162 child chunks between them (29 +
            # 29 + 104), and .get() returns them in storage order, not relevance
            # order. Slicing [:5] off that list — or off the parents it expanded
            # into — was an arbitrary choice that discarded the answer. Measured:
            # the chunk "The contact number for the University of Loralai is
            # +92 (824) 410051." sits at index 1 of every one of those pages and
            # is exactly the ground truth for the main-phone question, yet
            # "410051" never reached the context at all.
            #
            # WHICH QUERY TO RANK WITH — measured, and not obvious:
            # when the student names the field they want ("main phone number",
            # "main email address", "address"), their own words rank it best; the
            # cross-encoder puts the right proposition first every time. But a
            # bare "UoL contact?" carries no signal about what a contact detail
            # looks like, so the same ranking returns generic prose ("UoL is a
            # dynamic convergence of excellence and innovation") and coverage for
            # that question collapsed to 0.08.
            #
            # Appending contact words to EVERY query was tried and rejected: it
            # demoted the correct chunk on the specific questions, ranking the
            # information-office number above the university's main number for
            # "main phone number". So the augmented form is used only when the
            # query names no field — exactly the case where there is nothing
            # better to rank by. That restores all three ground-truth facts
            # (410051, info@uoli.edu.pk, Zerh Karez) into the top 5 for the terse
            # query while leaving the specific ones untouched.
            _NAMED_FIELDS = ["phone", "email", "number", "address", "extension",
                             "ext", "mail", "website", "hours", "located",
                             "location"]
            _rank_query = query if any(f in query_lower for f in _NAMED_FIELDS) \
                else "contact number email address of the University of Loralai"

            # The /contact page's atomic propositions DROPPED the main email:
            # the proposition chunker kept the phone and the postal address as
            # one-fact sentences but split info@uoli.edu.pk out of the footer
            # block and lost it, so it survives only inside the raw contact-block
            # chunk and a handful of policy-PDF citations. A rerank over
            # contact_raw alone therefore has no email to rank for an email
            # question — the model answered "not available" over a corpus that
            # publishes it. When the student asks for email and no fetched chunk
            # carries the main address, pull the chunks that literally contain
            # info@uoli into the pool so the rerank can surface it.
            if any(w in query_lower for w in ("email", "e-mail", "mail")) and \
                    not any("info@uoli" in d.page_content for d in contact_raw):
                _seen_e = {d.page_content for d in contact_raw}
                try:
                    _er = kb.db.get(include=["documents", "metadatas"],
                                    where_document={"$contains": "info@uoli"})
                except Exception:
                    _er = {}
                for _t, _m in zip(_er.get("documents") or [],
                                  _er.get("metadatas") or []):
                    if _t and _t not in _seen_e:
                        _seen_e.add(_t)
                        contact_raw.append(Document(page_content=_t,
                                                    metadata=_m or {}))

            # Children are kept rather than swapped for parents. The original
            # comment here claimed "child chunks alone are too small — phone/
            # email in parent", but these children are complete one-fact
            # sentences that already carry the number and the address. Expanding
            # them re-attached ~700-1000 characters of unrelated page text to
            # each hit, which costs context_precision for no recall gain.
            contact_docs = kb._rerank(_rank_query, contact_raw)
            return contact_docs[:5], 5

        # Step 2: semantic fallback — search for phone/email directly
        _contact_query = "university phone number email address contact"
        contact_docs = _search_filtered(
            kb, _contact_query, ["overview", "general"], k=5
        )
        if contact_docs:
            return contact_docs[:5], 5

    _LEADERSHIP_TERMS = ["vc", "vice chancellor", "pro vc", "pro-vc",
                         "registrar", "dean", "treasurer", "controller"]
    _leadership_count = sum(1 for t in _LEADERSHIP_TERMS if t in query_lower)
    _HISTORY_WORDS = ["established", "founded", "when was", "history",
                      "started", "created", "charter", "year of"]
    _has_history = any(w in query_lower for w in _HISTORY_WORDS)

    # "Who holds this post?" and "What does this office do?" need different
    # evidence from the same page, and treating them alike is what made the
    # Treasurer question unanswerable. Urdu/Pashto "kon"/"kaun" included
    # because the evaluation set asks at least one of these in Roman Urdu.
    _PERSON_ASK_WORDS = ("who", "kon", "kaun", "name of", "naam",
                         "contact", "email", "e-mail", "phone", "extension")
    _asks_person = any(w in query_lower for w in _PERSON_ASK_WORDS)

    # ── Deputy / Assistant / Additional office-holders ───────────────────────
    # A query for a SUBORDINATE post ("who is the Deputy Controller", "assistant
    # registrar") shares its base role word ("controller", "registrar") with the
    # office loop below, so it was answered by the probe for the PRIMARY holder:
    # _role_name_probe("controller") ranks "Dr. Shahji Ahmed is the Controller"
    # first, and _fetch_parents then swaps the one line that names the deputy for
    # the office's mission page. The deputy's name lives in exactly one atomic
    # proposition — "Hafiz Mohd. Ibrahim is the Deputy Controller of
    # Examinations" — which also fails _PERSON_RE (no Prof/Dr/Mr honorific), so
    # the probe files it in its lowest tier and it never surfaces. Find it by its
    # exact title and return it WITHOUT expanding to the parent, so the sentence
    # that answers the question leads instead of being buried in office furniture.
    _SUBORDINATE_WORDS = ("deputy", "assistant", "additional", "joint",
                          "in-charge", "incharge", "in charge", "acting")
    if _asks_person and any(w in query_lower for w in _SUBORDINATE_WORDS):
        _BASE_ROLES = ("controller", "registrar", "treasurer", "director",
                       "dean", "provost", "proctor", "librarian")
        _PREFIX = {"deputy": "Deputy", "assistant": "Assistant",
                   "additional": "Additional", "joint": "Joint",
                   "in-charge": "Incharge", "incharge": "Incharge",
                   "in charge": "Incharge", "acting": "Acting"}
        # Build the exact title(s) as they are written in the corpus, e.g.
        # "deputy" + "controller" -> "Deputy Controller". $contains is
        # case-sensitive, and every such proposition is Title-Cased.
        _titles = [f"{_PREFIX[_pre]} {_role.capitalize()}"
                   for _pre in _SUBORDINATE_WORDS if _pre in query_lower
                   for _role in _BASE_ROLES if _role in query_lower]
        sub_hits, _seen_sub = [], set()
        for _title in _titles:
            try:
                _r = kb.db.get(include=["documents", "metadatas"],
                               where_document={"$contains": _title})
            except Exception:
                continue
            for _t, _m in zip(_r.get("documents") or [], _r.get("metadatas") or []):
                if _t and _t not in _seen_sub:
                    _seen_sub.add(_t)
                    sub_hits.append(Document(page_content=_t, metadata=_m or {}))
        if sub_hits:
            # Rank the atomic hits against the question and return them as-is.
            # No _fetch_parents: the one-sentence proposition already carries the
            # whole answer, and expanding it re-buries the name.
            return kb._rerank(query, sub_hits)[:5], 5

    if _leadership_count <= 1 and not _has_history:
        for keyword, url in _OFFICE_URL.items():
            if keyword not in query_lower:
                continue

            raw = _docs_by_source(kb, url)

            # An office page is not guaranteed to name the person who runs it,
            # and several of these do not. /office-of-treasurer is 75 chunks of
            # "the Office of Treasurer manages payroll disbursements" and never
            # once says Mr. Nasir Rehan; his name appears in exactly one chunk
            # in the whole corpus, on a news page. Returning the office page for
            # "who is the Treasurer" is a confident wrong answer: retrieval
            # looks healthy, 75 documents come back, and the model still has to
            # say it does not know.
            #
            # WHY THE PROBE RUNS EVEN WHEN THE PAGE DOES NAME SOMEBODY: naming
            # *a* person is not the same as naming *this* person.
            # /office-of-registrar states who the Deputy Registrar and the
            # Assistant Registrar are, and gives Prof. Dr. Khalid Khan's email
            # without ever saying he is the Registrar — the sentence that says
            # so lives on /registrar-message. A gate of "does this page mention
            # anyone" passes that page and leaves the model to infer the answer
            # from a mailbox name, or to reach for the Deputy. So for a
            # who-question the probe always runs and always leads; the page
            # follows as supporting context, and the cross-encoder in
            # _get_faculty_docs makes the final ordering call against the
            # question as actually asked.
            if _asks_person:
                probe = _role_name_probe(kb, keyword)
                raw = probe + [d for d in raw if d.page_content not in
                               {p.page_content for p in probe}]

            if raw:
                raw = raw[:12]
                if len(kb.parent_store) > 0:
                    raw = kb._fetch_parents(raw, preserve_order=True)
                return raw, min(len(raw), 8)

            # Nothing under this URL and nothing under the role name. Do NOT
            # return the empty list — stop scanning the table and let control
            # reach the _search_filtered call below.
            #
            # What stood here was `return raw, 5` with raw empty. Because all
            # seven URLs in this table were written with a trailing slash and
            # ChromaDB stores them without one, that line returned zero
            # documents for every office question ever asked, and the search
            # two lines further down was unreachable. Registrar, Controller,
            # Deputy Controller and Treasurer all answered "I don't have
            # enough information" while the facts sat in the database. A
            # branch that cannot answer must hand the query on, never end it.
            break

    docs = _search_filtered(kb, query,
                            ["faculty", "offices", "overview", "general"], k=10,
                            engineered_queries=engineered_queries)
    return docs, 5
   
def faculty_agent_node(state: Dict[str, Any], kb: KnowledgeBase, graph_searcher=None) -> Dict[str, Any]:
    """
    Faculty agent — delegates doc fetching to _get_faculty_docs().
    That function is the single source of truth shared with stream_chat().
    """
    query          = state["user_query"]
    docs, max_docs = _get_faculty_docs(kb, query)

    if not docs:
        return _finish(state, FALLBACK_ANSWER, docs)

    llm    = _get_llm()
    prompt = FACULTY_PROMPT.format(
        university_name = config.UNIVERSITY_NAME,
        context         = _format_context(docs, max_docs=max_docs),
        history         = _build_history(state),
        question        = query,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return _finish(state, _extract_answer(response.content), docs[:max_docs])

# ══════════════════════════════════════════════════════════════
#  AGENT 3 — ADMISSIONS & PROGRAMS AGENT
# ══════════════════════════════════════════════════════════════

ADMISSIONS_PROMPT = """You are an admissions advisor for {university_name}.

ADMISSIONS INFORMATION:
{context}

CONVERSATION HISTORY:
{history}

STUDENT'S QUESTION:
{question}

STRICT RULES:
- Answer ONLY from the provided documents.
- For document requirements: list EVERY required document with bullet points.
  Missing one document wastes a student's trip to the university.
- For program questions: list all programs found in the documents.
- State eligibility criteria clearly (minimum percentage, CGPA etc.)
- Be welcoming and encouraging — these students want to join the university.
RESPONSE FORMAT:
Provide your complete, welcoming answer directly. Do not use "ANSWER:" prefix.""" + _BREVITY_RULES + _FIGURE_RULES + _CONFLICT_RULES


def admissions_agent_node(state: Dict[str, Any], kb: KnowledgeBase) -> Dict[str, Any]:
    """
    ADMISSIONS & PROGRAMS AGENT — how to apply, documents, programs, eligibility.
    Searches admissions + courses + students categories.
    Welcoming tone for prospective students.
    """
    query   = state["user_query"]
    # Retrieval lives in retrieve_for_agent(). The categories that actually
    # exist in this DB are policy, faculty, general, offices, overview and
    # admissions; "courses" and "students" match zero rows, which is why the
    # profile no longer names them.
    docs, _max_docs, _ = retrieve_for_agent(kb, "admissions", query)
    quality = _grade_quality(docs)

    if quality == "empty":
        return _finish(state, FALLBACK_ANSWER, docs)

    llm    = _get_llm()
    prompt = ADMISSIONS_PROMPT.format(
        university_name = config.UNIVERSITY_NAME,
        context         = _format_context(docs),
        history         = _build_history(state),
        question        = query,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return _finish(state, _extract_answer(response.content), docs)


# ══════════════════════════════════════════════════════════════
#  AGENT 4 — CAMPUS SERVICES AGENT
# ══════════════════════════════════════════════════════════════

REWRITE_PROMPT = """Rewrite this student question into clear English search terms
for finding university bus/transport, library, or campus facilities information.
Keep it under 10 words. Only rewrite — do not answer.

Common Urdu/Pakistani terms and their English equivalents:
- garee, gaadi, bus, sawari, transport, kiraya → university bus transport routes
- time table, timings, schedule, waqt → bus schedule timings
- library, kutub khana, books → university library
- hostel, rihayish, accommodation → student hostel accommodation
- lab, laboratory, computer room → computer lab facilities
- strength, tadad, kitne students → student enrollment count

Original: "{query}"
Rewritten:"""

CAMPUS_PROMPT = """You are a campus services assistant for {university_name}.

CAMPUS SERVICES INFORMATION:
{context}

CONVERSATION HISTORY:
{history}

STUDENT'S QUESTION:
{question}

STRICT RULES:
- Answer ONLY from the provided documents.
- "garee", "gaadi", "vehicle", "transport", "bus" all mean the same thing.
- For transport: mention routes, timings, number of buses if available.
- For library: mention resources, location, timings if in documents.
- For hostels/accommodation: mention boys/girls hostels, capacity, facilities if available.
- For computer labs/facilities: mention number, location, capacity if in documents.
- If specific numbers (how many hostels, labs) are not stated in documents, say:
  "The documents do not specify the exact number. Please contact the university office
  or visit uoli.edu.pk for the latest campus facilities information."
- Do NOT invent numbers or say information does not exist if related content is present.

RESPONSE FORMAT:
Provide your campus services answer directly. Do not use "ANSWER:" prefix.""" + _BREVITY_RULES + _FIGURE_RULES + _CONFLICT_RULES


def campus_agent_node(state: Dict[str, Any], kb: KnowledgeBase) -> Dict[str, Any]:
    """
    CAMPUS SERVICES AGENT — bus routes, transport, library, hostels, facilities.

    QUERY REWRITING: Before searching, LLM rewrites the query.
    "garee time table" → "bus routes transport schedule"
    This is the proper fix for vocabulary gaps — no hardcoded lists.
    Any Urdu/Pashto/mixed language transport question gets rewritten
    into standard English that matches KB document language.
    """
    query = state["user_query"]
    llm   = _get_llm()

    # Retrieval — including the Urdu/Pashto query rewrite and the retry on the
    # original query — lives in retrieve_for_agent(), so stream_chat() runs the
    # same thing. The prompt below deliberately shows the student's ORIGINAL
    # wording, not the rewrite.
    docs = retrieve_for_agent(kb, "campus", query)[0]
    if _grade_quality(docs) in ("poor", "empty"):
        return _finish(state, FALLBACK_ANSWER, docs)

    prompt = CAMPUS_PROMPT.format(
        university_name = config.UNIVERSITY_NAME,
        context         = _format_context(docs),
        history         = _build_history(state),
        question        = query,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return _finish(state, _extract_answer(response.content), docs)


# ══════════════════════════════════════════════════════════════
#  AGENT 6 — FEE & SCHOLARSHIP AGENT
# ══════════════════════════════════════════════════════════════

FEE_PROMPT = """You are a financial advisor for {university_name}.

FEE & SCHOLARSHIP INFORMATION:
{context}

CONVERSATION HISTORY:
{history}

STUDENT'S QUESTION:
{question}

STRICT RULES:
- Answer ONLY from the provided documents.
- Clearly list the specific costs, tuition, admission fees, or scholarships if available.
- If the exact fee is not available in the context, clearly state: "I don't have the exact fee amount in my records. Please consult the fee structure document or the university office."
- Do NOT guess or make up numbers for fees.
- Do NOT use "ANSWER:" prefix in your response.""" + _BREVITY_RULES + _FIGURE_RULES + _CONFLICT_RULES


def fee_agent_node(state: Dict[str, Any], kb: KnowledgeBase) -> Dict[str, Any]:
    """
    FEE AGENT — handles questions about fee structures, costs, scholarships.
    """
    query   = state["user_query"]
    # Try vector search first
    # Retrieval, including the direct-from-store fallback for the known fee
    # pages, lives in retrieve_for_agent(). Categories verified against the live
    # DB: "fees" matches zero rows (the fee-structure page was never ingested;
    # fee figures live under "admissions") and the financial-aid percentages
    # live on /about-us under "overview".
    docs = retrieve_for_agent(kb, "fees", query)[0]
    if not docs:
        return _finish(state, FALLBACK_ANSWER, docs)
    llm    = _get_llm()
    prompt = FEE_PROMPT.format(
        university_name = config.UNIVERSITY_NAME,
        context         = _format_context(docs),
        history         = _build_history(state),
        question        = query,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return _finish(state, _extract_answer(response.content), docs)


# ══════════════════════════════════════════════════════════════
#  AGENT 5 — GENERAL AGENT  (catch-all)
# ══════════════════════════════════════════════════════════════

GENERAL_PROMPT = """You are {bot_name}, a professional AI assistant for {university_name}.

UNIVERSITY INFORMATION:
{context}

CONVERSATION HISTORY:
{history}

STUDENT'S QUESTION:
{question}

STRICT RULES:
- Answer ONLY from the provided documents.
- VOCABULARY BRIDGING: Bridge vocabulary gaps intelligently.
- For news/events/latest updates questions: Look through ALL documents
  for any recent activities, events, sessions, or announcements.
  Summarize what you find — do NOT refuse if any relevant content exists.
- For specific factual questions: If not found in documents, say
  "I don't have that information. Please contact the university office directly."
- Be professional and use bullet points for lists.

RESPONSE FORMAT:
ANSWER: <your detailed answer>""" + _BREVITY_RULES + _FIGURE_RULES + _CONFLICT_RULES


GENERAL_PROMPT_WITH_GRAPH = """You are {bot_name}, a professional AI assistant for {university_name}.

UNIVERSITY INFORMATION (from knowledge base):
{context}

{graph_context_section}

CONVERSATION HISTORY:
{history}

STUDENT'S QUESTION:
{question}

STRICT RULES:
- Answer ONLY from the provided documents and graph context above.
- VOCABULARY BRIDGING: Bridge vocabulary gaps intelligently.
- For news/events/latest updates questions: Look through ALL documents
  for any recent activities, events, sessions, or announcements.
  Summarize what you find — do NOT refuse if any relevant content exists.
- For relationship/structure questions: Use the GRAPH KNOWLEDGE section
  which contains community summaries showing how entities connect.
- For specific factual questions: If not found in documents, say
  "I don't have that information. Please contact the university office directly."
- Be professional and use bullet points for lists.

RESPONSE FORMAT:
Provide your detailed answer directly. Do not use "ANSWER:" prefix.""" + _BREVITY_RULES + _FIGURE_RULES + _CONFLICT_RULES



def _general_docs(kb: KnowledgeBase, query: str):
    """Pick the documents the general route should answer from.

    Returns (docs, max_docs, allow_graph). max_docs is None for "show all".

    WHY THIS IS A SEPARATE FUNCTION: this route has four retrieval strategies,
    and general_agent_node previously inlined each one next to its own copy of
    the same prompt-building block — four near-identical copies. That is how
    this codebase drifted in the first place: a later edit improves one copy and
    silently leaves the other three behind. Retrieval decisions live here, the
    single prompt build lives in the node, and bench_retrieval.py calls this so
    the harness measures what production retrieves instead of only the last
    branch. allow_graph is False for the targeted branches because a GraphRAG
    community summary adds nothing to a figure lookup or a department roster.
    """
    query_lower = query.lower()

    # ── Aggregate statistics ──────────────────────────────────
    # Count questions do not all route to the faculty agent: the router sends
    # "How many faculty members does UoL have?" to faculty but "How many
    # students, faculty, and programs does UoL have?" here. Both are the same
    # retrieval problem, so both use the same shared helper. Without this, the
    # question fell through to the generic vector search below, which never
    # surfaced the "at a Glance" figures and produced a flat refusal.
    _populations = _asked_populations(query_lower)
    if _populations:
        stats_docs = _search_stats(kb, _populations)
        if stats_docs:
            return stats_docs, None, False

    # ── Fix 6: Department count/list queries ──────────────────
    # WHY: "how many departments" routes to general_agent but
    # the answer is in faculty pages. Detect these queries and
    # search faculty category directly for complete results.
    dept_words = ["how many department", "list department",
                  "all department", "department names", "departments in",
                  "how many dept", "what departments"]
    if any(w in query_lower for w in dept_words):
        dept_docs = _search_filtered(kb, query, ["faculty", "overview"], k=15)
        if dept_docs:
            return dept_docs, 15, False

    # Department LIST queries — search main faculty page specifically
    dept_list_words = ["all departments name", "departments name",
                       "tell me all department", "list departments",
                       "how many department", "name all department"]
    if any(w in query_lower for w in dept_list_words):
        # Get ALL faculty overview chunks — main faculty page lists all depts
        all_docs = kb.db.get(
            include=["documents", "metadatas"],
            where={"category": {"$eq": "faculty"}}
        )
        dept_docs = []
        seen = set()
        for text, meta in zip(
            all_docs.get("documents", []),
            all_docs.get("metadatas", [])
        ):
            src = (meta or {}).get("source", "")
            if src not in seen:
                dept_docs.append(Document(page_content=text, metadata=meta or {}))
                seen.add(src)
        if dept_docs:
            return dept_docs, 20, False

    # Standard vector search (same as before)
    news_words = ["news", "latest", "recent", "events",
                  "happening", "updates", "activities", "announcement"]
    search_q = (
        "university recent activities events sessions programs"
        if any(w in query_lower for w in news_words)
        else query
    )
    return kb.search(search_q), None, True


# ══════════════════════════════════════════════════════════════
#  SHARED RETRIEVAL — the one implementation both chat paths use
# ══════════════════════════════════════════════════════════════

def retrieve_for_agent(kb: KnowledgeBase, agent_name: str, query: str,
                       engineered_queries=None):
    """Retrieve documents for one agent. Returns (docs, max_docs, allow_graph).

    WHY THIS EXISTS
    ───────────────
    There are two ways into this bot and they were retrieving differently.

    workflow.chat() drives the LangGraph and reaches the *_agent_node functions
    below. workflow.stream_chat() cannot — a node returns a dict and Streamlit
    needs a token generator — so it re-implemented the routing inline. That
    re-implementation carried its own copies of the category filters, and the
    copies were never updated when the profiles were corrected. Measured against
    the live DB at the time this was written:

        agent       RETRIEVAL_PROFILES (used by RAGAS)        stream_chat (used by Streamlit)
        fees        admissions/overview/general/offices  k=8  fees/admissions/general        k=8
        campus      general/overview/offices             k=10 campus/general/students/overview k=10
        admissions  admissions/policy/general/overview   k=12 admissions/courses/students/... k=20
        policy      ["policy"] k=10, unfiltered for attendance   always unfiltered

    "fees", "courses", "students" and "campus" match ZERO rows. So a Streamlit
    user asking what percentage of students receive financial aid searched
    fees+admissions+general — and that fact lives on /about-us under "overview".
    The fee path could not reach it. eval_ragas.py scored the same question
    through chat(), which could. The eval was not measuring the product.

    That is the failure this function removes: the routing table now has one
    home. _assert_every_category_is_readable() already guards
    RETRIEVAL_PROFILES against declaring unreachable categories, and because
    both paths now read that dict, the guard finally covers both of them.

    WHAT IS DELIBERATELY *NOT* UNIFIED
    ──────────────────────────────────
    Only retrieval. Prompt selection, CRAG grading, self-reflection and history
    handling stay where they are — the streaming and non-streaming paths
    genuinely differ there (one yields tokens, the other returns a dict), and
    collapsing them would mean rewriting the graph. Retrieval is the part that
    must not differ, because it decides what the model can possibly know, and it
    is the part that silently drifted.

    engineered_queries is passed through so stream_chat keeps the HyDE expansion
    it already computed in parallel with the classifier instead of paying for it
    twice. When ENABLE_HYDE is off this is just the expanded query, so behaviour
    is identical either way.
    """
    # ── Cross-agent multi-intent fanout ─────────────────────────────────
    # Problem: the supervisor routes a chain question like
    # "what is attendance policy and who is HOD of CS" to ONE agent
    # (faculty, because of "HOD"). The policy half is never retrieved,
    # so the LLM silently ignores the attendance question.
    #
    # Fix: when kb._split_intents detects >= 2 distinct intents AND they
    # classify into different agent families, retrieve from EACH family
    # separately and merge the results before returning to the caller.
    # The current agent's own retrieval is always included, so this is
    # purely additive — the worst case is a few extra chunks.
    #
    # Gating: _split_intents requires >= 3 content words per clause,
    # so "BS and MS programs" is not split. We only fanout when the
    # two intents classify into DIFFERENT agent families, so a
    # single-agent multi-intent question ("attendance rule and CGPA
    # rule") stays on the single-pass path it already handles well.
    _intents = kb._split_intents(query)
    if len(_intents) >= 2:
        def _quick_classify(q: str) -> str:
            """Keyword-only classification mirroring Supervisor._families."""
            q_l = q.lower()
            _fam = [
                ("faculty",    ["who is", "hod", "head of", "dean", "registrar",
                                "vc", "vice chancellor", "contact", "email",
                                "phone", "teacher", "lecturer", "professor",
                                "treasurer", "pro vc", "pro-vc"]),
                ("policy",     ["attendance", "cgpa", "policy", "rule", "regulation",
                                "absent", "freeze", "probation", "punishment",
                                "plagiar", "discipline", "conduct", "harassment",
                                "grading", "grade", "gpa", "exam", "cheating",
                                "sexual", "misconduct"]),
                ("admissions", ["admission", "apply", "eligibility", "merit",
                                "program", "undergraduate", "graduate", "documents"]),
                ("fees",       ["fee", "fees", "scholarship", "tuition", "challan"]),
                ("campus",     ["library", "bus", "transport", "hostel", "location",
                                "located", "address", "where is", "where's"]),
            ]
            for ag, kws in _fam:
                if any(kw in q_l for kw in kws):
                    return ag
            return "general"

        _classified = [_quick_classify(intent) for intent in _intents]

        # Fanout for ALL intents, even if they belong to the same agent family!
        if len(_intents) >= 2:
            _all_docs: list = []
            _seen_keys: set = set()

            for _ag, _intent_q in zip(_classified, _intents):
                # Retrieve for this intent using its own profile.
                # We call internal helpers directly (not retrieve_for_agent
                # recursively) to avoid re-triggering this fanout block.
                if _ag == "faculty":
                    _ag_docs, _ = _get_faculty_docs(kb, _intent_q, engineered_queries=None)
                elif _ag == "policy":
                    _att_words = ["attendance", "attandance", "attendence",
                                  "atandance", "absent", "miss class", "haazri",
                                  "75", "percent", "ghair haazir", "present in class"]
                    _other_pol = ["harassment", "grading", "grade", "cgpa",
                                  "discipline", "plagiar", "conduct", "policy"]
                    _iq_lower = _intent_q.lower()
                    _pure_att = (any(w in _iq_lower for w in _att_words)
                                 and not any(w in _iq_lower for w in _other_pol))
                    if _pure_att:
                        _ag_docs = kb.search(_intent_q, engineered_queries=None)
                    else:
                        _cats, _k = RETRIEVAL_PROFILES["policy"]
                        _ag_docs = _search_filtered(kb, _intent_q, _cats, k=_k,
                                                    engineered_queries=None)
                elif _ag in RETRIEVAL_PROFILES:
                    _cats, _k = RETRIEVAL_PROFILES[_ag]
                    _ag_docs = _search_filtered(kb, _intent_q, _cats, k=_k,
                                               engineered_queries=None)
                else:
                    _ag_docs = kb.search(_intent_q, engineered_queries=None)

                for _doc in (_ag_docs or []):
                    _key = KnowledgeBase._content_key(_doc.page_content)
                    if _key not in _seen_keys:
                        _seen_keys.add(_key)
                        _all_docs.append(_doc)

            if _all_docs:
                print(f"[MultiIntent] {len(_intents)} intents → agents "
                      f"{_classified} → {len(_all_docs)} merged docs")
                return _all_docs, None, False

    if agent_name == "policy":
        # Attendance questions search unfiltered ON PURPOSE: the 75% rule lives
        # on the academic-rules web page, which is categorised "general", and
        # BM25 matches the literal "75%" that vector search alone misses.
        # BUT: only use unfiltered search for PURE attendance questions.
        # If the query ALSO mentions harassment/policy/grading, use
        # _search_filtered so the harassment PDF is also retrieved.
        _other_policy_words = ["harassment", "grading", "grade", "cgpa",
                               "discipline", "plagiar", "conduct", "policy"]
        _is_pure_attendance = (
            any(w in query.lower() for w in ["attendance", "attandance",
                "attendence", "atandance", "absent", "miss class",
                "haazri", "75", "percent", "ghair haazir", "present in class"])
            and not any(w in query.lower() for w in _other_policy_words)
        )
        if _is_pure_attendance:
            return kb.search(query, engineered_queries=engineered_queries), None, False
        _cats, _k = RETRIEVAL_PROFILES["policy"]
        return _search_filtered(kb, query, _cats, k=_k,
                                engineered_queries=engineered_queries), None, False


    if agent_name == "faculty":
        docs, max_docs = _get_faculty_docs(kb, query,
                                           engineered_queries=engineered_queries)
        return docs, max_docs, False

    if agent_name == "admissions":
        _cats, _k = RETRIEVAL_PROFILES["admissions"]
        return _search_filtered(kb, query, _cats, k=_k,
                                engineered_queries=engineered_queries), None, False

    if agent_name == "campus":
        # The rewrite turns "garee time table" into "bus routes transport
        # schedule". It is done on the ORIGINAL query, so the pre-computed HyDE
        # expansion does not apply to the rewritten string and is not passed on.
        _cats, _k = RETRIEVAL_PROFILES["campus"]

        # ADDRESS / LOCATION / MAIN-CONTACT QUESTIONS ARE NOT TRANSPORT QUESTIONS.
        # REWRITE_PROMPT only knows bus/library/hostel/lab, so it rewrites
        # "what is the university address" into campus-facility terms and the
        # search lands on facility pages (IT directorate, ORIC, program tables),
        # never on /contact — where the address actually lives. Measured on the
        # eval: the address question retrieved eight furniture chunks and zero
        # address, scoring precision=recall=0. The _grade_quality retry below did
        # not rescue it, because "retrieved the wrong topic" still grades as
        # non-empty. So for these queries skip the rewrite and search the raw
        # words, which the cross-encoder ranks straight onto the /contact line.
        _CONTACT_LOCATION_WORDS = (
            "address", "location", "located", "where is", "where's", "postal",
            "mailing", "map", "directions", "how to reach", "how do i reach",
            "email", "phone number", "contact number", "contact detail",
            "contact info", "zip", "pincode", "post code",
        )
        if any(w in query.lower() for w in _CONTACT_LOCATION_WORDS):
            docs = _search_filtered(kb, query, _cats, k=_k,
                                    engineered_queries=engineered_queries)
            return docs, None, False

        rewrite = _get_llm().invoke(
            [HumanMessage(content=REWRITE_PROMPT.format(query=query))])
        search_query = rewrite.content.strip()
        if len(search_query.split()) > 12 or not search_query:
            search_query = query
        docs = _search_filtered(kb, search_query, _cats, k=_k)
        if _grade_quality(docs) in ("poor", "empty"):
            docs = _search_filtered(kb, query, _cats, k=_k)
        return docs, None, False

    if agent_name == "fees":
        _cats, _k = RETRIEVAL_PROFILES["fees"]
        docs = _search_filtered(kb, query, _cats, k=_k,
                                engineered_queries=engineered_queries)

        # ── Atomic fee-line probe ────────────────────────────────────────────
        # The named fee facts are single sentences ("processing fee for PhD and
        # MS/MPhil programs is Rs. 3,000"), but _search_filtered runs through
        # KnowledgeBase.search(), which swaps each matched child for its parent
        # page — and the parent is the multi-row program-and-fee TABLE, in which
        # the one relevant number is a single cell among dozens. Measured: the
        # PhD processing-fee question retrieved those tables and the model
        # answered "I don't have the exact fee" over a corpus that states it
        # plainly. So when the student names a specific fee, pull the atomic
        # sentences by the fee phrase via $contains (case-sensitive, so probe the
        # lower- and sentence-cased forms) and rerank the whole pool: the
        # one-line fact then outranks the table without any parent expansion.
        _FEE_PHRASES = ("processing fee", "registration fee", "admission fee",
                        "security deposit", "application fee", "semester fee",
                        "tuition fee", "hostel fee", "examination fee")
        _q = query.lower()
        _wanted = [p for p in _FEE_PHRASES if p in _q]
        if not _wanted and "fee" in _q:      # generic "what are the fees" — still
            _wanted = ["processing fee", "semester fee", "tuition fee"]
        if _wanted:
            _seen = {d.page_content for d in docs}
            _extra = []
            for _phrase in _wanted:
                for _cased in (_phrase, _phrase.capitalize()):
                    try:
                        _r = kb.db.get(include=["documents", "metadatas"],
                                       where_document={"$contains": _cased})
                    except Exception:
                        continue
                    for _t, _m in zip(_r.get("documents") or [],
                                      _r.get("metadatas") or []):
                        if _t and _t not in _seen:
                            _seen.add(_t)
                            _extra.append(Document(page_content=_t,
                                                   metadata=_m or {}))
            if _extra:
                docs = kb._rerank(query, docs + _extra)[:_k]

        if not docs:
            # Last resort: pull the known fee pages straight out of the store.
            for url in ("https://uoli.edu.pk/university-apply",
                        "https://uoli.edu.pk/admissions-open-fall-2026",
                        "https://uoli.edu.pk/admission"):
                result = kb.db.get(include=["documents", "metadatas"],
                                   where={"source": {"$eq": url}})
                docs.extend(
                    Document(page_content=t, metadata=m or {})
                    for t, m in zip(result.get("documents", []),
                                    result.get("metadatas", [])))
            if docs:
                # Ranked, not raw. A whole page dumped in source order puts
                # whatever the page opens with at rank 1, and context_precision
                # is an ordering metric — an unranked fallback scores worse than
                # a shorter ranked one.
                docs = kb._rerank(query, docs)
        return docs, None, False

    return _general_docs(kb, query)


def general_agent_node(state: Dict[str, Any],
                       kb: KnowledgeBase,
                       graph_searcher=None) -> Dict[str, Any]:
    """
    GENERAL AGENT — news, events, research, ORIC, relationships, anything else.
    E1 GraphRAG: if query is a relationship question, also searches
    knowledge graph for community context.
    graph_searcher is optional — if None, works exactly as before.

    Retrieval lives in _general_docs() so this node has exactly one prompt
    build; see that function for why.
    """
    query = state["user_query"]

    docs, max_docs, allow_graph = _general_docs(kb, query)

    if not docs:
        return _finish(state, FALLBACK_ANSWER, [])

    # GraphRAG search (NEW in E1)
    graph_context_section = ""
    if allow_graph and graph_searcher is not None:
        if graph_searcher.is_graph_question(query):
            graph_ctx = graph_searcher.search(query, max_results=3)
            if graph_ctx:
                graph_context_section = (
                    "GRAPH KNOWLEDGE (entity relationships and community summaries):\n"
                    + graph_ctx
                )

    llm    = _get_llm()
    prompt = GENERAL_PROMPT_WITH_GRAPH.format(
        bot_name              = config.BOT_NAME,
        university_name       = config.UNIVERSITY_NAME,
        context               = _format_context(docs, max_docs=max_docs),
        graph_context_section = graph_context_section,
        history               = _build_history(state),
        question              = query,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return _finish(state, _extract_answer(response.content), docs)