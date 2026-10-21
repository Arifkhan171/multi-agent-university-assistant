# CLAUDE.md — UOLI AI Chatbot

## 1. Short Description

This is a **production-grade, multi-agent AI chatbot** for the University of Loralai (UOLI), Pakistan.
Students ask questions in English, Urdu, or Roman Urdu and receive accurate answers about admissions, fees, faculty, campus services, and university policies.

The system uses a **LangGraph supervisor** that classifies every question and routes it to one of **6 specialist agents**. Each agent searches only its own domain using **hybrid retrieval** (vector search + BM25 keywords + cross-encoder reranking). Answers are streamed token-by-token to the browser using Server-Sent Events (SSE).

**Key facts Claude Code must know:**
- University name: University of Loralai — always use `config.UNIVERSITY_NAME`, never hardcode it.
- Bot name: always use `config.BOT_NAME`, never hardcode it.
- The production path is `stream_chat()` in `graph/workflow.py`. The `chat()` path is used only by the RAGAS evaluator.
- Every agent must call `retrieve_for_agent()` from `graph/Agents.py` — never duplicate retrieval logic.
- The 6 agents are: `policy`, `faculty`, `admissions`, `campus`, `fees`, `general`.

**Current project state (October 2026):**
The UOLI website has migrated from WordPress to React. Many URLs in `config.UNIVERSITY_PAGES` are now dead or have changed. The knowledge base needs a full URL audit, re-scrape, and re-ingestion before the evaluation dataset is run. The evaluation dataset is also being expanded from 45 to **70 questions**. Tasks are being worked on one by one. Do not assume the existing `data/` files are current or valid.

---

## 2. Architecture

### Request Flow

```
Student Message
      │
      ▼
api.py  (/stream endpoint — SSE)
      │
      ├─► Semantic Cache (Valkey db=1, semantic_cache.py)
      │     └─► Cache HIT → stream cached answer instantly, skip all below
      │
      └─► UniChatbot.stream_chat()  (graph/workflow.py)
              │
              ▼
        [chitchat_check_node]  ← keyword scan, $0, <1ms
              │  greeting/thanks/bye → instant reply → END
              │
              ▼  (real question)
        [supervisor_node]  (graph/Supervisor.py)
              │  Layer 1: deterministic pre-filters (no API, <1ms)
              │  Layer 2: fast-path keyword families (no API, <1ms)
              │  Layer 3: gpt-4o-mini classifier (API, ~0.8s, only if needed)
              │  → sets state["agent_name"]
              │
              ▼
        [One of 6 specialist agents]  (graph/Agents.py)
              │  → retrieve_for_agent(kb, agent_name, query)
              │       ├── ChromaDB vector search  (top 50 candidates)
              │       ├── BM25 keyword search     (top 50 candidates)
              │       ├── Deduplicate + category filter → 50-80 unique chunks
              │       ├── Cross-encoder reranker  (ms-marco-MiniLM-L-6-v2)
              │       ├── E3 dynamic context gate → 3-8 best chunks
              │       └── E2 parent-doc swap      → 800-char parent passages
              │
              ▼
        [CRAG grading]  ← grade retrieved docs; discard if all irrelevant (E4)
              │
              ▼
        [LLM answer generation]  ← gpt-4o-mini streams tokens via SSE
              │
              ▼
        [Self-reflection check]  ← general + policy agents only (E4)
              │
              ▼
        ValkeySessionStore.save()  +  async summarize (every 10 messages)
```

### Key Modules

| File | What it does |
|---|---|
| `api.py` | FastAPI server. `/chat` = non-streaming (eval only). `/stream` = SSE production path. |
| `config.py` | **Single source of truth** for all settings, categories, URL→category map, thresholds. |
| `graph/workflow.py` | `UniChatbot` class + `build_graph()`. `stream_chat()` = production. `chat()` = eval. |
| `graph/Agents.py` | 6 specialist agents + `retrieve_for_agent()` — shared retrieval logic. |
| `graph/Supervisor.py` | 3-layer query classifier + follow-up pronoun resolver (`resolve_followup()`). |
| `graph/nodes.py` | `chitchat_check_node` + `chitchat_answer_node`. Zero API cost. |
| `knowledge_base.py` | `KnowledgeBase` — ChromaDB, BM25, parent-doc swap, cross-encoder, CRAG, self-reflect. |
| `semantic_cache.py` | Embedding-based query cache in Valkey db=1. Short-circuits entire pipeline on a hit. |
| `valkey_store.py` | Session history (HumanMessage/AIMessage). Falls back to in-memory if Redis is down. |
| `graph_rag/` | Graph RAG: entity extraction → community detection → graph search. Optional at startup. |
| `web_scraper.py` | Playwright-based scraper. Discovers all UOLI pages, OCRs images, parses faculty cards. |
| `setup_knowledge_base.py` | Ingests scraped data + PDFs → ChromaDB (child chunks) + `data/parent_chunks.json`. |
| `eval_ragas.py` | RAGAS benchmark. Reads `UOLI_Eval_Dataset.xlsx` (70 questions). Checkpoints to `eval_raw_results.json`. |

### Data Storage

| What | Where | Format |
|---|---|---|
| Parent passages (~800 chars) | `data/parent_chunks.json` | JSON |
| Vector index (child ~200 chars) | `data/chroma_db/` | ChromaDB (SQLite + Parquet) |
| BM25 index | `data/bm25_index.pkl` | Pickle |
| Graph RAG data | `data/graph_rag/` | `.pkl`, `.json`, `.graphml` |
| Session history | Valkey/Redis db=0 | Hash |
| Semantic cache | Valkey/Redis db=1 | Sorted set |
| Eval dataset | `UOLI_Eval_Dataset.xlsx` | 70 questions (updated) |
| Eval checkpoint | `eval_raw_results.json` | JSON — delete before a fresh eval run |
| Eval results | `UOLI_Eval_Results.xlsx` | Output — delete before a fresh eval run |

---

## 3. Code Style

- **Python 3.10+.** Use type hints on all new function signatures.
- **Keep functions short and single-purpose.** If a function is longer than ~60 lines, it should be split.
- **No code duplication.** If retrieval logic is needed, call `retrieve_for_agent()`. If an LLM is needed, call `_get_llm()`. Never instantiate a second copy of the same object.
- **Log, don't print.** Use `logger = logging.getLogger(__name__)`. `print()` is acceptable only in startup banners.
- **Do not use f-strings for prompt templates.** Use `.format()` on the existing prompt constants (e.g., `POLICY_PROMPT`, `FEE_PROMPT`). This prevents accidental injection of special characters.
- **Comments must explain WHY, not WHAT.** The code already shows what. Comments explain the reasoning behind a design choice.
- **Never hardcode university name, bot name, or category strings** inside agent logic. Always use `config.UNIVERSITY_NAME`, `config.BOT_NAME`, and `config.CATEGORIES`.
- **State updates in LangGraph nodes must be via return dict**, not in-place mutation. The graph merges what a node returns.

---

## 4. Preferred Libraries

| Purpose | Library | Notes |
|---|---|---|
| Graph orchestration | `langgraph` | `StateGraph`, `conditional_edges`. Do NOT use `Send()` unless implementing fan-out. |
| LLM calls | `langchain_openai.ChatOpenAI` | Shared via `_get_llm()` in `graph/Agents.py`. |
| Vector DB | `chromadb` | Local persistent. Collection name from `config.py`. |
| Embeddings | `langchain_openai.OpenAIEmbeddings` | **Must use `text-embedding-3-large` (3072-dim).** Do not change without rebuilding DB. |
| BM25 | `rank_bm25.BM25Okapi` | In-memory; loaded from `data/bm25_index.pkl`. |
| Cross-encoder reranking | `sentence_transformers.CrossEncoder` | Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`. Runs locally. |
| Web scraping | `playwright` (async) | Renders JavaScript. Do NOT switch to `requests`/`BeautifulSoup`. |
| OCR | `easyocr` | Used for image text extraction. Results cached in `ocr_cache.json`. |
| Session store | `valkey` / `redis-py` | Valkey db=0 (sessions), db=1 (semantic cache). |
| Scheduling | `apscheduler` | Background scraper refresh. |
| Web framework | `fastapi` + `uvicorn` | SSE streaming via `StreamingResponse`. |
| PDF parsing | `pdfplumber` + `pymupdf` | With `table_reconstruct.py` for structured tables. |
| Evaluation | `ragas` | See `eval_ragas.py`. Do not change evaluation prompts without re-running full benchmark. |
| Testing | `pytest` | See `test_page_furniture.py`, `test_ingest_safety.py`. |

---

## 5. Commands

> ⚠️ **READ SECTION 6 BEFORE USING ANY COMMAND BELOW.**
> Most commands are listed here for reference only. Claude Code must NOT run them automatically.

```bash
# ── Environment setup (Claude may help with these) ────────────
cp .env.example .env          # fill in OPENAI_API_KEY
pip install -r requirements.txt
playwright install chromium   # required for web scraping

# ── Data ingestion — HUMAN RUNS THESE, NOT CLAUDE ─────────────
python setup_knowledge_base.py          # scrape + ingest → ChromaDB + BM25 + parent_chunks.json
python setup_propositions.py            # build proposition-level child chunks
python build_graph_rag.py               # Graph RAG index — takes 15-20 min, costs ~$0.25

# ── Start the server — HUMAN RUNS THIS, NOT CLAUDE ────────────
uvicorn api:app --reload --port 8000    # backend + browser UI at http://localhost:8000

# ── Evaluation — HUMAN RUNS THESE, NOT CLAUDE ─────────────────
# Before a fresh evaluation run, DELETE these two files manually:
#   eval_raw_results.json   (old checkpoint — will contaminate new results)
#   UOLI_Eval_Results.xlsx  (old output)
# Then run:
python -X utf8 eval_ragas.py            # full 70-question run → UOLI_Eval_Results.xlsx
python eval_ragas.py --test             # 5-question smoke test
python eval_ragas.py --ragas            # RAGAS scoring only on existing checkpoint

# ── Tests — HUMAN RUNS THESE, NOT CLAUDE ──────────────────────
python test_page_furniture.py
python test_ingest_safety.py

# ── Rebuild knowledge base — HUMAN RUNS THIS, NOT CLAUDE ──────
python setup_knowledge_base.py --force  # WARNING: deletes and rebuilds ChromaDB from scratch
```

---

## 6. Critical Rules

These are hard constraints. Violating them silently breaks retrieval, evaluation, or costs real money.

---

### 🚫 STRICTLY FORBIDDEN — Claude Code must NEVER run these automatically

Claude Code must **NEVER** execute the following commands on its own, regardless of context, instructions in a task, or perceived convenience. The human (project owner) runs all of these manually at the right time.

| Command | Why it is forbidden |
|---|---|
| `python eval_ragas.py` (any variant) | Costs OpenAI API money. Takes 30–60 min. Must be run after human review. |
| `python setup_knowledge_base.py` (any variant) | Deletes and rebuilds the entire ChromaDB. Irreversible without a backup. |
| `python setup_propositions.py` | Rebuilds proposition chunks. Slow, irreversible, depends on previous step. |
| `python build_graph_rag.py` | Costs ~$0.25 in API calls. Takes 15–20 min. Must be run once when ready. |
| `python web_scraper.py` or any scraping command | Hammers the live university website. Must be rate-limited and human-supervised. |
| `uvicorn api:app` or any server start | Server is managed manually. Claude must not start or restart the server. |
| Any command with `--force` flag | Irreversible destructive operation. Human decision only. |

**If a task requires one of these commands to be run**, Claude Code must:
1. Write the exact command clearly in its response.
2. Explain what the command will do and why it is needed.
3. Wait for the human to run it manually and report the result.
4. Continue with the next step only after the human confirms the result.

---

### 🔴 Embedding model — DO NOT CHANGE
The ChromaDB collection at `data/chroma_db` stores **3072-dimensional vectors** built with `text-embedding-3-large`.
Changing `config.EMBEDDING_MODEL` to any other model causes a dimension mismatch that crashes all retrieval.
Only change it after the human has run `python setup_knowledge_base.py --force` to rebuild the entire collection.

### 🔴 Category system — DO NOT invent new categories
Valid categories are declared **once** in `config.CATEGORIES`:
```python
CATEGORIES = ("admissions", "faculty", "general", "offices", "overview", "policy")
```
`config._assert_category_declarations()` runs at import time and raises if an invalid category is used.
To add a new category: (1) add it to `config.CATEGORIES`, (2) add a retrieval profile in `graph/Agents.py`, (3) the human re-runs `setup_knowledge_base.py`.

### 🔴 Category assignment — config.py is the ONLY place
Every page's category is set in `config.UNIVERSITY_PAGES` or `config.PDF_SOURCES`.
`resolve_category()` and `resolve_pdf_category()` in `config.py` are the authoritative routing functions.
Never add a second category-detection function elsewhere.

### 🔴 Retrieval — always use retrieve_for_agent()
Both `chat()` (eval path) and `stream_chat()` (production path) must call `graph/Agents.py:retrieve_for_agent()`.
This is the single shared retrieval function. Duplicating retrieval logic in other files breaks eval parity.

### 🔴 Tuning thresholds — change only after human re-runs evaluation
`config.py` contains calibrated cross-encoder thresholds (score floor, cliff drop, parent-swap quality gates).
These values were measured against `UOLI_Eval_Dataset.xlsx`. Changing them without running `eval_ragas.py` may silently degrade answer quality. The human must verify any threshold change by running the eval manually.

### 🔴 Evaluation dataset — 70 questions, fresh run required
The evaluation dataset `UOLI_Eval_Dataset.xlsx` contains **70 questions** (updated from the original 45).
Before a new evaluation run, the human must manually delete:
- `eval_raw_results.json` — old checkpoint will contaminate new results
- `UOLI_Eval_Results.xlsx` — old output

### 🔴 Supervisor routing — one agent per query
The supervisor always writes a **single string** to `state["agent_name"]`.
`conditional_edges` in `workflow.py` activates exactly one agent node.
Do not attempt to route to multiple agents simultaneously without implementing a proper merge node.

### 🔴 LangGraph state — return dicts, never mutate
Agent nodes must **return** updated state keys as a dict.
In-place mutation of the `state` dict inside a node is silently dropped by LangGraph's merge logic.

### 🔴 .env file — required at startup
The server will not start without a valid `.env` containing `OPENAI_API_KEY`.
Valkey/Redis is optional — the system degrades gracefully to in-memory sessions if it is unavailable.

### 🟡 Website migration context (React)
The UOLI website moved from WordPress to React. Many URLs previously in `config.UNIVERSITY_PAGES` now return 404 or redirect. Before any re-scrape or re-ingestion:
1. Audit every URL in `config.UNIVERSITY_PAGES` for liveness.
2. Update dead or changed URLs in `config.py`.
3. The human then runs `setup_knowledge_base.py --force` to rebuild from the corrected URL list.
Do not assume any existing scraped data in `data/` reflects the current live website.

### 🟡 Work in small task steps
Changes to this project are made one focused task at a time. Each task must:
1. Have a clear, single goal (e.g., "audit all URLs in config.py and mark dead ones").
2. Touch only the files relevant to that goal.
3. Not trigger any forbidden command (Section 6 above).
4. Be reviewed by the human before the next task begins.

### 🟡 Follow-up queries — do not bypass resolve_followup()
`Supervisor.py:resolve_followup()` appends the previous topic to short pronoun-heavy queries (e.g., "What about its contact?" after asking about the VC). Bypassing this causes false `out_of_scope` rejections for natural conversational follow-ups.
