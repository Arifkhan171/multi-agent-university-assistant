# ═══════════════════════════════════════════════════════════════
#  config.py  —  Phase 4
#  All settings in one place. New settings marked with NEW.
# ═══════════════════════════════════════════════════════════════

import os
import re
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# ── LLM ─────────────────────────────────────────────────────
LLM_MODEL       = "gpt-4o-mini"
LLM_TEMPERATURE = 0.2

# ── Embeddings ───────────────────────────────────────────────
# WARNING: the live Chroma collection in data/chroma_db stores 3072-dim vectors,
# i.e. it was built with text-embedding-3-large. Changing this to
# text-embedding-3-small (1536-dim) causes a dimension mismatch and breaks
# retrieval entirely — it would require a full re-embed via
# setup_knowledge_base.py --force. Do not change without rebuilding the DB.
EMBEDDING_MODEL = "text-embedding-3-large"

# ── ChromaDB ─────────────────────────────────────────────────
CHROMA_DB_PATH    = "./data/chroma_db"
CHROMA_COLLECTION = "university_docs"

# ── Retrieval ────────────────────────────────────────────────
# Baseline values. Do NOT raise these without re-running eval_ragas.py:
# the extra tail documents are low-relevance and dilute context_precision.
TOP_K_RESULTS = 10
CANDIDATE_K   = 50

# ── Reranker ─────────────────────────────────────────────────
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# ── Context gating (E3) ──────────────────────────────────────
# The CrossEncoder in _rerank() already scores query+document together, so
# _compress_documents() gates on those scores instead of paying an LLM to redo
# the same judgement (the old _llm_extract path cost 3.5-9.4s per query and
# discarded answer-bearing documents).
#
# Scale: ms-marco-MiniLM emits raw logits, roughly +5..+11 strongly relevant,
# -2..+3 loosely related, -8..-11 irrelevant. Values below are calibrated
# against the eval set by calibrate_gate.py — re-run it before changing them.
CONTEXT_SCORE_FLOOR  = -7.0   # absolute cutoff: below this a doc is noise
CONTEXT_SCORE_MARGIN = 7.0    # retained for reference; cliff detection now leads
CONTEXT_CLIFF_DROP   = 2.0    # a gap this wide between consecutive scores marks
                              # the end of relevance (measured gaps at the true
                              # boundary are 3-6.5; within-relevance gaps are <1)
MIN_CONTEXT_DOCS     = 3      # hard backstop — never gate below this many
MAX_CONTEXT_DOCS     = 8      # upper bound: context_precision is average-precision,
                              # so a relevant doc at rank 10 scores worse than the
                              # same answer delivered in 6 documents

# ── Parent swap quality gate (E2) ────────────────────────────────────────────
# The child->parent swap exists to trade a precise proposition for the richer
# passage around it. Measured on this corpus, that trade is not always an
# upgrade: 21% of the 848 parents are under 150 characters and 506 of the 5558
# children (9.1%) map to a parent no bigger than 1.2x the child itself — one
# 183-character child has a 37-character "parent". Swapping there replaces a
# fact with a fragment and still spends a context slot. The healthy 91% have a
# median parent/child ratio of 6.8x, so the degenerate cases are a distinct tail
# and not a threshold judgement call.
PARENT_MIN_GAIN_RATIO    = 1.2   # parent must be at least this many times the
                                 # child's length to be worth swapping in
PARENT_MIN_CONTEXT_CHARS = 200   # a parent below this that does not even contain
                                 # the child's fact has neither the fact nor
                                 # useful surrounding context
PARENT_TIE_EPSILON       = 0.01  # when a child is kept alongside its parent, the
                                 # child leads by this much: it is what actually
                                 # matched and it holds what the parent lacks.
                                 # Small enough to break ties only, never to
                                 # reorder documents with genuinely different
                                 # scores.

# A PROVENANCE test, not a size test. A child is a proposition an LLM wrote FROM
# its parent's text, so a healthy child restates its parent's content words. When
# the parent contains almost none of them, the parent is not the passage this
# child came from — the parent text was damaged after the child was written, or
# the proposition was written from something else. Either way, swapping it in
# substitutes unrelated text for text that actually matched the query.
#
# Measured: fraction of a child's content words (stopwords and <=2-char tokens
# dropped) that appear in its parent, over all 4857 live children.
#
#   bucket                   n    mean  median   p10   p25   p75  <0.30  <0.50
#   healthy parent >=200  3871    0.82    0.88  0.55  0.75  1.00     1%     6%
#   healthy parent <200    515    0.55    0.50  0.20  0.33  0.80    23%    40%
#   truncated parent       314    0.17    0.14  0.00  0.00  0.27    78%    92%
#   address-stub parent    157    0.24    0.25  0.00  0.00  0.40    64%    87%
#
# 0.30 sits inside a real gap: healthy p10 is 0.55, so the cut costs 1% of good
# swaps while rejecting 78% and 64% of the two damaged populations. 0.50 would
# reject more damage but cost 6% of good swaps — six times the price for the last
# fifth of the benefit.
#
# MEASURED OUTCOME ON THE LIVE CORPUS (all 4857 child/parent pairs, after the
# institution's own name is excluded as a content word — see _ENTITY_STOP)
# ────────────────────────────────────────────────────────────────────────────────
# This rule is consulted ONLY for parents below PARENT_MIN_CONTEXT_CHARS. Within
# that band it rejects 172 swaps that the two size rules accept:
#
#     134  the 119-char postal-address stub          damage
#      27  truncated parent                          damage
#      11  other short parent                        mixed
#
# HOW THE BOUND WAS ARRIVED AT — it is a correction, not a design choice. The rule
# first shipped unbounded and rejected 229 swaps, the extra 57 being parents >=200
# chars (36 navigation menus, 13 bare link lists, 8 genuine prose). Those 57 were
# hand-read and the nav menus really are furniture, so blocking them was argued to
# be free. The eval run that followed disagreed:
#
#     faithfulness 0.980 -> 0.934   answer_relevancy 0.860 -> 0.804
#     context_precision 0.760 -> 0.744   context_recall 0.890 -> 0.775
#
# Recall lost 0.115 and precision gained nothing. Rejecting a LONG parent is the
# mechanism: it keeps one short proposition where a passage may carry the answer
# in words the child never reused, and word overlap cannot see that. Bounding the
# rule hands those 57 back and keeps all 134 address-stub and 27 truncated blocks,
# which are the population it was built for. Measurement over argument.
#
# The nav menus are not abandoned, only moved: they are deleted at INGESTION
# (test_ingest_safety.py proves the stripping), which removes them from the corpus
# once instead of declining them per query. Fighting them in both places is what
# cost the recall.
#
# WHY THE FAILURE MODE IS SAFE IN BOTH DIRECTIONS
# ───────────────────────────────────────────────
# Rejecting a swap does not remove a document; it keeps the CHILD instead of the
# parent. So a false rejection costs only the parent's surrounding prose while
# retaining the exact text the cross-encoder scored — nothing becomes
# unretrievable and no fact leaves the corpus. That cost is charged to
# context_recall, which is why the rule is bounded to parents too small to hold
# prose worth keeping rather than trusted everywhere.
#
# WHY IT IS NOT REDUNDANT WITH THE REBUILD
# ────────────────────────────────────────
# The rebuild removes today's damaged parents, and afterwards this rule will
# reject ~1% of pairs where rejection is near-free. It stays because the defect
# class does not: any future scrape, cleanup script or partial re-embed can
# desynchronise a parent from its children again, and this catches that from any
# cause without a model call.
PARENT_MIN_WORD_OVERLAP  = 0.30  # parent must contain at least this fraction of
                                 # the child's content words to be the passage
                                 # that child was written from

# ── Page-furniture demotion ──────────────────────────────────────────────────
# The scraped site footer ("University of Loralai, Quetta Road, Zerh Karez,
# Loralai, Balochistan. Phone: ... Email: info@uoli.edu.pk") was returned at
# RANK 0 for "Who is the Registrar?", "Who is the Deputy Controller of
# Examinations?" and "Who is the VC and what departments does UoL have?". It
# answers none of them, and because context_precision is average precision one
# irrelevant document at rank 0 is enough to halve a question's score — Q12's
# answer sat at rank 3 and scored exactly 1/4 = 0.250.
#
# Sized against the measured CrossEncoder scale (+5..+11 strongly relevant,
# -8..-11 irrelevant): 8.0 is enough to move furniture below every genuinely
# relevant document without pinning it so far down that a contact question
# cannot reach it. That question does not apply the penalty at all — see
# KnowledgeBase._asks_contact_details.
BOILERPLATE_PENALTY = 8.0

# Source-diversity cap in _rerank(): a document scoring within this much of the
# best document ignores the "max 2 chunks per source" limit. Needed because
# multi-part answers (the admission document checklist) span 3-4 chunks of one
# page, and a blind cap made those questions unanswerable.
RERANK_DIVERSITY_MARGIN = 2.0

# Adaptive second retrieval pass. If the best CrossEncoder score in pass 1 falls
# below this, search() retries with HyDE-expanded queries and no category filter.
# Measured separation is wide: queries that retrieve well top out at +2.4 to
# +8.6, queries that fail top out below zero. So this only fires on real misses.
WEAK_RETRIEVAL_SCORE = 2.0

# Master switch for that second pass. Measured cost is 5-9s per firing query
# (one LLM call plus a second search + rerank over an unfiltered pool), which is
# most of the end-to-end latency budget. On the eval set it fired on 5 questions
# and changed the final gated context on none of them, because the blocker there
# was reranker ordering, not candidate availability. Off until a measurement
# shows it recovers answers.
ENABLE_ADAPTIVE_RETRY = False

# ── Query Engineering ────────────────────────────────────────
ENABLE_HYDE = False   # Set False to disable HyDE (saves API cost)
# ── University ───────────────────────────────────────────────
UNIVERSITY_NAME = os.getenv("UNIVERSITY_NAME", "University of Loralai (UOLI)")
BOT_NAME        = os.getenv("BOT_NAME", "UOLI Assistant")

# ── FastAPI ──────────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 8000

# ── NEW: Redis ───────────────────────────────────────────────
# Redis stores conversation sessions so they survive restarts.
# If Redis is not running, the chatbot falls back to in-memory sessions.
REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379")
SESSION_TTL_HOURS = 24    # sessions expire after 24 hours of inactivity

# ── NEW: LangGraph Checkpointing ─────────────────────────────
# SQLite database file for LangGraph state checkpointing
CHECKPOINT_DB = "./data/checkpoints.db"

# ── NEW: University Website Scraping ─────────────────────────
# Full UOLI website — all key pages scraped and stored in KB.
#
# THIS IS THE ONLY PLACE A PAGE'S CATEGORY IS DECLARED. web_scraper.py used to
# carry a second, independent _detect_category() that disagreed with this list on
# 7 of the 37 URLs below and won silently, because nothing ever read this file's
# "category" keys — the scraper worked from its own GUARANTEED_PAGES list. That
# is why /undergraduate and /graduate-programs ended up in "policy", where the
# only reader is the policy agent, so the admissions agent could not see the
# program lists at all. Both functions are now replaced by resolve_category()
# at the bottom of this file, which reads the list below.
#
# Every category here must appear in CATEGORIES (see below) — that is asserted at
# import time, and again in graph/Agents.py against what the agents actually
# read, so a category that nothing can retrieve fails loudly instead of silently
# swallowing pages.
#
# The values below are the MEASURED-GOOD assignments, not the aspirational ones.
# Where a page's live category was already scoring well it is preserved and the
# reason recorded; the four categories this list used to invent ("students",
# "courses", "fees", "downloads") are gone because no agent ever read them.
UNIVERSITY_PAGES = [

    # ── MAIN ────────────────────────────────────────────────
    # Homepage: deliberately "general", NOT "overview". Its text is the welcome
    # blurb, the statistics strip and the News & Events listing — verb-rich
    # generic prose that the CrossEncoder scores highly on almost any query.
    # "general" is read by the admissions, fees and campus profiles but NOT by
    # the faculty profile, which keeps that prose out of "who is the HOD of X"
    # retrieval. Promoting it to "overview" would expose it to the faculty path.
    {"url": "https://uoli.edu.pk/",               "category": "general"},
    {"url": "https://uoli.edu.pk/about-us/",      "category": "overview"},
    {"url": "https://uoli.edu.pk/vc-message/",    "category": "overview"},
    {"url": "https://uoli.edu.pk/pro-vc-message/","category": "overview"},  # ← found during scrape
    {"url": "https://uoli.edu.pk/registrar-message/", "category": "overview"},  # ← found during scrape
    {"url": "https://uoli.edu.pk/dean-message/",  "category": "overview"},  # ← found during scrape
    # The contact page stays "general" for the same reason as the homepage: its
    # content IS the address/phone/email block, and "overview" would feed that
    # block into faculty retrieval, where it was measured at rank 0 for "Who is
    # the Registrar?" before BOILERPLATE_PENALTY was added.
    {"url": "https://uoli.edu.pk/contact/",       "category": "general"},

    # ── STUDENTS ────────────────────────────────────────────
    # Student-life page. Was declared "students", which no agent reads.
    {"url": "https://uoli.edu.pk/overview/",         "category": "general"},
    {"url": "https://uoli.edu.pk/admission/",         "category": "admissions"},
    # Program lists. Were declared "courses" (unread) and were actually stored as
    # "policy" (read only by the policy agent), so "what programs does UoL offer"
    # could not reach them from the admissions path. This is the fix.
    {"url": "https://uoli.edu.pk/undergraduate/",     "category": "admissions"},
    {"url": "https://uoli.edu.pk/graduate-programs/", "category": "admissions"},
    # Was declared "fees", which no agent reads — the fees profile reads
    # ["admissions", "overview", "general", "offices"]. Filed under "admissions"
    # so both the fees and admissions agents can retrieve it.
    {"url": "https://uoli.edu.pk/fee-structure/",     "category": "admissions"},
    {"url": "https://uoli.edu.pk/library/",           "category": "general"},
    {"url": "https://uoli.edu.pk/bus-routes/",        "category": "general"},
    {"url": "https://uoli.edu.pk/alumni/",            "category": "general"},

    # ── DEPARTMENTS / FACULTIES ─────────────────────────────
    {"url": "https://uoli.edu.pk/faculty/",                         "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/allied-health-sciences/",  "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/commerce/",                "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/computer-science/",        "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/education/",               "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/english/",                 "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/islamic-studies/",         "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/management-sciences/",     "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/mathematics/",             "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/pashto/",                  "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/political-science/",       "category": "faculty"},
    {"url": "https://uoli.edu.pk/faculty/zoology/",                 "category": "faculty"},

    # ── OFFICES ─────────────────────────────────────────────
    {"url": "https://uoli.edu.pk/offices/",                                    "category": "offices"},
    # Kept as "overview", not "offices", although it is plainly an office page.
    # The faculty profile reads both, so faculty questions are unaffected either
    # way; but "offices" is NOT in the admissions profile, so moving it there
    # would remove the registrar's registration/records content from admissions
    # retrieval. Q3 ("Who is the Registrar?") currently returns its answer at
    # rank 1 with this assignment. Change it only behind a bench_retrieval run.
    {"url": "https://uoli.edu.pk/office-of-registrar/",                        "category": "overview"},
    {"url": "https://uoli.edu.pk/office-of-treasurer/",                        "category": "offices"},
    {"url": "https://uoli.edu.pk/offices/directorate-of-it/",                  "category": "offices"},
    {"url": "https://uoli.edu.pk/offices/controller-of-examination-office/",   "category": "offices"},
    {"url": "https://uoli.edu.pk/oric/",                                       "category": "offices"},
    {"url": "https://uoli.edu.pk/dqe/",                                        "category": "offices"},
    {"url": "https://uoli.edu.pk/offices/fao/",                                "category": "offices"},
    {"url": "https://uoli.edu.pk/directorate-of-postgraduate-studies/",        "category": "offices"},

    # ── DOWNLOADS (text description — PDFs handled via PDF_SOURCES) ──
    # The page itself is a list of document titles and links; the documents
    # themselves are ingested separately as "policy" via PDF_SOURCES. Was
    # declared "downloads", which no agent reads.
    {"url": "https://uoli.edu.pk/downloads/", "category": "general"},
]

# ── PDF / DOCX Sources ────────────────────────────────────────
# Policy documents available as PDFs on the UOLI downloads page.
# These are downloaded at setup time and loaded into the KB.
# If you have local .docx copies, place them in data/docs/ instead.
PDF_SOURCES = [
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/UOL-Notification-of-Under-Graduate-Rules.pdf",
        "category": "policy",
        "label":    "UOL Undergraduate Rules",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/UOL-Notification-of-Graduate-Rules.pdf",
        "category": "policy",
        "label":    "UOL Graduate Rules",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/UOL-Notificaion-of-Disciplinary-Rules.pdf",
        "category": "policy",
        "label":    "UOL Disciplinary Rules",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/The-Balochistan-Universities-Act-2022.pdf",
        "category": "policy",
        "label":    "Balochistan Universities Act 2022",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/HEC_Policy_on_Sexual_Harassment.pdf",
        "category": "policy",
        "label":    "HEC Sexual Harassment Policy",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/Anti-Drug-Policy-For-Higher-Education-Institutions-In-Pakistan.pdf",
        "category": "policy",
        "label":    "Anti-Drug Policy",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/UOL-Notification-of-ED-Rules.pdf",
        "category": "policy",
        "label":    "UOLi E&D Rules",  # found on homepage, was missing from original list
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/Act-2016-Balochistan-Protection-Against-Harassment-of-Women.pdf",
        "category": "policy",
        "label":    "BPAS Anti-Harassment Policy",  # found on homepage, was missing
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2025/12/UOL-Notification-of-Conduct-Rules.pdf",
        "category": "policy",
        "label":    "UOLi Conduct Rules",  # found on homepage, was missing
    },
    # ── Crawler-missed PDFs (from pdf_skipped.md entries 10–12) ─────────────
    # These URLs were NOT reached by the crawler because their source pages
    # were either empty or the link was dead during the crawl. Adding them
    # to PDF_SOURCES forces a direct download attempt at next setup run.
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2026/07/Advertisement-for-Visiting-Faculty.pdf",
        "category": "faculty",
        "label":    "Advertisement for Visiting Faculty 2026",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2026/07/Bidding-Document-Report-05-07-2026.pdf",
        "category": "general",
        "label":    "Bidding Document Report July 2026",
    },
    {
        "url":      "https://uoli.edu.pk/wp-content/uploads/2026/07/NIT-Report.pdf",
        "category": "general",
        "label":    "Notice Inviting Tender (NIT) Report 2026",
    },
]

# How often to auto-scrape the website (hours between each scrape)
# Set to 0 to disable automatic scraping
SCRAPE_INTERVAL_HOURS = 0

# Maximum characters to keep from each scraped page
# Prevents storing entire websites in the knowledge base
MAX_PAGE_CHARS = 15000  # raised from 8000 — long faculty/policy pages can be 12,000+ chars

# ── Fallback Contact ──────────────────────────────────────────
FALLBACK_PHONE   = "(082) 2441174"
FALLBACK_WEBSITE = "uoli.edu.pk"
FALLBACK_ADDRESS = "University of Loralai, Loralai, Balochistan"


# ═══════════════════════════════════════════════════════════════
#  CATEGORY RESOLUTION — the single source of truth
# ═══════════════════════════════════════════════════════════════
# One function decides the category of every ingested page: resolve_category().
# It replaces web_scraper._detect_category(), which was an independent second
# implementation that disagreed with UNIVERSITY_PAGES on 7 of 37 URLs and won,
# because nothing read UNIVERSITY_PAGES at all.
#
# TWO STRUCTURAL FLAWS IN THE OLD FUNCTION, BOTH FIXED HERE:
#
# 1. It matched bare substrings anywhere in the URL, and its news list was
#    checked FIRST. The list contained "research", "library", "alumni",
#    "gallery" and "tender", so any future URL containing those letters anywhere
#    was forced to "general" before any other rule could run — a genuine
#    research-office page at /oric/research-projects would have been filed as
#    news. Matching here is on PATH SEGMENTS and hyphen-delimited TOKENS within a
#    segment, so "research" as a word in one segment cannot decide a page whose
#    identity is set by another segment.
#
# 2. It needed a hand-maintained list of ~30 news slugs, so every new event page
#    the university published was miscategorised until somebody added its slug.
#    No such list exists here. News pages are recognised structurally instead:
#    a reference page has a short slug ("about-us", "vc-message",
#    "faculty-and-staff" — at most three hyphen-separated tokens) while an event
#    headline is long ("uol-participates-in-7th-national-olive-gala"). Only the
#    PREFIX rules apply that length guard; token-equality rules such as
#    "admission" do not need it, because a long slug containing the word
#    "admission" genuinely is admissions content.
#
# Returning None means DO NOT INGEST. That is used only for machinery a crawler
# will inevitably reach — WordPress plumbing, feeds, tag/pagination archives —
# never for pages with real text. Archive pages such as /about-old-123 are
# deliberately NOT excluded: they were checked, and they carry unique facts
# ("established in 2012", "charter granted by the Balochistan Provincial
# Assembly") that appear nowhere else in the corpus. Dead pages cannot be
# recognised from a URL, so 404 detection is content-based and lives in
# web_scraper._looks_dead().

# The complete category vocabulary. Every value written into chunk metadata must
# be one of these, and graph/Agents.py asserts at import that every one of them
# is read by at least one retrieval profile. Four names this file used to declare
# — "students", "courses", "fees", "downloads" — are absent because no agent ever
# read them, so any page filed under them was unreachable by construction.
CATEGORIES = ("admissions", "faculty", "general", "offices", "overview", "policy")

FALLBACK_CATEGORY = "general"

# A reference page's slug is short; an event headline is long. Used only by the
# prefix rules below, as a guard against a news slug that happens to start with a
# reference word ("faculty-development-workshop-held" is news, not faculty).
_MAX_REFERENCE_TOKENS = 3

# First-path-segments that are unambiguously office pages.
_OFFICE_SLUGS = frozenset({
    "offices", "oric", "dqe", "fao", "registrar", "treasurer", "controller",
})

# Token equality, not prefix: a long slug containing one of these really is
# admissions content ("admissions-open-fall-2026").
_ADMISSION_TOKENS = frozenset({"admission", "admissions", "apply", "applying"})

_POLICY_TOKENS = frozenset({
    "rules", "policy", "policies", "conduct", "discipline", "disciplinary",
    "act", "statute", "statutes", "regulation", "regulations", "ordinance",
    # Downloads-page PDFs name the policy in their filename, so the policy
    # agent can only find them if these words route to "policy".
    "plagiarism", "harassment", "harrassment",
})

# PDF documents about procurement — tenders, bidding documents, notices
# inviting tender. No student query is about these; wherever they land they
# only dilute retrieval, so resolve_pdf_category()/reclassify_pdf() drop them.
_PROCUREMENT_TOKENS = frozenset({
    "bidding", "tender", "tenders", "nit", "quotation", "quotations",
    "procurement", "corrigendum", "eoi",
})

_CONTACT_TOKENS = frozenset({"contact", "contacts"})

# Path segments that mean "this is site machinery, not a page". A 300-page crawl
# of a WordPress site reaches all of these, and each one duplicates text that is
# already indexed from the real page it points at.
_EXCLUDE_SEGMENTS = frozenset({
    "wp-content", "wp-admin", "wp-includes", "wp-json", "wp-login.php",
    "feed", "rss", "comments", "trackback", "xmlrpc.php",
    "tag", "category", "author", "search",
    # Image galleries carry no retrievable text. The one instance in the corpus,
    # /about/gallary, is in fact a 404 page that was ingested as 220 characters
    # of "Oops! Something went Wrong..." plus the site footer.
    "gallery", "gallary",
})


def _url_path(url) -> str:
    """The path of a URL, lowercased, without scheme, host, query or fragment.

    Always starts with "/" and never ends with one, so "/faculty",
    "https://uoli.edu.pk/faculty/" and "HTTPS://UOLI.EDU.PK/faculty?x=1#y" all
    normalise to the same key. The homepage normalises to "/".
    """
    text = str(url or "").strip().lower()
    text = text.split("#", 1)[0].split("?", 1)[0]
    for scheme in ("https://", "http://", "//"):
        if text.startswith(scheme):
            text = text[len(scheme):]
            text = text.split("/", 1)[1] if "/" in text else ""
            break
    text = text.strip("/")
    return "/" + text if text else "/"


def _segments(path: str):
    return [seg for seg in path.split("/") if seg]


def _tokens(segment: str):
    """Hyphen/underscore/dot-separated words inside one path segment."""
    return [t for t in re.split(r"[-_.]+", segment) if t]


# path -> category, built once from the declarations above. An explicit
# declaration always beats a structural rule.
_DECLARED_CATEGORIES = {
    _url_path(page["url"]): page["category"] for page in UNIVERSITY_PAGES
}


def _is_excluded(segs) -> bool:
    if any(seg in _EXCLUDE_SEGMENTS for seg in segs):
        return True
    # /page/2, /blog/page/13 — pagination archives, pure excerpt duplication.
    if len(segs) >= 2 and segs[-2] == "page" and segs[-1].isdigit():
        return True
    return False


def _structural_category(segs):
    """Category from URL shape alone, or None if no rule applies."""
    if not segs:
        return FALLBACK_CATEGORY

    first  = segs[0]
    ftoks  = _tokens(first)
    ftokset = set(ftoks)
    ltokset = set(_tokens(segs[-1]))
    short  = len(ftoks) <= _MAX_REFERENCE_TOKENS

    # Contact pages BEFORE the /about rule, so /about/contact is treated as a
    # contact page rather than an institutional-overview page. "general" rather
    # than "overview" is deliberate: the faculty profile reads "overview", and
    # the address/phone/email block was measured at rank 0 for "Who is the
    # Registrar?" — see the /contact/ note in UNIVERSITY_PAGES.
    if (_CONTACT_TOKENS & ftokset) or (_CONTACT_TOKENS & ltokset):
        return "general"

    # Faculty and department pages, including any department added in future:
    # /faculty/<anything> matches on the first segment alone.
    if first == "faculty" or (first.startswith("faculty") and short):
        return "faculty"
    if "department" in ftokset and short:
        return "faculty"

    # Offices and directorates. The length guard is looser here because real
    # office slugs run to four tokens ("directorate-of-postgraduate-studies").
    if first in _OFFICE_SLUGS:
        return "offices"
    if first.startswith(("office", "directorate")) and len(ftoks) <= 5:
        return "offices"

    # Admissions: application pages, program lists and fee tables.
    if _ADMISSION_TOKENS & ftokset:
        return "admissions"
    if first.startswith(("undergraduate", "graduate", "postgraduate",
                         "program", "fee", "prospectus")) and short:
        return "admissions"

    # Policy: rules, acts and codes of conduct, in any segment.
    if (_POLICY_TOKENS & ftokset) or (_POLICY_TOKENS & ltokset):
        return "policy"

    # The institution talking about itself.
    if first == "about" or (first.startswith("about") and short):
        return "overview"
    if first.endswith("-message") and short:
        return "overview"

    return None


def resolve_category(url):
    """The category for a page, or None if the page must not be ingested.

    Resolution order, most authoritative first:
      1. exclusion  — site machinery a crawler reaches but no student asks about
      2. declaration — an exact URL listed in UNIVERSITY_PAGES
      3. structure   — path-segment rules, so URLs not in the list still land
                       correctly (/faculty/<new-department>/ -> faculty)
      4. fallback    -> "general"

    The return value is guaranteed to be in CATEGORIES or None; the closing
    assertions in this module check that over every declared URL, and callers
    clamp anything unexpected rather than writing an unreadable category.
    """
    segs = _segments(_url_path(url))
    if _is_excluded(segs):
        return None
    declared = _DECLARED_CATEGORIES.get(_url_path(url))
    if declared:
        return declared
    category = _structural_category(segs) or FALLBACK_CATEGORY
    return category if category in CATEGORIES else FALLBACK_CATEGORY


def _pdf_filename_signal(pdf_url) -> str:
    """What a PDF's own filename says it is.

    A PDF lives under /wp-content/uploads, a path _is_excluded() swallows, so
    its category cannot come from its own URL the ordinary way — which is why
    the old code fell back to the linking page and filed every downloads-page
    PDF as "general". But the filename is descriptive: UoLi-Harrassment-
    Policy.pdf, UOL-Notification-of-Disciplinary-Rules.pdf and Plagiarism-
    Committee-2023.pdf each name what they are. The last path segment alone
    tells us where the document belongs.

    Returns one of: "drop" (procurement noise), "policy", "admissions", or ""
    (no signal — the caller decides from the linking pages or leaves it be).
    """
    segs = _segments(_url_path(pdf_url))
    toks = set(_tokens(segs[-1])) if segs else set()
    if _PROCUREMENT_TOKENS & toks:
        return "drop"
    if _POLICY_TOKENS & toks:
        return "policy"
    if _ADMISSION_TOKENS & toks:
        return "admissions"
    return ""


def resolve_pdf_category(pdf_url, referrers=None):
    """Category for a discovered PDF, or None to skip it entirely.

    Order, most authoritative first:
      1. filename signal — the document names itself policy / admissions, or
                           names itself procurement noise (-> None, skip it).
      2. linking pages   — the most specific category among the pages that
                           link it, ignoring the "general" fallback.
      3. fallback        -> "general".

    Replaces the referrer-only rule that filed every downloads-page PDF as
    "general" (resolve_category("/downloads") is "general") and so hid every
    policy document from the policy agent.
    """
    sig = _pdf_filename_signal(pdf_url)
    if sig == "drop":
        return None
    if sig:
        return sig
    if referrers:
        specific = sorted(
            c for c in {resolve_category(r) for r in referrers}
            if c and c != "general"
        )
        if specific:
            return specific[0]
    return FALLBACK_CATEGORY


def reclassify_pdf(pdf_url, current_category):
    """Correct an already-stored PDF category from its filename.

    Used to re-index the existing corpus without re-scraping: the parent store
    already holds the category the scrape assigned, and we only want to
    (a) promote a document whose filename clearly names it policy/admissions,
    or (b) drop procurement noise. Everything else keeps the category it has,
    so a PDF a linking page already placed sensibly (e.g. an office
    notification filed "offices") is never downgraded to "general".
    """
    sig = _pdf_filename_signal(pdf_url)
    if sig == "drop":
        return None
    if sig:
        return sig
    return current_category


def _assert_category_declarations():
    """Fail at import if the declarations above are internally inconsistent.

    Three ways this file can be wrong, all of which used to fail silently:
      - a page declared with a category outside CATEGORIES (that is how
        "courses", "fees", "students" and "downloads" became unreachable);
      - a PDF declared with a category outside CATEGORIES;
      - a declared page that an exclusion rule swallows, which would silently
        drop a page somebody deliberately listed.
    """
    bad_pages = {p["url"]: p["category"] for p in UNIVERSITY_PAGES
                 if p["category"] not in CATEGORIES}
    if bad_pages:
        raise ValueError(
            "config.UNIVERSITY_PAGES declares categories outside CATEGORIES "
            f"{CATEGORIES}: {bad_pages}. A category no agent reads makes those "
            "pages unretrievable — add it to a profile in graph/Agents.py "
            "RETRIEVAL_PROFILES or file the page under an existing category."
        )

    bad_pdfs = {p["url"]: p["category"] for p in PDF_SOURCES
                if p["category"] not in CATEGORIES}
    if bad_pdfs:
        raise ValueError(
            f"config.PDF_SOURCES declares categories outside CATEGORIES "
            f"{CATEGORIES}: {bad_pdfs}"
        )

    swallowed = [p["url"] for p in UNIVERSITY_PAGES
                 if resolve_category(p["url"]) is None]
    if swallowed:
        raise ValueError(
            "config._EXCLUDE_SEGMENTS excludes pages that UNIVERSITY_PAGES "
            f"explicitly declares: {swallowed}"
        )


_assert_category_declarations()
#python -X utf8 setup_propositions.py
#python -X utf8 eval_ragas.py
#uvicorn api:app --reload