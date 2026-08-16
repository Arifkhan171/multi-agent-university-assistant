# 🎓 UOLI University AI Chatbot — Phase 5

> **Production-ready** AI chatbot for the University of Loralai (UOLI), built with LangGraph, RAG, semantic caching, and a streaming chat UI.

---

## ✨ Features

| Feature | Details |
|---------|---------|
| 🧠 **LangGraph Multi-Agent** | Supervisor + specialist agents (fees, admissions, programs, general) |
| 📚 **Hybrid RAG** | Semantic search + BM25 + Parent-Document retrieval |
| 🕸️ **Graph RAG** | Entity extraction → community summaries for complex queries |
| ⚡ **Semantic Cache** | Valkey/Redis cache — instant replies for repeated questions |
| 🔄 **Live Scraping** | Auto-scheduled website scraping with APScheduler |
| 📄 **PDF Ingestion** | Multi-strategy PDF parsing with OCR fallback |
| 💬 **Streaming UI** | Real-time token streaming via SSE (Server-Sent Events) |
| 🧪 **RAGAS Evaluation** | Full evaluation pipeline with faithfulness, relevancy metrics |
| 🗂️ **Session Memory** | Per-user conversation history via Valkey |

---

## 🏗️ Architecture

```
User (Browser)
    │  SSE stream
    ▼
FastAPI  (api.py)
    │
    ├── SemanticCache  ──→  Valkey db=1
    │
    └── UniChatbot  (graph/workflow.py)
            │
            ├── Supervisor  (graph/Supervisor.py)
            │       └── Routes to specialist agents
            │
            ├── KnowledgeBase  (knowledge_base.py)
            │       ├── ChromaDB  (semantic search)
            │       ├── BM25Index (keyword search)
            │       └── ParentDocStore (context expansion)
            │
            └── GraphRAG  (graph_rag/)
                    ├── EntityExtractor
                    ├── CommunityBuilder
                    └── GraphSearcher
```

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/Arifkhan171/university-ai-chatbot.git
cd university-ai-chatbot
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your OpenAI API key
```

### 3. Build the Knowledge Base

```bash
# Ingest PDFs
python setup_knowledge_base.py

# Build proposition chunks (optional, improves retrieval)
python setup_propositions.py

# Build Graph RAG index (optional, improves complex queries)
python build_graph_rag.py
```

### 4. Start the Server

```bash
uvicorn api:app --reload --port 8000
```

Open your browser at **http://localhost:8000** 🎉

---

## 📁 Project Structure

```
├── api.py                   # FastAPI server — main entry point
├── config.py                # All settings (env vars, model names, paths)
├── knowledge_base.py        # Hybrid RAG: ChromaDB + BM25 + Parent-Doc
├── web_scraper.py           # University website scraper
├── semantic_cache.py        # Semantic query cache (Valkey)
├── valkey_store.py          # Redis/Valkey session storage
├── session_store.py         # Session management
├── pdf_ingest.py            # PDF ingestion pipeline
├── page_furniture.py        # Page structure analysis
├── ocr_quality.py           # OCR quality assessment
├── table_reconstruct.py     # Table extraction from PDFs
├── parent_doc_store.py      # Parent document retrieval store
├── proposition_chunker.py   # Proposition-level chunking
├── advanced_crawler.py      # Deep web crawler
│
├── graph/                   # LangGraph multi-agent workflow
│   ├── workflow.py          # Main UniChatbot class
│   ├── Agents.py            # Specialist agents
│   ├── Supervisor.py        # Routing supervisor
│   ├── nodes.py             # Graph nodes
│   └── state.py             # Shared state schema
│
├── graph_rag/               # Graph RAG pipeline
│   ├── entity_extractor.py  # NER + relation extraction
│   ├── community_builder.py # Graph community detection
│   ├── graph_searcher.py    # Graph-based retrieval
│   └── graph_store.py       # Graph persistence
│
├── frontend/                # Browser chat UI
│   ├── index.html           # Chat interface
│   ├── style.css            # Styles
│   └── chat.js              # SSE streaming client
│
├── setup_knowledge_base.py  # One-time KB builder
├── setup_propositions.py    # One-time proposition builder
├── build_graph_rag.py       # One-time Graph RAG builder
├── eval_ragas.py            # RAGAS evaluation pipeline
│
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template (copy → .env)
└── data/                    # Runtime data (gitignored large files)
    └── parent_chunks.json   # Core KB parent documents
```

---

## 🧪 Evaluation

Run the RAGAS evaluation suite:

```bash
python eval_ragas.py
```

Results are saved to `UOLI_Eval_Results.xlsx`.

---

## ⚙️ Requirements

- Python 3.10+
- OpenAI API key
- Valkey or Redis (for caching & sessions)
- ~4 GB disk space (for vector DB + models)

---

## 📜 License

MIT License — University of Loralai AI Research Project
