# ═══════════════════════════════════════════════════════════════
#  api.py  —  Phase 5 FastAPI Server  (Production-ready)
#  Endpoints:
#    POST /chat          → full response (used by eval_ragas.py)
#    GET  /chat/stream   → SSE token stream (used by JS frontend)
#    GET  /              → serves frontend/index.html
#    GET  /health        → system status
#    POST /scrape        → manual scrape trigger
#    GET  /sessions/count
#    DELETE /session/{id}
# ═══════════════════════════════════════════════════════════════
#
#  HOW TO RUN:
#    uvicorn api:app --reload --port 8000
#
# ═══════════════════════════════════════════════════════════════

import os
import sys
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from knowledge_base import KnowledgeBase
from graph.workflow import UniChatbot
from web_scraper import scrape_all_pages
from semantic_cache import SemanticCache
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Global instances (set at startup)
chatbot:   Optional[UniChatbot]    = None
kb:        Optional[KnowledgeBase] = None
sem_cache: Optional[SemanticCache] = None

# Path to the JS frontend folder
FRONTEND_DIR = Path(__file__).parent / "frontend"


# ── Scheduler setup ──────────────────────────────────────────
def _start_scheduler():
    """
    Start APScheduler to run scrape_all_pages() every N hours.
    Only starts if SCRAPE_INTERVAL_HOURS > 0 and pages are configured.
    """
    if config.SCRAPE_INTERVAL_HOURS <= 0:
        logger.info("Auto-scraping disabled (SCRAPE_INTERVAL_HOURS=0)")
        return None

    if not config.UNIVERSITY_PAGES:
        logger.info("No university pages configured — skipping scheduler")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        def scheduled_scrape():
            """This function runs automatically on the schedule."""
            logger.info("Scheduled scrape starting...")
            if kb:
                results = scrape_all_pages(kb)
                logger.info(
                    f"Scheduled scrape done: "
                    f"{results['scraped']} pages updated"
                )

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func    = scheduled_scrape,
            trigger = "interval",
            hours   = config.SCRAPE_INTERVAL_HOURS,
            id      = "website_scrape",
        )
        scheduler.start()
        logger.info(
            f"Scheduler started — scraping every {config.SCRAPE_INTERVAL_HOURS} hours"
        )
        return scheduler

    except ImportError:
        logger.warning("APScheduler not installed. Auto-scraping disabled.")
        logger.warning("Run: pip install apscheduler")
        return None


# ── Startup / Shutdown ────────────────────────────────────────
scheduler_instance = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global chatbot, kb, scheduler_instance, sem_cache

    logger.info("Starting University Chatbot API...")

    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set in .env file")

    # Load knowledge base
    kb = KnowledgeBase()
    if kb.get_doc_count() == 0:
        logger.warning("Knowledge base is empty. Run: python setup_knowledge_base.py")

    # Create chatbot (includes Redis + checkpointing setup)
    chatbot = UniChatbot(kb)

    # ── Semantic Cache (Valkey db=1) ─────────────────────────
    # Uses the same embeddings already loaded in KnowledgeBase so
    # no extra API client is needed. Stores on db=1 to stay isolated
    # from chat sessions on db=0. Falls back gracefully if Valkey
    # is not running (sem_cache.client == None → cache disabled).
    sem_cache = SemanticCache()
    logger.info("Semantic cache initialised (Valkey db=1)")

    # Start auto-scraper scheduler
    scheduler_instance = _start_scheduler()

    logger.info(f"API ready    → http://{config.API_HOST}:{config.API_PORT}")
    logger.info(f"Chat UI      → http://localhost:{config.API_PORT}/")
    logger.info(f"API docs     → http://localhost:{config.API_PORT}/docs")

    yield

    # Shutdown
    if scheduler_instance:
        scheduler_instance.shutdown(wait=False)
    logger.info("API shutdown complete.")


# ── FastAPI app ───────────────────────────────────────────────
app = FastAPI(
    title   = f"{config.UNIVERSITY_NAME} Chatbot API",
    version = "5.0.0",
    lifespan= lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static assets (CSS, JS) from the frontend/ folder
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Pydantic models ───────────────────────────────────────────

class ChatRequest(BaseModel):
    message:    str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., min_length=1, max_length=100)

    model_config = {
        "json_schema_extra": {
            "example": {
                "message":    "What is the CS fee?",
                "session_id": "student_ali_123",
            }
        }
    }

class ChatResponse(BaseModel):
    answer:     str
    intent:     str
    session_id: str

class HealthResponse(BaseModel):
    status:          str
    bot_name:        str
    university:      str
    docs_loaded:     int
    active_sessions: int
    redis_connected: bool
    scheduler_on:    bool

class ScrapeResponse(BaseModel):
    status:  str
    scraped: int
    failed:  int
    message: str


# ── Endpoints ─────────────────────────────────────────────────

@app.get("/", tags=["Frontend"])
async def serve_frontend():
    """
    Serve the production JS chat UI.
    Falls back to an HTML info page if frontend/ folder is missing.
    """
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse(
        content=f"""<!DOCTYPE html><html><body>
        <h2>{config.BOT_NAME}</h2>
        <p>Frontend not built yet. API is running.</p>
        <p>API docs: <a href="/docs">/docs</a></p>
        </body></html>""",
        status_code=200,
    )


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """System health check — shows Redis status, session count, etc."""
    docs     = kb.get_doc_count() if kb else 0
    sessions = chatbot.get_session_count() if chatbot else 0
    redis_on = chatbot.store.using_redis if chatbot else False

    return HealthResponse(
        status          = "healthy" if chatbot else "starting",
        bot_name        = config.BOT_NAME,
        university      = config.UNIVERSITY_NAME,
        docs_loaded     = docs,
        active_sessions = sessions,
        redis_connected = redis_on,
        scheduler_on    = scheduler_instance is not None,
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Send a message and get a FULL response (non-streaming).
    Used by eval_ragas.py and API integrations. DO NOT REMOVE OR MODIFY.
    For streaming (JS frontend), use GET /chat/stream instead.
    """
    if not chatbot:
        raise HTTPException(status_code=503, detail="Chatbot not ready")

    # ── Semantic cache check ──────────────────────────────────
    # Embed the query using the already-loaded KB embeddings (no extra
    # API client). If a cached answer exists with >=90% cosine similarity
    # return it instantly — zero LLM cost, ~5ms latency.
    _query_vec = None
    if sem_cache and sem_cache.client:
        try:
            _query_vec = kb.embeddings.embed_query(request.message)
            _cached = sem_cache.check_cache(_query_vec)
            if _cached:
                logger.info(f"⚡ Cache HIT  → '{request.message[:60]}'")
                return ChatResponse(
                    answer     = _cached,
                    intent     = "cache_hit",
                    session_id = request.session_id,
                )
        except Exception as _ce:
            logger.warning(f"Cache check error (continuing): {_ce}")

    try:
        result = chatbot.chat(
            user_message = request.message,
            session_id   = request.session_id,
        )
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    answer = result if isinstance(result, str) else result.get("answer", str(result))

    # ── Save to cache for next time ───────────────────────────
    if sem_cache and sem_cache.client and _query_vec and answer:
        try:
            sem_cache.save_to_cache(request.message, answer, _query_vec)
            logger.info(f"💾 Cache SAVED → '{request.message[:60]}'")
        except Exception as _se:
            logger.warning(f"Cache save error (non-fatal): {_se}")

    return ChatResponse(
        answer     = answer,
        intent     = "general",
        session_id = request.session_id,
    )


@app.get("/chat/stream", tags=["Chat"])
async def chat_stream(
    message:    str = Query(..., min_length=1, max_length=2000),
    session_id: str = Query(..., min_length=1, max_length=100),
):
    """
    Server-Sent Events (SSE) streaming endpoint for the JS frontend.

    WHY SSE NOT WEBSOCKETS: SSE is unidirectional (server to client), which
    is exactly what token streaming needs. It works over plain HTTP with
    no upgrade handshake, survives proxies and load balancers, and is
    natively supported by browser EventSource without any library.

    Protocol:
        data: <token>           — one LLM token chunk
        data: [SOURCES]<json>   — JSON array of source URLs after completion
        data: [DONE]            — stream ended, close the EventSource

    JS client usage:
        const es = new EventSource('/chat/stream?message=hi&session_id=abc')
        es.onmessage = e => {
            if (e.data.startsWith('[SOURCES]')) { ... }
            else if (e.data === '[DONE]') { es.close(); }
            else { appendToken(e.data); }
        }
    """
    if not chatbot:
        raise HTTPException(status_code=503, detail="Chatbot not ready")

    import json

    async def event_generator():
        try:
            # ── Semantic cache check ──────────────────────────
            # Embed once here; reuse vector for save after streaming.
            _q_vec = None
            if sem_cache and sem_cache.client:
                try:
                    _q_vec = kb.embeddings.embed_query(message)
                    _cached = sem_cache.check_cache(_q_vec)
                    if _cached:
                        logger.info(f"⚡ Stream Cache HIT → '{message[:60]}'")
                        # Yield the full cached answer as a single chunk so
                        # the client sees it stream-in (no LLM call at all).
                        yield f"data: {_cached}\n\n"
                        yield f"data: [SOURCES]{json.dumps([])}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                except Exception as _ce:
                    logger.warning(f"Stream cache check error (continuing): {_ce}")

            # ── Normal streaming path ─────────────────────────
            last_docs    = []
            full_answer  = []  # buffer so we can save to cache after done
            for token in chatbot.stream_chat(message, session_id):
                full_answer.append(token)
                yield f"data: {token}\n\n"

            # Collect source URLs from last retrieval (stored on chatbot)
            last_docs = getattr(chatbot, "_last_docs", [])
            sources = list(dict.fromkeys(
                d.metadata.get("source", "")
                for d in last_docs
                if d.metadata.get("source")
            ))
            yield f"data: [SOURCES]{json.dumps(sources)}\n\n"
            yield "data: [DONE]\n\n"

            # ── Save full answer to cache ─────────────────────
            _answer_text = "".join(full_answer).strip()
            if sem_cache and sem_cache.client and _q_vec and _answer_text:
                try:
                    sem_cache.save_to_cache(message, _answer_text, _q_vec)
                    logger.info(f"💾 Stream Cache SAVED → '{message[:60]}'")
                except Exception as _se:
                    logger.warning(f"Stream cache save error (non-fatal): {_se}")

        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield f"data: [ERROR]{str(e)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",   # disables nginx buffering
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.delete("/session/{session_id}", tags=["Sessions"])
async def clear_session(session_id: str):
    """Clear conversation history for a session."""
    if chatbot:
        chatbot.clear_history(session_id)
    return {"status": "cleared", "session_id": session_id}


@app.get("/sessions/count", tags=["Sessions"])
async def session_count():
    """How many active sessions are stored."""
    count = chatbot.get_session_count() if chatbot else 0
    return {"active_sessions": count}


@app.post("/scrape", response_model=ScrapeResponse, tags=["Knowledge Base"])
async def trigger_scrape():
    """
    Manually trigger a website scrape right now.
    Useful for testing or forcing an immediate update.
    The scraper also runs automatically on the configured schedule.
    """
    if not kb:
        raise HTTPException(status_code=503, detail="Knowledge base not ready")

    if not config.UNIVERSITY_PAGES:
        return ScrapeResponse(
            status  = "skipped",
            scraped = 0,
            failed  = 0,
            message = "No university pages configured in config.py",
        )

    try:
        results = scrape_all_pages(kb)
        return ScrapeResponse(
            status  = "success",
            scraped = results["scraped"],
            failed  = results["failed"],
            message = (
                f"Scraped {results['scraped']} pages successfully. "
                f"{results['failed']} failed."
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scrape failed: {str(e)}")
