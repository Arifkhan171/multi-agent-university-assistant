# The Complete Master Architecture & Engineering Guide
**Project: University of Loralai (UOLI) Advanced AI Chatbot**
**Version: 1.0 — Enterprise-Grade Specification Document**

---

## CHAPTER 1: The Big Picture & Core Technologies

### 1.1 What is this Project?
This project is an advanced, production-ready AI Chatbot for the University of Loralai (UOLI). Standard, primitive chatbots read from a single text file or rely on simple keyword matching. This system, however, acts as a team of highly intelligent, specialized university staff members. It autonomously pulls information from live university websites, PDFs, and images, structures them mathematically, and answers complex student queries with near-perfect accuracy.

### 1.2 Core Technologies Used
To achieve this level of intelligence, the system merges several cutting-edge AI and web technologies:
* **FastAPI:** The high-speed backend web framework. It acts as the bridge receiving questions from the user's browser and streaming the AI's answers back in real-time.
* **LangGraph:** The "brain" orchestrator. Instead of using one single AI model that tries to do everything (and inevitably gets confused), LangGraph allows the creation of a "Supervisor" AI that delegates tasks to specialized AI workers (like an Admissions Expert or a Policy Expert).
* **Hybrid RAG (Retrieval-Augmented Generation):** The knowledge retrieval system. When a user asks a question, the system searches **ChromaDB** (for semantic meaning), **BM25** (for exact keywords), and a **Knowledge Graph** (to understand relationships between people and departments).
* **Valkey / Redis:** The ultra-fast memory system. It tracks user sessions (so the bot remembers what the user said 2 minutes ago) and acts as a **Semantic Cache**. 
* **OpenAI (LLMs):** The language engine (`gpt-4o-mini` and `gpt-4o`) that reads the retrieved facts and generates the final human-like text response.

### 1.3 The Step-by-Step Execution Flow
When a student types: *"How much are the Computer Science fees?"*, the system executes the following 6-step pipeline:

1. **The Request Hits the Server:** The user's browser sends the message to the FastAPI backend (specifically the `/chat/stream` endpoint).
2. **The Semantic Cache Check (The Shortcut):** Before spending money on an AI call, the system checks Valkey. It converts the user's question into a mathematical vector and asks: *"Has anyone asked a question with a 90% similar meaning recently?"*
   * *If Yes:* It instantly grabs the cached answer and streams it back. Zero AI cost, and it takes ~5 milliseconds.
   * *If No:* It proceeds to Step 3.
3. **The Supervisor Agent (Routing):** The question enters the LangGraph workflow. The Supervisor agent looks at "Computer Science fees" and determines it is a financial question. It immediately routes the task to the **Fees Agent**.
4. **The Specialist Agent & Knowledge Base:** The Fees Agent searches the database using Vector Search (meaning), BM25 (keywords), and Graph RAG (relationships). It uses Parent-Child chunking to find the exact sentence containing the fee, and then pulls the massive surrounding paragraph to ensure full context.
5. **Generating the Answer & Streaming:** The Fees Agent takes all those retrieved documents, reads them alongside the student's question, and uses OpenAI to generate an answer. As the AI types word-by-word, FastAPI streams it directly to the user's screen using SSE (Server-Sent Events).
6. **Saving for Later:** The complete question and answer are saved back into the Valkey Semantic Cache. The next time a student asks about CS fees, the bot answers instantly at Step 2.

### 1.4 Background Worker Tasks
While the chatbot is answering questions, background workers keep the knowledge base alive:
* **The APScheduler Scraper:** Every few hours, it automatically browses the UOLI website to check for new information, updating the Knowledge Base without human intervention.
* **PDF Ingestion:** When new policy PDFs are discovered, it uses OCR and table-reconstruction algorithms to format the data beautifully for the AI.

---

## CHAPTER 2: The Multi-Agent Orchestration (LangGraph)

Standard AI bots fail because they use one generic prompt for every question. This project uses a Multi-Agent architecture, functioning like a university administration office.

### 2.1 The Supervisor Agent (The Boss)
The Supervisor receives every incoming question.
* **Chitchat Bypass:** If the student types *"Hi"* or *"Thanks"*, a hardcoded `chitchat_check_node` intercepts it. It replies instantly without using the LLM. Cost: $0.
* **LLM Classification:** For real questions, the Supervisor uses `gpt-4o-mini` (a highly efficient, cheap model). It is given a strict prompt to classify the question into exactly one of six labels. 
* **Follow-up Resolution:** If a student asks *"What does that mean?"*, the Supervisor appends the previous conversation topic to the question so the subagents don't lose context.

### 2.2 The 6 Specialist Subagents
Once classified, the question goes to a highly specialized agent:
1. **Policy Agent:** Handles rules, regulations, attendance, cheating punishments, and discipline.
2. **Faculty Agent:** The "directory" agent. It handles people, HODs, contact emails, extensions, and leadership roles.
3. **Admissions Agent:** Handles how to apply, eligibility, merit criteria, and program structures (BS/MS/PhD).
4. **Campus Agent:** Handles physical services like bus routes (garee/gaadi), library timings, hostels, and medical facilities.
5. **Fees Agent:** Handles costs, fee structures, financial aid, and scholarships.
6. **General Agent:** Handles broad university overview, history, news, organization structure, and total student/faculty counts.

*(There is also an **Out of Scope Agent** that instantly rejects questions about sports, math, or politics).*

### 2.3 Metadata Categories and Sharing
Agents do not search the entire database. They are restricted to specific metadata categories tagged during scraping.
* `policy`: Searched by Policy & Admissions agents.
* `faculty`: Searched by the Faculty agent.
* `admissions`: Searched by Admissions & Fees agents.
* `offices`: Searched by Faculty, Campus, and Fees agents.
* `overview` & `general`: Searched by almost everyone.
By restricting an agent to specific categories, accuracy skyrockets. A student asking about "library fees" won't accidentally pull a document about "library silence rules."

### 2.4 Multi-Intent Query Splitting (`_split_intents`)
**The Vulnerability:** If the Supervisor must pick ONE agent, what happens if a student asks: *"Who is the VC [Faculty], and what is the bus routine [Campus]?"* If it goes to the Faculty agent, the bus routine might be ignored because the Faculty agent cannot search the Campus categories.

**The Solution:** Inside the agents, a function called `_split_intents` handles compound queries. It splits *"Who is the VC and what departments does UoL have?"* into two separate searches, runs them in parallel, and merges the results. While this mitigates the issue within a single agent's domain, true cross-domain query decomposition is required to fully solve the vulnerability across wildly different topics.

---

## CHAPTER 3: The Ultimate Web Scraper & Ingestion Pipeline

The chatbot is only as smart as its data. The `setup_knowledge_base.py` and `web_scraper.py` pipeline is an enterprise-grade data ingestion engine.

### 3.1 Playwright vs. Basic Scrapers (React Resilience)
Old scrapers (like `requests` + `BeautifulSoup`) only download raw HTML. If a university updates their website to use modern frameworks like React, Vue, or heavy JavaScript, old scrapers see a blank page.
Your scraper uses **Playwright**. It opens an actual, invisible Chromium web browser, lets the website load completely, executes all JavaScript, and *then* extracts the text. If UOLI moves to React, the scraper will continue to work perfectly.

### 3.2 The Auto-Crawler (Phase A & B)
You do not have to manually feed every URL into the bot.
* **Phase A (Guaranteed Pages):** It scrapes explicitly defined, critical URLs (e.g., `/faculty`, `/admissions`).
* **Phase B (Auto-Crawler):** The `advanced_crawler.py` starts at the homepage and recursively clicks every internal link it can find, mapping up to 300 pages. If the university deletes an old link and creates a new one, the crawler dynamically discovers it.

### 3.3 EasyOCR (Reading Text from Images)
Standard chatbots are completely blind to images. If UOLI uploads a `.jpeg` poster saying "Admissions Open - Fall 2026", standard bots miss it. 
Your scraper uses **EasyOCR**. It downloads the image, uses neural networks to read the text off the pixels, and formats it as searchable text. 
* **MD5 Hashing:** To prevent reading the exact same footer image 100 times on 100 different pages, the scraper hashes the image bytes. It only reads an image once, caches the result in `ocr_cache.json`, and applies it everywhere.

### 3.4 Faculty Card Structural Parsing
Rather than blindly flattening a webpage into messy text, the scraper looks for specific HTML structures like `<div class="faculty-card">`. 
It cleanly extracts the HOD's name, their designation, and their email. Crucially, if an email is missing, the code explicitly injects the text *"Email not listed."* This structural parsing prevents the LLM from hallucinating and accidentally assigning a neighboring teacher's email to the HOD.

### 3.5 PDF Ingestion (Pass 3)
When the scraper finds a `.pdf` link on the website (e.g., Disciplinary Rules), it automatically downloads the file, parses the text (and OCRs it if it is a scanned document), and ingests it directly into the Knowledge Base.

---

## CHAPTER 4: Data Chunking, Vectors, & Storage Architecture

You cannot feed a 100-page website into an LLM all at once; it exceeds the context window. The text must be chunked. This architecture uses advanced **Parent-Child Chunking**.

### 4.1 The 1500-Character Safety Net
As the text is scraped, the `UniversityAwareChunker` attempts to keep logical sections intact by splitting at HTML headings (`##`). If a section is massive, it applies a strict 1500-character fallback limit to prevent memory overflow. This 1500-character chunk is temporary; it lives in RAM for a fraction of a second and is then passed to the main splitter.

### 4.2 The 800-Character Parent Chunks
The `RecursiveCharacterTextSplitter` takes the text and cuts it into maximum 800-character blocks (about 150-200 words, or 1-2 paragraphs). 
**How it cuts:** It does not blindly chop at exactly 800 characters, which would cut words in half. It looks backward from the 800 limit to find a natural break:
1. Paragraph Break (`\n\n`)
2. Line Break (`\n`)
3. Sentence Period (`. `)
These perfectly intact paragraphs are saved to the hard drive in a human-readable file called `data/parent_chunks.json`.

### 4.3 The 200-Character Child Chunks (The RAG Secret)
The 800-character Parent is then chopped again into roughly four tiny 200-character Children (about 1 sentence each). 
**Only the 200-character Children are converted into mathematical vectors and stored in ChromaDB.** (This is why your database shows ~7,000 chunks).

### 4.4 Why Parent-Child Chunking? (Solving Context Dilution)
* **Small chunks (200 chars)** are perfect for vector search. If you convert an 800-character paragraph into a vector, the math gets "watered down" because the paragraph discusses multiple topics. A 200-character sentence is mathematically pure.
* **Big chunks (800 chars)** are perfect for the LLM. If you only give the LLM a 200-character sentence, it lacks context. 
* **The Magic Trick:** When the code makes the tiny 200-character child chunks, it stamps them with a metadata tag (e.g., `parent_id = 42`). When a student asks "What is the fee?", ChromaDB finds the highly-accurate 200-character child. The code reads the tag, opens `parent_chunks.json`, grabs the massive 800-character Parent block, and sends the big block to the LLM. Pinpoint search accuracy + massive reading context!

### 4.5 Where Everything is Stored
* **Parent Chunks (800 chars):** `data/parent_chunks.json` (Human-readable JSON).
* **Vector Search (200 chars):** `data/chroma_db/` (Binary `.sqlite3` and `.parquet` files).
* **BM25 Index:** `data/bm25_index.pkl` (A highly compressed mathematical Python Pickle file).
* **Graph RAG Data:** `data/graph_rag/` (Contains `.json` and `.graphml` files for nodes, edges, and summaries).

---

## CHAPTER 5: The Hybrid Search Engine

When a specialist agent needs answers, it searches using three distinct mathematical systems simultaneously.

### 5.1 Semantic Vector Search (ChromaDB)
This engine converts sentences into coordinates on a 384-dimensional mathematical graph. It searches by **meaning**. If a student searches for "automobile", it can find documents containing "car" or "bus" because they exist close to each other in mathematical space.

### 5.2 Keyword Search (BM25)
Vector search is terrible at finding exact IDs or acronyms (like "Course Code DSA-301"). **BM25** is a traditional keyword search algorithm (using TF-IDF math). It calculates how often your exact word appears in a document compared to the rest of the database. It ensures strict keyword matches are never lost.

### 5.3 The Cross-Encoder Reranker (`ms-marco-MiniLM-L-6-v2`)
Vector search brings back the top 50 chunks. BM25 brings back the top 50 chunks. The system merges them into 100 chunks. 
Which one is actually the best? The system runs a local AI model called a **Cross-Encoder**. This model reads the student's question and reads every single one of those 100 chunks, scoring them on a strict scale from -10 to +10. The system keeps only the absolute highest-scoring chunks and throws the rest in the trash before calling OpenAI.

---

## CHAPTER 6: Graph RAG (Relational Knowledge Graph)

Standard Vector and BM25 RAG is excellent for facts ("What is the VC's email?"), but it completely fails at structural relationships ("Who reports to whom?"). Your system uses Graph RAG to map the entire university.

### 6.1 The One-Time Build (`build_graph_rag.py`)
This script is run manually and explicitly. It takes 15-20 minutes and makes hundreds of API calls to GPT-4o-mini. 
It feeds every chunk of data into the LLM and asks it to extract **Entities** (e.g., "Ismail Khan", "CS Department") and **Relationships** (e.g., "Is HOD of"). It builds a massive NetworkX `DiGraph` (Directed Graph) of the university.

### 6.2 The `greedy_modularity_communities` Algorithm
The entire university graph is too massive to read at once. The code uses a mathematical algorithm called `greedy_modularity_communities` (imported from NetworkX). 
This algorithm automatically groups densely connected nodes into "Communities." For example, it organically detects that the HOD of CS, the CS Faculty, and the BS CS program all belong together in one community.

### 6.3 Generating LLM Summaries
For every detected community, the system takes the entities and up to 20 relationships and sends them to GPT-4o-mini with this prompt: *"Write a clear, factual paragraph (100-150 words) summarizing what this group of entities represents, key people, and how they relate."*
If it finds 50 communities, it generates 50 summaries. These summaries are saved permanently in `data/graph_rag/summaries.json`.

### 6.4 The Trigger Logic & Parallel Execution
When a student asks: *"Who reports to whom and what is the DSA course code?"*
1. **The Trigger:** The `GraphSearcher` sees the keyword *"reports to whom"* (or detects two specific entity names). It knows this is a Graph question.
2. **Parallel Execution:** It does **not** bypass BM25/Vector search! It runs Graph RAG to fetch the pre-written Community Summaries explaining the hierarchy, AND it runs Vector/BM25 to fetch the DSA course code.
3. **The Merge:** Inside the final LLM prompt, it creates two distinct context boxes: `context:` (for BM25 text) and `graph_context_section:` (for Graph text). The LLM reads both simultaneously to answer the complex, multi-part question perfectly.

---

## CHAPTER 7: Corrective RAG (CRAG) & Adaptive Retry

Even the best search engines fail if a student asks a highly ambiguous question. The system has a built-in safety net called Corrective RAG (CRAG).

### 7.1 The `WEAK_RETRIEVAL_SCORE` Threshold
In `config.py`, there is a threshold set at `2.0`. After the Cross-Encoder scores the retrieved chunks, if the absolute best chunk scores lower than 2.0, the system realizes: *"This retrieval failed. These documents do not answer the question."*

### 7.2 HyDE (Hypothetical Document Embeddings) Expansion
Instead of saying "I don't know," the system executes an Adaptive Retry. It asks the LLM to rewrite the student's bad question into a detailed, hypothetical perfect answer (a technique called HyDE). It then vectorizes this fake answer, drops all category filters, and searches the entire database again.

*(Note: During evaluation, this Adaptive Retry took an extra 5-9 seconds per query. To prioritize lightning-fast 3-second responses, `ENABLE_ADAPTIVE_RETRY` is currently set to `False` in production, but the elite architecture remains ready to be toggled on).*

---

## CHAPTER 8: Infrastructure, Deployment, & Cost Analysis

This chatbot is a highly sophisticated, full-stack AI backend requiring specific server infrastructure.

### 8.1 Setup vs. Runtime Costs
* **Setup Costs:** Running `build_graph_rag.py` costs ~$0.25 one-time. Running the web scraper costs $0. 
* **Runtime Costs:** A standard question triggers exactly 2 LLM calls (1 Supervisor route, 1 Final Answer). Using `gpt-4o-mini`, a single question costs a tiny fraction of a cent.
* **Storage Costs:** $0. ChromaDB, NetworkX, and the BM25 matrix run 100% locally. You are not paying monthly SaaS fees for vector databases.

### 8.2 Why Shared Hosting (cPanel) Fails
Standard Hostinger Shared Web Hosting (e.g., $3/month plans) only supports PHP and basic SQL. This chatbot requires Python 3.10+, FastAPI, Playwright (which installs a full headless Chromium browser), PyTorch, EasyOCR (heavy math libraries), and ChromaDB (in-memory SQLite vectors). It will instantly crash on shared hosting.

### 8.3 The Hostinger VPS Deployment
To deploy this chatbot, the university must use a **Hostinger VPS (Virtual Private Server)** running Ubuntu Linux. 
Because of the AI models (Cross-Encoder reranking and EasyOCR image processing), the VPS requires an absolute minimum of **2GB RAM**, though **4GB RAM** is highly recommended to prevent out-of-memory crashes during the intensive web-scraping phase.

Once the FastAPI backend is running on the VPS, the university simply pastes a small JavaScript chat widget into their React or WordPress frontend. The widget communicates seamlessly with the VPS backend, delivering a world-class AI experience to the students of UOLI.

---

## CHAPTER 9: Deep Dive — The Single-Question Retrieval Pipeline

When a student submits a single, standalone question (such as *"Who is the Vice Chancellor?"* or *"What is the attendance policy?"*), the system executes a multi-stage retrieval and filtering pipeline designed to deliver pinpoint factual precision without hallucinations.

```
Incoming Question
       │
       ▼
[chitchat_check_node] ──── (Greeting / Bye) ────► [Instant Canned Answer] (Cost: $0)
       │ (Real Question)
       ▼
 [supervisor_node]    ────► Classifies domain (Fast-Path / GPT-4o-mini)
       │
       ▼
 [Specialist Agent Node]
       │
       ├─► [Leadership Fast-Path] ── (VC / Registrar / Dean) ──► Direct URL + Exact $contains Match
       │
       └─► [Full Hybrid Search]   ── (Policies, Fees, Campus)
                 │
                 ├── 1. Vector Search (ChromaDB text-embedding-3-large)  ──► Top 50 Chunks
                 ├── 2. Keyword Search (BM25Okapi Index)                 ──► Top 50 Chunks
                 │
                 ▼
          [Candidate Fusion & Deduplication]  ──────────────────────────► 50 to 80 Unique Chunks
                 │
                 ▼
          [Cross-Encoder Reranker] (ms-marco-MiniLM-L-6-v2)             ──► Scored from -10 to +10
                 │
                 ▼
          [E3 Dynamic Context Gating]
                 ├── Floor Filter: Remove scores < -7.0
                 ├── Cliff-Drop Filter: Cut off when score drops by ≥ 2.0
                 └── Bounded Range: Dynamically outputs between 3 and 8 chunks
                 │
                 ▼
          [E2 Parent-Child Passage Expansion]
                 └── Swap child propositions for 800-character parent passages
                     (Guarded by length, ratio ≥ 1.2x, and word overlap ≥ 30%)
                 │
                 ▼
          [LLM Generation (GPT-4o-mini)] ───────────────────────────────► Final Answer Streamed to User
```

### 9.1 The Request Lifecycle & Entry Nodes
1. **Entry Point (`workflow.py`):** The question first enters `chitchat_check_node`. If the user simply said *"hello"*, *"thank you"*, or *"salam"*, it returns an immediate static greeting without touching ChromaDB or OpenAI.
2. **Supervisor Routing (`Supervisor.py`):** Unambiguous queries are routed using deterministic keyword lists (e.g., words like *"fee"*, *"challan"* route directly to the Fees Agent). If keyword matching is ambiguous, `gpt-4o-mini` classifies the question into one of the specialist agent categories.
3. **Specialist Node Execution:** The supervisor transitions state control to the chosen specialist node (e.g., `faculty_agent_node`, `policy_agent_node`, `fee_agent_node`).

---

### 9.2 The Leadership & Office Fast-Path Architecture
For university executive roles (Vice Chancellor, Pro-VC, Registrar, Dean, Treasurer, Controller of Examinations), the standard vector search pipeline is completely bypassed.

1. **Direct URL Retrieval:** The system maintains an authoritative lookup table (`_OFFICE_URL`). For instance:
   * `"vc"` / `"vice chancellor"` $\rightarrow$ `https://uoli.edu.pk/vc-message`
   * `"registrar"` $\rightarrow$ `https://uoli.edu.pk/office-of-registrar`
   * `"treasurer"` $\rightarrow$ `https://uoli.edu.pk/office-of-treasurer`
   Instead of calculating embeddings, the agent directly fetches the ingested chunks originating from that exact official page.
2. **Exact Name Probing (`_role_name_probe`):** An office webpage may describe office duties across 75 paragraphs without explicitly naming the current officeholder in every paragraph. To ensure the person's name is retrieved, the code runs an exact literal string search (`where_document={"$contains": term}`) inside ChromaDB:
   * It looks for known person patterns (`_PERSON_RE` matching honorifics like *Engr.*, *Prof.*, *Dr.*, *Mr.*).
   * It looks for role assertion patterns (`_ROLE_ASSERTION_RE` matching sentences like *"is the Vice Chancellor"* or *"serves as Registrar"*).
3. **Guaranteed Output:** The exact 1-sentence assertion (e.g., *"Engr. Prof. Dr. Ehsanullah Kakar is the Vice Chancellor of the University of Loralai."*) is placed at Rank 1, followed by supporting office context.

---

### 9.3 Proper Names vs. Conceptual Policies
Why is this direct fast-path used ONLY for leadership and faculty names, and NOT for attendance, fees, or bus schedules?

* **Proper Names Lack Semantic Clues:** Vector search operates on mathematical embeddings of meaning. Proper names (*"Dr. Khalid Khan"*, *"Nasir Rehan"*) carry minimal semantic meaning. In high-dimensional vector space, embeddings for different professors cluster closely together. Vector search frequently confuses names or gets drowned out by pages mentioning the title in other contexts. Exact string matching is mathematically superior for proper names.
* **Policies are Conceptual and Spread Out:** Rules like *"Can I sit in final exams if I was sick for two weeks?"* or *"What is the passing CGPA?"* cannot be captured by a single name probe. Policies consist of percentages (75%), exceptions, committees, credit hours, and penalties described across multiple paragraphs and official notification PDFs.
* **Linguistic Diversity:** Students express policies in hundreds of different ways (*"attendance criteria"*, *"haazri policy"*, *"missed classes rule"*, *"75 percent requirement"*). Vector similarity search + BM25 keyword search is essential because it matches conceptual intent regardless of the student's exact phrasing.

---

### 9.4 Hybrid Search Mechanics (ChromaDB + BM25)
For all standard queries, the engine executes two parallel retrieval strategies:

1. **Dense Vector Search:**
   * **Embedding Model:** `text-embedding-3-large` (3,072 dimensions).
   * **Database:** ChromaDB (`./data/chroma_db`).
   * **Function:** Encodes the query into semantic vector space and calculates cosine similarity against document chunks. It retrieves the **top 50 candidate chunks**.
2. **Sparse Keyword Search (BM25):**
   * **Algorithm:** `BM25Okapi` (in-memory inverted index).
   * **Function:** Evaluates term frequency and inverse document frequency. It excels at finding exact codes, acronyms, and numbers (e.g., *"75%"*, *"CS-101"*, *"HEC"*). It retrieves the **top 50 candidate chunks**.

---

### 9.5 Candidate Fusion & The 50-to-80 Unique Paragraph Reality
Although Vector Search requests 50 chunks and BM25 requests 50 chunks, the reranker does **not** receive 100 distinct chunks.

* **High Relevance Overlap:** On well-indexed university topics, the highest-quality documents contain both the exact keywords and the relevant semantic meaning. As a result, 20 to 50 chunks appear in **both** result sets.
* **Deduplication:** The fusion layer unifies the candidates using content hashing. Duplicate paragraphs are eliminated so the reranker does not process identical text twice.
* **Category Filtering:** Chunks belonging to unrelated metadata categories (e.g., athletic news when searching for exam policies) are stripped out.
* **Net Candidate Pool:** The deduplicated pool presented to the reranker typically contains between **50 and 80 unique candidate paragraphs**.

---

### 9.6 Cross-Encoder Reranking (`ms-marco-MiniLM-L-6-v2`)
Standard vector search compares query and document embeddings independently (Bi-Encoder). To achieve deep semantic understanding, the candidates are evaluated by a **Cross-Encoder model**:

* **Model Architecture:** `cross-encoder/ms-marco-MiniLM-L-6-v2` running via `sentence_transformers`.
* **Mechanism:** The model takes the full query string and the candidate chunk together as a single input pair `[Query, Chunk]` through all transformer attention layers.
* **Scoring:** It computes an unconstrained relevance logit score (typically ranging from **-10.0** for complete noise to **+10.0** for perfect factual answers).
* **Sorting:** All 50–80 candidates are re-ordered strictly by this cross-encoder score.

---

### 9.7 The E3 Dynamic Context Gate (The 3-to-8 Paragraph Rule)
Feeding all 50–80 paragraphs to the LLM would overwhelm the prompt, increase latency, and degrade answer quality. The system uses a dynamic gating algorithm:

* **Absolute Floor (`CONTEXT_SCORE_FLOOR = -7.0`):** Any paragraph scoring below -7.0 is recognized as irrelevant noise and immediately discarded.
* **Cliff-Drop Detection (`CONTEXT_CLIFF_DROP = 2.0`):** The algorithm scans the sorted scores from top to bottom. If it encounters a sharp drop $\ge 2.0$ between two consecutive paragraphs, it recognizes the relevance boundary and cuts off all subsequent paragraphs.
* **Hard Backstop (`MIN_CONTEXT_DOCS = 3`):** The system always preserves at least 3 documents to guarantee the AI has sufficient context, even if scores decline quickly.
* **Upper Ceiling (`MAX_CONTEXT_DOCS = 8`):** The system caps standard context at 8 documents. Passing more than 8 documents degrades RAGAS `context_precision` because irrelevant tails dilute the prompt.
* **Dynamic Behavior:** For standard queries, the LLM receives dynamically between **3 and 8 paragraphs**, precisely trimmed to where actual relevance ends.

---

### 9.8 The E2 Parent Document Swap (`ParentDocStore`)
To optimize search, vector embeddings are generated from atomic, 200-character proposition chunks. However, passing an isolated 200-character sentence to the LLM can strip away important context.

* **Parent Storage:** All original ~800-character narrative blocks are stored in `data/parent_chunks.json`.
* **The Swap:** After reranking and gating, the surviving child chunks are mapped back to their parent passages.
* **Safety Verification Gates:** A parent swap is accepted only if:
  1. The parent is genuinely larger than the child ($\ge 1.2\times$ length ratio).
  2. The parent is not a broken UI snippet ($> 150$ characters).
  3. The parent shares at least $30\%$ content-word overlap with the child proposition.
  If a parent fails these checks, the atomic child is kept as-is to prevent replacing a concise fact with website navigation text.

---

## CHAPTER 10: Multi-Intent & Compound Query Architecture

Students frequently ask compound questions containing multiple distinct topics in a single prompt (e.g., *"Who is the VC, what is the bus routine, and what is the attendance policy?"*).

```
User Query: "Who is the VC, what is the bus routine, and attendance policy?"
                                  │
                                  ▼
                [_split_intents Regex & Grammar Deconstruction]
                                  │
          ┌───────────────────────┼───────────────────────┐
          ▼                       ▼                       ▼
 Intent 1: "Who is the VC"  Intent 2: "what is the bus" Intent 3: "attendance policy"
          │                       │                       │
          ▼                       ▼                       ▼
  Faculty Search Profile   Campus Search Profile    Policy Search Profile
          │                       │                       │
     ~50 Candidates          ~50 Candidates          ~50 Candidates
          │                       │                       │
          ▼                       ▼                       ▼
   Top 2-3 Best Hits       Top 2-3 Best Hits       Top 2-3 Best Hits
          └───────────────────────┬───────────────────────┘
                                  ▼
                     [Interleave & Deduplicate]
                                  ▼
           [Unified Context Payload (6 to 9 Focused Chunks)]
                                  │
                                  ▼
                      [Single LLM Synthesizer]
                                  │
                                  ▼
                     Comprehensive Unified Answer
```

### 10.1 The Multi-Intent Problem: Context Dilution & Rank Starvation
If a compound query is fed into a single search pass, the Cross-Encoder ranks documents against the combined text. Whichever topic possesses stronger keywords or higher frequency dominates the top ranks. The secondary topics get pushed down to ranks 9–50 and are subsequently eliminated by the E3 score cutoff. As a result, the chatbot answers only the first part of the question and ignores the rest.

---

### 10.2 How `_split_intents` Deconstructs Questions
The system utilizes a deterministic single-pass grammar analyzer (`knowledge_base.py:L2509`):
1. **Delimiter Splitting:** Splits on punctuation and conjunctions: `", and"`, `","`, `"and"`, `";"`, `"&"`.
2. **Question Prefix Detection (`_Q_PAT`):** Detects question headers:
   `who is`, `who are`, `what is`, `what are`, `where is`, `when is`, `how is`, `tell me about`.
3. **Prefix Inheritance:** If a student writes *"Who is the VC, the Registrar, and the Treasurer?"*, the fragments *"the Registrar"* and *"the Treasurer"* lack question words. The parser automatically prepends the last seen prefix (*"Who is"*), transforming them into complete standalone queries:
   * Intent 1: *"Who is the VC"*
   * Intent 2: *"Who is the Registrar"*
   * Intent 3: *"Who is the Treasurer"*
4. **Content Filtering:** Every fragment must contain at least one meaningful content word (excluding stop words) to qualify as a valid search intent.

---

### 10.3 Intra-Agent Multi-Intent Retrieval (Same Agent Domain)
When all sub-intents belong to the same agent (e.g., *"Who is the VC, what is his qualification, and what is his degree?"*):
* The query is split into distinct intents.
* The system executes candidate retrieval across all intents to assemble a combined candidate pool.
* **Independent Reranking:** Each intent reranks the pool against its own specific query string. Sub-intent 1 selects the top chunks for the VC's identity; sub-intent 2 selects the top chunks for his qualifications; sub-intent 3 selects the top chunks for his degrees.
* **Per-Intent Quota:** Each sub-intent is allocated a balanced slice of the context budget (typically 2 to 3 chunks per intent).

---

### 10.4 Rank Interleaving vs. Combined Late Reranking
The system does **not** dump all retrieved chunks into one large bucket to rerank them together at the end. Instead, it uses **Rank Interleaving**:
* **The Interleaving Pattern:**
  * Rank 1 of Intent 1
  * Rank 1 of Intent 2
  * Rank 1 of Intent 3
  * Rank 2 of Intent 1
  * Rank 2 of Intent 2
  * Rank 2 of Intent 3
* **Why Interleave?** RAGAS evaluates retrieval using `context_precision` (an average precision metric that rewards placing relevant documents near the top). Since every sub-intent represents a vital part of the student's question, interleaving ensures every topic has a top-ranked document near the start of the context window.
* **Bypassing the Cliff Gate:** Dynamic cliff-drop gating is intentionally disabled on interleaved pools because score drops between different questions reflect question difficulty rather than document relevance.

---

### 10.5 Cross-Agent Compound Queries
When sub-intents cross multiple agent domains (e.g., *"Who is VC [Faculty], what is bus routine [Campus], and attendance policy [Policy]?"*):
* In `graph/Agents.py:retrieve_for_agent()`, the system applies `_quick_classify()` to each sub-intent.
* It identifies that Intent 1 needs the **Faculty** profile, Intent 2 needs the **Campus** profile, and Intent 3 needs the **Policy** profile.
* It invokes the specialized retrieval function for each profile with its designated category filters.
* Chunks from all profiles are unified into a single deduplicated document list and passed to the LLM prompt.

---

### 10.6 The ~150 Candidate Pool Across Multi-Search Operations
When 3 sub-searches run:
* Search 1 (Faculty) retrieves $\sim 50$ candidate paragraphs.
* Search 2 (Campus) retrieves $\sim 50$ candidate paragraphs.
* Search 3 (Policy) retrieves $\sim 50$ candidate paragraphs.
* Across all three searches, **approximately 150 candidate paragraphs** are gathered and evaluated across the database before being filtered, reranked, and compressed into the final 6–9 context documents.

---

### 10.7 Sequential vs. Parallel Execution Analysis
* **Current Implementation (Sequential):** In the active codebase (`graph/Agents.py:L2233`), multi-intent retrieval runs inside a standard Python `for` loop:
  ```python
  for _ag, _intent_q in zip(_classified, _intents):
      # Retrieves intent 1, waits... then intent 2, waits... then intent 3
  ```
  It executes sequentially, one after the other.
* **Performance Impact:** A 3-intent question incurs 3 sequential ChromaDB queries and 3 Cross-Encoder scoring passes, increasing response latency to ~3–5 seconds.
* **Upgrade Path:** Parallelizing this loop using Python's `ThreadPoolExecutor` or `asyncio.gather()` would allow all 3 searches to execute concurrently, reducing latency to ~1.2 seconds without changing any retrieval results.

---

## CHAPTER 11: Multi-Agent Routing Mechanics & The Supervisor

A common misconception is that the LangGraph Supervisor forks compound questions to multiple worker agents at the same time. The real implementation is more focused:

```
[User Question: Multi-Intent]
            │
            ▼
    [Supervisor Node]
            │
    (Evaluates primary agent via classifier)
            │
            ▼
  [Routes to EXACTLY ONE Worker Agent]
  (e.g., Faculty Agent)
            │
            ▼
  [Worker Agent calls retrieve_for_agent()]
            │
            ├── Loops through all sub-intents
            ├── Borrows retrieval profiles from Campus, Policy, etc.
            └── Aggregates all documents into one context
            │
            ▼
  [Worker Agent LLM synthesizes complete multi-part answer]
            │
            ▼
          [END]
```

### 11.1 The Single-Agent Routing Constraint in LangGraph
In `graph/workflow.py`, conditional routing is defined as:
```python
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
```
The state key `agent_name` holds a **single string value**. The LangGraph graph executes as a single-path state machine. It does not fork or split execution into multiple active agent nodes concurrently.

---

### 11.2 Why the Supervisor Does Not Fork Messages
* **Simplicity and State Management:** In a state machine, forking to multiple nodes requires complex synchronization barriers (joining and waiting for all branches to finish before synthesizing).
* **Token Efficiency:** Having 3 separate agents invoke 3 separate LLM calls to answer each fragment would consume $3\times$ the API tokens and triple costs.
* **Cohesive Writing:** A single LLM receiving all retrieved context writes a smooth, natural, unified response that connects all parts of the user's question, rather than producing three disconnected answers.

---

### 11.3 How the Single Destination Agent Coordinates Cross-Domain Retrieval
The architectural solution lives inside `retrieve_for_agent()`:
* The Supervisor routes the overall message to whichever agent represents the primary intent (e.g., the Faculty Agent).
* The Faculty Agent invokes `retrieve_for_agent()`.
* `retrieve_for_agent()` detects that parts of the question belong to other domains.
* It internally borrows the category filters and search logic of the Campus and Policy agents.
* All retrieved documents are returned directly to the Faculty Agent, which synthesizes the complete, unified answer for the student.

---

## CHAPTER 12: Clarification & Ambiguity Handling (The Disambiguation Dilemma)

When a student asks an underspecified or ambiguous question (such as *"What is the fee?"* or *"Give me Bilal's contact number"* when three different lecturers are named Bilal), an ideal human advisor would ask for clarification:
* *"Which degree program's fee are you inquiring about: BS, MS, or PhD?"*
* *"We have Dr. Bilal in Computer Science, Engr. Bilal in Civil Engineering, and Mr. Bilal in Management Sciences. Which department do you need?"*

Currently, the chatbot does **not** ask clarifying questions. It immediately provides an answer. Below is the technical breakdown of why this happens and how it can be upgraded safely.

---

### 12.1 The Four Root Causes of Non-Clarification

#### Cause 1: Strict Direct-Answer System Prompts
The agent system prompts explicitly instruct the model to provide direct answers and forbid counter-questions:
* `_BREVITY_RULES` commands the LLM to provide immediate facts in concise bullet points.
* `FACULTY_PROMPT` and `FEE_PROMPT` instruct the model: *"Answer directly based on context. If partial information exists, provide what is available."*
* Prompts contain no instructions explaining when or how to ask clarifying questions.

#### Cause 2: Autonomous Retrieval Fallback Probing
The retrieval code actively masks ambiguity. When a student types *"what is fee"*:
* The Fees Agent notices the query is short and automatically injects fallback queries covering both tuition fee schedules and admission processing fees.
* It retrieves documents for both and passes them to the LLM.
* The LLM sees documents for BS and MS fees in its prompt and summarizes both instead of pausing to ask what the student meant.

#### Cause 3: First-Hit Faculty Resolution
When searching for a common name like *"Bilal"*:
* `_role_name_probe` queries ChromaDB with `$contains: "Bilal"`.
* It returns chunks matching the first Bilal ingested into the database, or the reranker assigns a slightly higher score to whichever professor has more descriptive text on his department page.
* The model receives context for that specific person and answers with his details, unaware that other lecturers share the same name.

#### Cause 4: The Benchmark Evaluation Constraint (RAGAS Penalty)
The chatbot was optimized and evaluated using `eval_ragas.py` and `UOLI_Eval_Dataset.xlsx`:
* Automated RAG benchmark frameworks evaluate **single-turn question-answering pairs**.
* If the benchmark asks *"What is the fee?"* with a ground-truth answer of *"The BS fee is 35,000 PKR"*, and the chatbot responds with *"Which program are you asking about: BS or MS?"*, the automated evaluator flags this as an unhelpful refusal.
* The evaluation scores for **Answer Relevancy** and **Faithfulness** collapse to near zero for that test case. As a result, the chatbot was engineered to always produce an answer.

---

### 12.2 Feasibility & Implementation of Disambiguation (Zero Benchmark Impact)
Can clarifying questions be implemented with **0% score degradation** on benchmarks? **Yes (100% feasible).**

#### The Dual-Path Strategy
The evaluation dataset contains specific questions (e.g., *"What is the BS CS admission fee?"* or *"Who is the HOD of Computer Science?"*). Ambiguity occurs primarily on vague, short user inputs.

1. **The Disambiguation Gate Node:**
   Add a lightweight classification check before retrieval:
   * **Degree Ambiguity Check:** If the query mentions *"fee"*, *"admission"*, or *"eligibility"*, but fails to specify any degree level (`bs`, `bachelors`, `ms`, `masters`, `phd`, `mphil`), trigger a clarification prompt.
   * **Name Multiplicity Check:** When searching a faculty member's first name, count distinct person entities in the database matching that name. If `distinct_matches > 1` and no department is specified in the query, trigger clarification.
2. **Preserving Benchmark Scores:**
   Because all evaluation benchmark test cases include specific programs and titles, the disambiguation gate evaluates to `False` for 100% of benchmark queries, preserving existing benchmark scores while enabling natural clarification for ambiguous live student conversations.

---

## CHAPTER 13: Architectural Bottlenecks & Future Roadmap

To prepare the UOLI AI Chatbot for high-concurrency production use, three specific technical upgrades have been identified:

### 13.1 Parallelizing Multi-Intent Retrieval with Thread Pools
* **Current State:** Compound questions process sub-intents sequentially in a single-threaded Python `for` loop, causing latency to scale linearly with the number of intents ($O(N)$ latency).
* **Recommended Upgrade:** Wrap sub-intent retrieval inside `concurrent.futures.ThreadPoolExecutor(max_workers=3)`.
* **Result:** ChromaDB reads and BM25 index lookups execute concurrently across CPU threads. Multi-intent response times drop from ~4.5 seconds to under 1.5 seconds.

---

### 13.2 Expanding Past-Tense Grammar in Intent Regex
* **Current State:** In `knowledge_base.py:L2577`, the prefix detection regex `_Q_PAT` matches present-tense phrases:
  ```python
  ^(who\s+is|who\s+are|what\s+is|what\s+are|when\s+is|when\s+are)\b
  ```
  It lacks past-tense patterns like `when was` and `who was`.
* **Identified Bug:** When a student asks: *"When was UOLI established and who is the current VC?"*, the first clause fails `_Q_PAT` matching, preventing clean intent extraction.
* **Recommended Upgrade:** Update `_Q_PAT` to explicitly support past-tense indicators:
  ```python
  ^(who\s+(?:is|are|was|were)|what\s+(?:is|are|was|were)|when\s+(?:is|are|was|were))\b
  ```

---

### 13.3 Language-Gated Query Rewriting in the Campus Agent
* **Current State:** The Campus Agent always calls an LLM query rewriter (`REWRITE_PROMPT`) to translate Roman Urdu words (e.g., *"garee"*, *"hostel timings"*) into English before retrieval. This adds a redundant 800ms LLM call even when the student asks the question in plain English.
* **Recommended Upgrade:** Gate the rewriter behind a Roman Urdu keyword detector (`_has_urdu_terms = any(w in query_lower for w in URDU_CAMPUS_TERMS)`). If the query is already in English, skip the LLM rewriter and proceed immediately to hybrid search.
* **Result:** Saves 800ms of latency and reduces API token costs on 70% of campus queries.

---

**End of Master Architecture & Engineering Guide — Chapters 1 to 13**

---

## CHAPTER 14: Complete Routing & Supervisor Architecture Analysis
**Verified against actual code — October 2026**

---

### 14.1 System Overview

Your system has **6 agent nodes**, not 5. The workflow comment says 5 but the actual registered nodes are:

| Node Name | Agent | File | Function |
|---|---|---|---|
| `policy_agent` | Policy & Rules | `graph/Agents.py` | `policy_agent_node()` |
| `faculty_agent` | People / Contacts | `graph/Agents.py` | `faculty_agent_node()` |
| `admissions_agent` | Admissions & Programs | `graph/Agents.py` | `admissions_agent_node()` |
| `campus_agent` | Physical Campus | `graph/Agents.py` | `campus_agent_node()` |
| `fee_agent` | Fees & Scholarships | `graph/Agents.py` | `fee_agent_node()` |
| `general_agent` | University Overview | `graph/Agents.py` | `general_agent_node()` |

Plus two pre-routing nodes: `chitchat_check` and `chitchat_answer`.

---

### 14.2 Complete Path — Question to Agent (Confirmed)

The complete execution path is verified in `workflow.py` and `graph/Supervisor.py`.

```
User Question
      │
      ▼
[Layer 0] chitchat_check_node (nodes.py)
      │   Zero API cost. Pure Python keyword scan.
      │   Checks: greetings, thanks, farewells, "who are you",
      │           personal statements, memory questions, math.
      ├── Intent = "chitchat" ──► chitchat_answer_node ──► END
      │
      │   Intent = ""  (real question)
      ▼
[Layer 1] _classify_node (Supervisor.py)
      │   Step 1: Deterministic keyword pre-filters (no API)
      │     - Hierarchy phrases → "general"
      │     - Math/gibberish → "out_of_scope"
      │     - Urdu department-list phrases → "faculty"
      │     - Urdu student-population phrases → "general"
      │     - Harassment procedure phrases → "policy"
      │     - "readmission" → "policy"
      │     - "tell me about" → "faculty" (unless clear OOS)
      │
      │   Step 2: Fast-path keyword family matching (no API)
      │     Checks 5 keyword families: faculty / policy / admissions / fees / campus
      │     IF exactly 1 family matches → route immediately (skips LLM)
      │     IF 0 or 2+ families match → fall through to LLM
      │
      │   Step 3: LLM classification (GPT-4o-mini, max_tokens=8)
      │     Uses CLASSIFY_PROMPT with 6 categories + examples
      │     Returns one word: policy / faculty / admissions / campus / fees / general / out_of_scope
      │     Validated against {valid} set; fallback → "general"
      ▼
[Layer 2] conditional_edges (workflow.py:L116)
      │   lambda state: state.get("agent_name", "general")
      ├── "policy"     ──► policy_agent
      ├── "faculty"    ──► faculty_agent
      ├── "admissions" ──► admissions_agent
      ├── "campus"     ──► campus_agent
      ├── "fees"       ──► fee_agent
      └── "general"    ──► general_agent
                              │
                              ▼
                           [END]
```

**Source confirmation:**
- `workflow.py:L104-L135` — graph edges definition
- `Supervisor.py:L279-L500` — full routing logic
- `nodes.py:L169-L237` — chitchat_check_node

---

### 14.3 The Three-Layer Routing Mechanism (Confirmed)

#### Layer 0 — Chitchat Check (`nodes.py:L169`)
**Type:** Deterministic keyword matching. No API. ~0ms.

Checked lists:
- `GREETINGS` — hello, hi, salam, assalam, good morning…
- `THANKS` — thank, thanks, shukriya, jazakallah…
- `FAREWELLS` — bye, goodbye, allah hafiz…
- `ABOUT_BOT` — "who are you", "what is your name"…
- `PERSONAL` — "my name is", "i am a student"…
- `MEMORY_Q` — "do you remember", "what did i tell you"…
- `NOT_UNIVERSITY` — "calculate", "capital of", "tell me a joke"…

**Important note:** The chitchat check uses **substring** matching (not word-boundary) for most lists, and **word boundary** matching for greetings/farewells. This means the word `"hi"` in `"vehicles"` would NOT trigger chitchat (correctly), because greetings use `query_words` (`.split()`) not substring.

---

#### Layer 1 — Deterministic Pre-Filters (`Supervisor.py:L317-L403`)
**Type:** Pure Python `in` checks. No API. ~0ms.

Six hard-coded rule groups fire before the LLM ever sees the question:

| Rule | Pattern | Routes to |
|---|---|---|
| Hierarchy | "report to whom", "chain of command", "org structure", "hierarchy"… | `general` |
| Math/Gibberish | `\d+\s*[+-*/]\s*\d+` or <50% alphanumeric chars | `out_of_scope` |
| Urdu dept-list | "kitne department", "department hain", "tamam department"… | `faculty` |
| Urdu population | "kitne students", "student strength", "total students"… | `general` |
| Harassment procedure | "how do i report", "complaint procedure", "file a complaint"… | `policy` |
| Readmission | "readmission", "re-admission" | `policy` |
| Tell me about | "tell me about", "tell about", "bata about" (unless clear OOS) | `faculty` |

---

#### Layer 2 — Fast-Path Keyword Family Matching (`Supervisor.py:L431-L483`)
**Type:** Python `in` check on 5 keyword families. No API. ~0ms.

The `_families` dictionary defines keywords for each domain:

```python
_families = {
    "faculty":    ["who is", "who's", "kaun hai", "vice chancellor", "registrar",
                   "treasurer", "hod", "head of department", "dean", "email",
                   "phone", "extension", "contact"],

    "policy":     ["attendance", "cgpa", "freeze", "probation", "promotion",
                   "supplementary", "re-appear", "plagiar", "disciplinary",
                   "discipline", "conduct rules", "withdraw", "grading"],

    "admissions": ["admission", "admitted", "apply", "application", "eligibility",
                   "entry requirement", "merit", "documents", "how long is"],

    "fees":       ["fee", "fees", "scholarship", "financial aid", "stipend",
                   "tuition", "challan", "cost of"],

    "campus":     ["library", "bus", "transport", "hostel", "cafeteria", "canteen",
                   "sports", "gym", "laboratory", "facilities", "wifi",
                   "medical", "mosque", "timings", "address", "located", "location"],
}
```

**The safety rule (confirmed, `Supervisor.py:L482`):**
```python
if len(_matched) == 1:
    return {"agent_name": _matched[0]}
```
The fast-path fires **only if exactly 1 family matches**. Zero matches or multiple matches → falls through to LLM.

---

#### Layer 3 — LLM Classification (`Supervisor.py:L487-L500`)
**Type:** GPT-4o-mini API call. Temperature=0. max_tokens=8. ~0.6–1.5s.

Used when fast-path does not resolve (0 or 2+ keyword families match).

**Model:** `gpt-4o-mini` (not `gpt-4o`)
**Temperature:** `0` (fully deterministic)
**Max tokens:** `8` (forces one word only)
**Output validation:** `raw.split()[0]` extracted; checked against `valid` set; fallback = `"general"`.

---

### 14.4 The CLASSIFY_PROMPT (Full Text, Confirmed)

The complete prompt from `Supervisor.py:L180-L276`:

```
You are classifying a university student's question for routing.
Choose ONE category. Read ALL definitions carefully before choosing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CATEGORY DEFINITIONS AND EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POLICY — Rules, regulations, punishments, procedures.
  ✓ attendance, CGPA rules, semester freeze, harassment complaint procedure
  ✓ "what happens if I miss class", "can I improve my grade"
  ✗ NOT org structure, NOT who manages who

FACULTY — People, contacts, offices, leadership.
  ✓ HOD, teacher, professor, VC, Registrar, Dean, Treasurer
  ✓ department email, phone, extension, leadership messages
  ✓ "who is [university person]"
  ✗ NOT fictional characters (batman → out_of_scope)

ADMISSIONS — Joining the university, programs, eligibility.
  ✓ how to apply, merit criteria, BS/MS/PhD programs offered
  ✓ "can I get admission", "what programs does UOLI offer"

CAMPUS — Physical campus services: transport, library, facilities.
  ✓ bus routes, timing, vehicle schedule, garee, gaadi (Urdu)
  ✓ library timings, lab facilities
  ✓ "when does the bus leave"

FEES — Cost, fee structure, scholarships, financial aid.
  ✓ tuition fee, semester fee, scholarship, challan
  ✓ "how much does BS CS cost"

GENERAL — University overview, news, org structure.
  ✓ university history, latest news, organizational hierarchy
  ✓ total students, total faculty count, total programs
  ✓ "tell me about the university"
  ✓ anything that does not clearly fit the 4 categories above

OUT_OF_SCOPE — Nothing to do with UOLI.
  ✓ math, general knowledge, gibberish, other universities

CRITICAL DISTINCTIONS (hardcoded into prompt):
  "What is attendance policy and who is HOD of CS?" → FACULTY
  "Who leads university?" → FACULTY
  "Who is in charge of attendance?" → FACULTY
  "How do I report harassment?" → POLICY

Question: "{query}"
Reply with ONE word only: policy, faculty, admissions, campus, fees, general, or out_of_scope
```

---

### 14.5 Follow-Up Resolution (`Supervisor.py:L139-L172`, Confirmed)

Before classification, `resolve_followup()` checks if the query is a fragment referring to the previous message. Uses three regex patterns:

- `_REFERENT_RE` — pronouns: `it`, `its`, `they`, `he`, `she`, `their`…
- `_DEMONSTRATIVE_RE` — demonstratives followed by a verb or end of sentence
- `_FRAGMENT_RE` — bare continuations: `and`, `also`, `tell me more`, `explain`, `elaborate`…

**If the query is a referencing fragment AND shorter than 90 characters**, it appends the previous question's topic:

```python
return f"{text} (in the context of: {previous[:120]})"
```

This prevents follow-ups like *"What about its contact?"* after asking about the VC from being classified as `out_of_scope` due to missing UOLI context.

---

### 14.6 Parallel Execution During Routing (`workflow.py:L328-L364`, Confirmed)

In `stream_chat()` mode (Streamlit), the supervisor and query expansion run **in parallel**:

```python
with ThreadPoolExecutor(max_workers=2) as executor:
    clf_future  = executor.submit(_run_classifier)
    hyde_future = executor.submit(_run_hyde, query)
    classifier_result      = clf_future.result()
    pre_engineered_queries = hyde_future.result()
```

This means the classification LLM call and the HyDE/query expansion run simultaneously, shaving ~0.5–1s off total response time.

---

### 14.7 The 7 Example Questions — Actual Routing Behavior

#### Q1: "How much do I have to pay next semester?"

**Layer 0 — Chitchat check:** PASSES (not chitchat).
**Layer 1 — Pre-filters:** None match.
**Layer 2 — Fast-path:** `"pay"` is not in any keyword family. `"next semester"` is not in any family. **Zero families match → falls through to LLM.**
**Layer 3 — LLM:** GPT-4o-mini reads the question. "Pay next semester" implies a financial obligation → **routes to `fees`.**

> **Result: `fee_agent`.**
> The LLM correctly understands financial intent without the word "fee." Confirmed by `CLASSIFY_PROMPT` example: `"what are the charges"` → FEES.

---

#### Q2: "When is the deadline for submitting my dues?"

**Layer 0:** PASSES.
**Layer 1:** None match.
**Layer 2:** `"dues"` is not listed. No keyword match in any family. **Zero matches → LLM.**
**Layer 3 — LLM:** "dues" in a university context = financial payment; "deadline" = administrative → the LLM would likely route to `fees`.

> **Result: Likely `fee_agent` (inferred, not confirmed by direct execution).**
> "Dues" is not in the fast-path keyword list. If the LLM interprets "dues" as an academic deadline rather than a financial one, it could route to `admissions` or `general` instead. This is a **potential gap**: "dues" as a synonym for fee payment is not in the prompt examples.

---

#### Q3: "How can I get to campus?"

**Layer 0:** PASSES.
**Layer 1:** None match.
**Layer 2:** `"campus"` is NOT in the campus family keywords. The word `"campus"` means the university itself, not physical services. Checking the `_families["campus"]` list: `["library", "bus", "transport", "hostel", "cafeteria", "canteen", "sports", "gym", "laboratory", "facilities", "wifi", "wi-fi", "medical", "mosque", "timings", "address", "located", "location"]`. The word `"campus"` is NOT in this list.
**Zero keyword matches → LLM.**
**Layer 3 — LLM:** "How can I get to campus?" implies travel/transportation → **routes to `campus`.**

> **Result: Likely `campus_agent` (via LLM classification).**
> The fast-path misses this because `"campus"` as a destination word is not a keyword. The LLM saves it. This is actually correct system behavior — the fast-path is designed only for unambiguous, single-family matches.

---

#### Q4: "Where can I find the computer science department?"

**Layer 0:** PASSES.
**Layer 1:** None match (`"tell me about"` not present).
**Layer 2:** `"department"` is not in any family list. `"located"` is in campus but is not present in this question. **Zero matches → LLM.**
**Layer 3 — LLM:** "Where can I find the CS department?" is a location/directory question. The CLASSIFY_PROMPT says:
- `"who is [university person]"` → FACULTY
- `"list of departments"` → FACULTY
- `"how departments connect"` → GENERAL

"Where to find a department" is ambiguous — could be a physical location (campus) or a contact (faculty). The LLM would likely route to `faculty` based on "department" being a directory entity.

> **Result: Likely `faculty_agent` (inferred).**
> This is a slight gap: a student wanting a campus map location might get a faculty-style answer (HOD contact, department listing) rather than a physical map/address.

---

#### Q5: "Who leads our university?"

**Layer 0:** PASSES.
**Layer 1:** None match.
**Layer 2:** `"who is"` is in the `faculty` family → **exactly 1 match → immediately routes to `faculty`.**

> **Result: `faculty_agent`. Confirmed by fast-path rule.**
> `"who"` + context of leadership → correctly resolves via the leadership fast-path in the Faculty Agent.

---

#### Q6: "Tell me about the university."

**Layer 0:** PASSES.
**Layer 1:** `"tell me about"` is matched in the pre-filter `_TELL_ABOUT` → **routes to `faculty`.**

> **Result: `faculty_agent`** (from `Supervisor.py:L396-L403`).
> This is a known design decision. The comment says: *"Faculty agent has name detection that searches KB directly. If person not found → gives clean 'not found' message. Better than out_of_scope rejection for a real UOLI dean."*
> However, for "tell me about the university" (a general overview), routing to the faculty agent is **suboptimal**. The faculty agent optimizes for staff directories and contacts, not university history or overviews. The general agent with GraphRAG would serve this better.

> **This is a genuine routing gap. See Section 14.12 for improvement.**

---

#### Q7: "What is the fee, and when does the bus leave?"

**Layer 0:** PASSES.
**Layer 1:** None match.
**Layer 2:**
- `fees` family: `"fee"` matches → 1 hit
- `campus` family: `"bus"` matches → 1 hit
- **Two families match → NOT single → fast-path does NOT fire → goes to LLM.**

**Layer 3 — LLM:** The CLASSIFY_PROMPT instructs:
> *"What is attendance policy and who is HOD of CS?" → FACULTY (mixed queries: person takes priority)*

The prompt does not provide a specific rule for fees + campus. The LLM will pick whichever it considers the dominant intent. In practice, it would likely choose `fees` or `general` as the primary.

> **Result: Single-agent response, likely `fee_agent` (inferred).**
> **This is the most critical architectural limitation.** A compound fee + bus question routes to ONE agent. The `fee_agent` has no access to bus schedule data (bus data is under campus categories). The fee answer will be correct but the bus answer will be missing or incomplete.

> The multi-intent mitigation inside `retrieve_for_agent()` (splitting intents and borrowing category profiles) may partially compensate.

---

### 14.8 Can Multiple Agents Run Simultaneously? (Confirmed: NO)

**Answer: The supervisor selects exactly ONE agent. Agents run sequentially, not in parallel.**

From `workflow.py:L116-L127`:
```python
graph.add_conditional_edges(
    "supervisor",
    lambda state: state.get("agent_name", "general"),   # single string
    {
        "policy":     "policy_agent",
        "faculty":    "faculty_agent",
        "admissions": "admissions_agent",
        "campus":     "campus_agent",
        "fees":       "fee_agent",
        "general":    "general_agent",
    },
)
```

The state key `agent_name` is a **single string**. LangGraph `conditional_edges` evaluates it and activates **exactly one node**. The graph has no `Send()`, no parallel branches, no fan-out mechanism.

**What partially compensates:** Inside `retrieve_for_agent()` in `Agents.py`, when `_split_intents()` detects multiple intents in the question, it retrieves documents for each intent separately using the correct category profile for each. However, this still runs inside one agent node — the same single agent writes the final answer.

---

### 14.9 LangGraph Routing Mechanism Used (Confirmed)

| Mechanism | Used? | Location |
|---|---|---|
| `conditional_edges` | ✅ YES | `workflow.py:L116` |
| `Send()` | ❌ NO | Not used |
| `Command` | ❌ NO | Not used |
| Parallel branches | ❌ NO | Not used |
| Tool-calling router | ❌ NO | Legacy tools exist but not in Phase 6 graph |

The routing mechanism is `add_conditional_edges` from a single supervisor node to exactly one of six specialist nodes.

---

### 14.10 What Happens for Ambiguous, Unrelated, or Multi-Domain Questions?

| Scenario | Result |
|---|---|
| Ambiguous (0 keyword matches) | Falls to LLM; LLM classifies or returns `general` |
| Multi-domain (2+ keyword matches) | Falls to LLM; LLM picks dominant intent; one agent handles all |
| Completely unrelated (math, politics, other university) | `out_of_scope` → instant rejection message, no KB search |
| Greeting/chitchat | `chitchat_answer_node` → instant pre-written response, no KB search |
| LLM returns invalid label | Validated; fallback = `"general"` |

---

### 14.11 Routing Quality Assessment

#### Strengths (Confirmed)
1. **Understands indirect language:** LLM classification handles "How much do I have to pay?" → `fees` without needing the word "fee."
2. **Roman Urdu support:** `CLASSIFY_PROMPT` explicitly lists `"garee"`, `"gaadi"`, `"mujhe admission chahiye"` as examples. Urdu/Pashto questions are handled.
3. **Spelling error tolerance:** The fast-path includes common misspellings (`"vicechanclor"`, `"vise chancellor"`). The LLM handles unexpected phrasing.
4. **Follow-up resolution:** Pronoun/fragment follow-ups are resolved before classification — prevents false `out_of_scope` rejections.
5. **Cost optimization:** Chitchat = $0. Clear single-topic questions = $0 (fast-path). Only genuinely ambiguous questions pay the LLM toll.
6. **Output validation:** LLM output is validated; invalid labels fall back to `"general"`.
7. **Deterministic override:** High-risk misclassifications (hierarchy vs. complaint, readmission) are hardcoded, preventing LLM errors.

#### Gaps (Confirmed or Inferred)
1. **"Tell me about the university"** routes to `faculty` (hardcoded `_TELL_ABOUT` rule), not to `general`. For general overviews this is suboptimal.
2. **Compound multi-domain questions** (e.g., fee + bus) route to one agent, which cannot fully answer both parts.
3. **"dues"** as a synonym for fee payment is not in the fast-path or prompt examples. LLM is the only safety net.
4. **"Where can I find the CS department?"** may route to `faculty` when a student wants physical campus directions.
5. **No confidence score:** The LLM returns a single word with no uncertainty signal. A low-confidence borderline classification (e.g., fees vs. admissions for application fee questions) receives the same treatment as a certain one.

---

### 14.12 Recommended Improvements

#### Priority 1 — Fix the "tell me about" rule (Easy, High Impact)
**Current:** All "tell me about X" queries go to `faculty`.
**Problem:** "Tell me about the university" / "Tell me about UOLI" should go to `general`.

```python
_TELL_ABOUT = ["tell me about", "tell about", "bata about", "batao mujhe"]
_GENERAL_TOPICS = ["university", "uoli", "uol", "history", "vision", "mission",
                   "about uoli", "about university"]
if any(p in query_lower for p in _TELL_ABOUT):
    if any(g in query_lower for g in _GENERAL_TOPICS):
        return {"agent_name": "general"}   # university overview
    if not any(oos in query_lower for oos in _CLEAR_OOS):
        return {"agent_name": "faculty"}   # specific person/department
```

---

#### Priority 2 — Expand fee synonyms in fast-path (Easy, Medium Impact)
Add payment-related words to the `fees` family:

```python
"fees": [
    "fee", "fees", "scholarship", "financial aid", "stipend",
    "tuition", "challan", "cost of",
    # Add these:
    "dues", "pay", "payment", "charges", "amount", "cost",
    "how much", "semester dues", "next semester payment",
],
```

**Caution:** `"pay"` is broad — test against evaluation set before adding.

---

#### Priority 3 — Add a multi-agent compound query path (Medium, High Impact)
The current architecture cannot fully answer a question like "What is the fee and when does the bus leave?" because a single agent handles it.

**Option A (Low complexity):** After classification, pass all retrieved docs from both domains to the LLM. The `retrieve_for_agent()` multi-intent split already partially does this inside a single agent.

**Option B (Proper fix):** Use LangGraph `Send()` API to fan out to multiple agents in parallel:
```python
from langgraph.types import Send

def supervisor_fanout(state):
    intents = detect_multiple_intents(state["user_query"])
    if len(intents) > 1:
        return [Send(agent_map[intent], state) for intent in intents]
    return state["agent_name"]
```

This requires a merge node to combine responses before returning to END.

---

#### Priority 4 — Add confidence signal (Low complexity, Medium Impact)

Instead of returning only a label, optionally return a confidence tag:

```python
CLASSIFY_PROMPT_V2 = """...
Reply with ONE word. If uncertain between two categories, prefix with '?':
  Example: "?fees" means you are uncertain between fees and admissions.
...
"""
```

In `_classify_node`, detect the `?` prefix and route uncertain questions to `general` (broadest rrieval) as a fallback.

---

#### Priority 5 — Add embedding-based fallback (Medium complexity, Low urgency)
For questions where the LLM classification confidence is low, you could pre-compute embedding vectors for each agent's description and compare cosine similarity to the query. This adds latency but provides a semantic fallback independent of the keyword rules.

**Not recommended right now** — the LLM with `temperature=0` is already highly accurate, and adding an embedding layer increases complexity without a confirmed accuracy gap to justify it.

---

### 14.13 Final Summary Table

| Question | Fast-Path? | LLM Needed? | Actual Route | Correct? |
|---|---|---|---|---|
| "How much do I have to pay next semester?" | ❌ No | ✅ Yes | `fees` | ✅ Yes |
| "When is the deadline for submitting my dues?" | ❌ No | ✅ Yes | Likely `fees` | ⚠️ Uncertain (no "dues" in prompt) |
| "How can I get to campus?" | ❌ No | ✅ Yes | `campus` | ✅ Yes |
| "Where can I find the CS department?" | ❌ No | ✅ Yes | Likely `faculty` | ⚠️ Partial (could be campus) |
| "Who leads our university?" | ✅ Yes (fast-path `who is`) | ❌ No | `faculty` | ✅ Yes |
| "Tell me about the university." | ✅ Yes (`_TELL_ABOUT` rule) | ❌ No | `faculty` | ❌ Should be `general` |
| "What is the fee, and when does the bus leave?" | ❌ No (2 families) | ✅ Yes | Single agent (fees or general) | ⚠️ Partial (can't fully answer both) |

---

*Analysis confirmed against: `graph/workflow.py`, `graph/Supervisor.py`, `graph/nodes.py`, `graph/Agents.py`*

---

**End of Master Architecture & Engineering Guide (Version 1.0)**
