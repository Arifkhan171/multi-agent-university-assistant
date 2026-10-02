<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:1a0533,25:3d0a52,50:6a0572,75:c62a88,100:ff6b35&height=220&section=header&text=UOLI%20AI%20Assistant&fontSize=60&fontColor=FFD700&animation=fadeIn&fontAlignY=44&desc=Multi-Agent%20University%20Chatbot%20%7C%20LangGraph%20%7C%20Graph%20RAG%20%7C%20LangSmith&descAlignY=66&descSize=18&descColor=FFB347&stroke=c62a88&strokeWidth=2" width="100%"/>

<br/>

[![YouTube Demo](https://img.shields.io/badge/▶️_Watch_Full_Demo-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://youtu.be/YpSCygFuMA8)
&nbsp;
[![LangGraph](https://img.shields.io/badge/LangGraph-FF6B6B?style=for-the-badge&logo=python&logoColor=white)](https://github.com/langchain-ai/langgraph)
&nbsp;
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
&nbsp;
[![ChromaDB](https://img.shields.io/badge/ChromaDB-FF6B35?style=for-the-badge&logo=python&logoColor=white)](https://www.trychroma.com)

<br/>

![Status](https://img.shields.io/badge/Status-Production_Ready-2ea44f?style=flat-square)
&nbsp;
![Docs](https://img.shields.io/badge/Docs_Indexed-7%2C021-c62a88?style=flat-square)
&nbsp;
![RAG](https://img.shields.io/badge/RAG-Hybrid+Graph+Proposition-6a0572?style=flat-square)
&nbsp;
![Cache](https://img.shields.io/badge/Cache-Semantic_Redis-ff6b35?style=flat-square)

</div>

<img src="https://capsule-render.vercel.app/api?type=soft&color=0:ff6b35,50:c62a88,100:6a0572&height=6" width="100%"/>

---

## 📌 Overview

> "Ask anything about the university — instantly, accurately, in real time."

*UOLI AI Assistant* is a production-grade intelligent chatbot built for the University of Loralai. It answers student and staff queries about admissions, fees, programs, faculty, and university policies using a multi-agent LangGraph pipeline backed by advanced hybrid retrieval — combining semantic search, BM25 keyword matching, Graph RAG, and proposition-level chunking for maximum accuracy.

The system streams responses in real time, caches repeated questions for instant replies, and maintains per-user session memory — all running on a FastAPI backend with a clean browser-based chat UI.

---

## 🎬 Demo

<div align="center">

[![Watch Demo](https://img.shields.io/badge/▶️_Watch_Full_System_Demo_on_YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white&labelColor=1a0533)](https://youtu.be/YpSCygFuMA8)

</div>

---

## 📸 System Screenshots

### 💬 Chat Interface — Welcome Screen

> Clean university-branded chat UI with session history, Redis connection status, document count, and real-time query input.

<img width="1584" height="718" alt="WhatsApp Image 2026-10-02 at 3 36 09 PM" src="https://github.com/user-attachments/assets/284f626c-15c3-4984-bdd8-d1237ec2ecdb" />


---

### 🤖 Live QA — Multi-Turn Conversation

> The assistant answers complex university queries across multiple turns — admissions requirements, fee structures, faculty information — all retrieved from 7,021 indexed documents.

<img width="1579" height="786" alt="WhatsApp Image 2026-10-02 at 3 37 00 PM" src="https://github.com/user-attachments/assets/e50f8c10-a0b3-465b-b642-2a700d5106d7" />


---

## ⚙️ Core Features

<table>
<tr>
<td width="50%" valign="top">

### 🤖 Agent Architecture
- ✅ LangGraph Supervisor + specialist agents
- ✅ Agents: Fees, Admissions, Programs, General
- ✅ Intelligent query routing by supervisor
- ✅ LangSmith full observability and tracing
- ✅ Stateful multi-turn conversation memory

</td>
<td width="50%" valign="top">

### 📚 Advanced Retrieval
- ✅ Hybrid RAG: Semantic + BM25 + Parent-Document
- ✅ Graph RAG: entity extraction + community summaries
- ✅ Proposition-based chunking for precision retrieval
- ✅ CRAG: corrective RAG for weak retrievals
- ✅ Cross-encoder reranking for top results

</td>
</tr>
<tr>
<td width="50%" valign="top">

### ⚡ Performance
- ✅ Semantic cache — instant replies for repeated queries
- ✅ Real-time token streaming via SSE
- ✅ 7,021 documents indexed and searchable
- ✅ Redis session memory per user
- ✅ RAGAS evaluation pipeline included

</td>
<td width="50%" valign="top">

### 🔧 Data Ingestion
- ✅ Auto-scheduled university website scraping
- ✅ Multi-strategy PDF ingestion with OCR fallback
- ✅ Table extraction from PDF documents
- ✅ Deep web crawler for full site coverage
- ✅ Automatic knowledge base rebuilding

</td>
</tr>
</table>

---

## 🏗️ System Architecture

mermaid
%%{init: {'theme': 'dark'}}%%
flowchart TD
    U[👤 User Browser\nChat UI] -->|SSE Stream| A

    A[⚡ FastAPI Server\napi.py] --> B & C

    B[🔄 Semantic Cache\nValkey Redis db=1\nInstant repeated query reply]

    C[🤖 UniChatbot\nLangGraph Workflow] --> D

    D[🧠 Supervisor Agent\nRoutes query to specialist] --> E1 & E2 & E3 & E4

    E1[💰 Fees Agent]
    E2[🎓 Admissions Agent]
    E3[📋 Programs Agent]
    E4[💬 General Agent]

    E1 & E2 & E3 & E4 --> F

    F[📚 Knowledge Base\nknowledge_base.py] --> G1 & G2 & G3

    G1[🔍 ChromaDB\nSemantic Dense Search]
    G2[🔑 BM25 Index\nKeyword Sparse Search]
    G3[📄 Parent Doc Store\nContext Expansion]

    F --> H[🕸️ Graph RAG\nEntity + Community Search]

    style U fill:#1a0533,color:#FFD700
    style A fill:#3d0a52,color:#fff
    style B fill:#ff6b35,color:#fff
    style C fill:#6a0572,color:#fff
    style D fill:#c62a88,color:#fff
    style F fill:#3d0a52,color:#fff
    style H fill:#6a0572,color:#fff


---

## 🧠 Advanced Techniques

| Technique | What It Does |
|---|---|
| *Graph RAG* | Extracts entities and builds knowledge graph — answers complex multi-hop queries |
| *CRAG* | Detects poor retrievals and self-corrects before generating answer |
| *Proposition Chunking* | Splits documents into atomic facts — much more precise than paragraph chunks |
| *Hybrid Fusion* | Combines dense semantic + sparse BM25 scores via Reciprocal Rank Fusion |
| *Parent-Document* | Retrieves small chunks but expands to full parent context for the LLM |
| *Semantic Cache* | Embeds queries and finds similar past queries — returns cached answers instantly |
| *LangSmith Tracing* | Tracks every agent step, retrieval call, and LLM response for full observability |
| *RAGAS Evaluation* | Measures faithfulness, answer relevancy, context precision across all agents |

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| 🤖 *Agent Orchestration* | LangGraph | Stateful multi-agent supervisor workflow |
| 🔗 *Agent Framework* | LangChain | LLM tools, chains, and retrievers |
| 📊 *Observability* | LangSmith | Full tracing, evaluation, and debugging |
| 🌐 *API Server* | FastAPI + SSE | Real-time streaming backend |
| 🔍 *Vector Store* | ChromaDB | Dense semantic document search |
| 🔑 *Keyword Search* | BM25 | Sparse keyword retrieval |
| 🕸️ *Graph Retrieval* | Graph RAG | Entity-based knowledge graph search |
| ⚡ *Cache + Sessions* | Valkey / Redis | Semantic cache and per-user memory |
| 📄 *PDF Ingestion* | PyMuPDF + OCR | Multi-strategy document parsing |
| 🌐 *Web Scraping* | APScheduler + Crawler | Auto-scheduled university site scraping |
| 🧪 *Evaluation* | RAGAS | Faithfulness and relevancy scoring |
| 🐍 *Language* | Python 3.10+ | Core system language |

---

## 🚀 Setup Instructions

<details>
<summary><b>Click to expand setup guide</b></summary>
<br/>

### Prerequisites
- Python 3.10+
- OpenAI API key
- Valkey or Redis installed and running
- ~4 GB disk space for vector DB and models

### Installation

bash
# 1. Clone the repository
git clone https://github.com/Arifkhan171/university-ai-chatbot.git
cd university-ai-chatbot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment
cp .env.example .env
# Open .env and add your OpenAI API key and Redis config


### Build the Knowledge Base

bash
# Ingest all university PDFs
python setup_knowledge_base.py

# Build proposition-level chunks (improves retrieval precision)
python setup_propositions.py

# Build Graph RAG index (improves complex multi-hop queries)
python build_graph_rag.py


### Launch the Server

bash
uvicorn api:app --reload --port 8000


Open your browser at *http://localhost:8000* 🎉

### Run Evaluation

bash
python eval_ragas.py
# Results saved to UOLI_Eval_Results.xlsx


</details>

---

## 📁 Project Structure

<details>
<summary><b>Click to expand full structure</b></summary>
<br/>


university-ai-chatbot/
│
├── api.py                    # FastAPI server — main entry point
├── config.py                 # All settings and environment variables
├── knowledge_base.py         # Hybrid RAG: ChromaDB + BM25 + Parent-Doc
├── semantic_cache.py         # Semantic query cache via Valkey
├── web_scraper.py            # University website auto-scraper
├── pdf_ingest.py             # PDF ingestion with OCR fallback
├── proposition_chunker.py    # Proposition-level document chunking
├── parent_doc_store.py       # Parent document retrieval store
│
├── graph/                    # LangGraph multi-agent workflow
│   ├── workflow.py           # Main UniChatbot class
│   ├── Agents.py             # Specialist agents
│   ├── Supervisor.py         # Query routing supervisor
│   ├── nodes.py              # Graph nodes
│   └── state.py              # Shared state schema
│
├── graph_rag/                # Graph RAG pipeline
│   ├── entity_extractor.py   # NER and relation extraction
│   ├── community_builder.py  # Graph community detection
│   ├── graph_searcher.py     # Graph-based retrieval
│   └── graph_store.py        # Graph persistence
│
├── frontend/                 # Browser chat UI
│   ├── index.html            # Chat interface
│   ├── style.css             # Styles
│   └── chat.js               # SSE streaming client
│
├── setup_knowledge_base.py   # One-time KB builder
├── build_graph_rag.py        # One-time Graph RAG builder
├── eval_ragas.py             # RAGAS evaluation pipeline
└── requirements.txt


</details>

---

## 🔒 Repository Access

This is a *private repository*.

[![Email](https://img.shields.io/badge/Request_Access-arif.cs.bs%40gmail.com-EA4335?style=for-the-badge&logo=gmail&logoColor=white)](mailto:arif.cs.bs@gmail.com)
&nbsp;
[![LinkedIn](https://img.shields.io/badge/Connect-Arif_Khan-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/arif-khan-71a711376)

---

<div align="center">

<img src="https://capsule-render.vercel.app/api?type=soft&color=0:1a0533,50:6a0572,100:c62a88&height=8" width="100%"/>

<br/>

*Built by Arif Khan — AI Engineer | University of Loralai, Pakistan*

If this project helped you — a ⭐ on the repo means the world!

</div>

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:ff6b35,40:c62a88,70:6a0572,100:1a0533&height=130&section=footer" width="100%"/>
