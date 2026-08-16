# ═══════════════════════════════════════════════════════════════
#  setup_knowledge_base.py  —  University of Loralai (UOLi)
#  Run this ONCE before starting the chatbot.
#
#  WHAT THIS DOES (in order):
#    Step 1 — Scrapes all configured UOLI website pages → ChromaDB
#    Step 2 — Loads all .docx / .pdf / .txt files from data/docs/
#
#  NO hardcoded FAQs. All knowledge comes from:
#    • Live UOLI website (web scraping)
#    • Official policy documents (.docx files you placed in data/docs/)
#
#  HOW TO RUN:
#    python setup_knowledge_base.py
#
#  HOW TO UPDATE KNOWLEDGE (run anytime):
#    python setup_knowledge_base.py --force
#    (or just use the "Scrape Website Now" button in the sidebar)
# ═══════════════════════════════════════════════════════════════

# import os
# import sys
# import time
# import argparse

# sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# from knowledge_base import KnowledgeBase,IngestionResult,IngestionSummary
# from web_scraper import scrape_page, scrape_all_pages
# from advanced_crawler import UniversityCrawler
# import config
# # ADD with your other imports at the top of setup_knowledge_base.py
# from parent_doc_store import ParentDocStore, split_into_parent_child
# from langchain_text_splitters import RecursiveCharacterTextSplitter

# # ─────────────────────────────────────────────────────────────
# #  CATEGORY HINTS — map filename keywords to intent categories
# #  Used when loading .docx / .pdf / .txt files from data/docs/
# # ─────────────────────────────────────────────────────────────
# CATEGORY_HINTS = {
#     # ── Policy documents ─────────────────────────────────────
#     "undergraduate": "policy",
#     "graduate":      "policy",
#     "disciplinary":  "policy",
#     "conduct":       "policy",
#     "harassment":    "policy",
#     "anti-drug":     "policy",
#     "drug":          "policy",
#     "ed-rules":      "policy",
#     "bpas":          "policy",
#     "balochistan":   "policy",   # covers 04_Balochistan_Universities_Act
#     "universities":  "policy",   # extra safety for act documents
#     "act":           "policy",   # any official act or law document
#     "hec":           "policy",   # HEC policy documents

#     # ── Fees ─────────────────────────────────────────────────
#     "fee":           "fees",
#     "fees":          "fees",
#     "finance":       "fees",

#     # ── Admissions ───────────────────────────────────────────
#     "admission":     "admissions",
#     "admissions":    "admissions",
#     "prospectus":    "admissions",
#     "eligibility":   "admissions",

#     # ── Schedule ─────────────────────────────────────────────
#     "schedule":      "schedule",
#     "timetable":     "schedule",
#     "calendar":      "schedule",
#     "exam":          "schedule",

#     # ── Faculty ──────────────────────────────────────────────
#     "faculty":       "faculty",
#     "department":    "faculty",
#     "staff":         "faculty",

#     # ── General ──────────────────────────────────────────────
#     "library":       "general",
#     "hostel":        "general",
#     "transport":     "general",
#     "bus":           "general",
#     "alumni":        "general",
#     "downloads":     "general",
# }

# SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")

# def _detect_category(filename: str) -> str:
#     """Detect knowledge category from filename keywords."""
#     name = filename.lower()
#     for keyword, category in CATEGORY_HINTS.items():
#         if keyword in name:
#             return category
#     return "general"


# def step1_scrape_website(kb: KnowledgeBase) -> dict:
#     """
#     Full-coverage UOLI website scraper.
#     Strategy:
#       1. scrape_all_pages() in web_scraper.py always starts from GUARANTEED_PAGES
#          (all departments, offices, leadership pages).
#       2. Then the crawler recursively discovers additional pages not in the static list.
#       3. Both sets are merged and deduplicated before indexing.
#     """
#     from web_scraper import GUARANTEED_PAGES
#     summary = {"scraped": 0, "failed": 0, "skipped": 0}

#     # --- Phase A: Guaranteed Static Pages ---
#     print("  Phase A: Scraping all guaranteed university pages...")
#     results_a = scrape_all_pages(kb, urls=GUARANTEED_PAGES)
#     print(f"  Phase A done — New: {results_a['scraped']}, Skipped: {results_a.get('skipped',0)}, Failed: {results_a['failed']}")
#     print()

#     # --- Phase B: Auto-Crawler (finds pages we missed) ---
#     print("  Phase B: Running crawler to discover additional pages...")
#     crawler = UniversityCrawler("https://uoli.edu.pk/", "uoli.edu.pk")
#     all_crawled_urls = crawler.crawl(max_pages=300)

#     # Only pass new URLs not already in our guaranteed list
#     guaranteed_set = {u.rstrip("/") for u in GUARANTEED_PAGES}
#     extra_urls = [
#         u for u in all_crawled_urls
#         if u.rstrip("/") not in guaranteed_set
#     ]
#     print(f"  Crawler found {len(all_crawled_urls)} pages, {len(extra_urls)} new to scrape.")

#     if extra_urls:
#         results_b = scrape_all_pages(kb, urls=extra_urls)
#         print(f"  Phase B done — New: {results_b['scraped']}, Skipped: {results_b.get('skipped',0)}, Failed: {results_b['failed']}")
#         summary["scraped"] = results_a["scraped"] + results_b["scraped"]
#         summary["failed"]  = results_a["failed"]  + results_b["failed"]
#         summary["skipped"] = results_a.get("skipped", 0) + results_b.get("skipped", 0)
#     else:
#         print("  No extra pages found by crawler.")
#         summary["scraped"] = results_a["scraped"]
#         summary["failed"]  = results_a["failed"]
#         summary["skipped"] = results_a.get("skipped", 0)

#     return summary


# def step2_load_documents(kb: KnowledgeBase) -> dict:
#     """
#     Load all supported documents from data/docs/ folder.
#     Supports: .docx, .pdf, .txt, .md

#     Place your downloaded policy documents here:
#       • anti-drug policy.docx
#       • conduct-rules.docx
#       • disciplinary-rules.docx
#       • graduate-rules.docx
#       • hec-harassment-policy.docx
#       • undergraduate-rules.docx
#     """
#     summary = {"loaded": 0, "failed": 0, "skipped": 0}
#     docs_folder = "./data/docs"

#     if not os.path.exists(docs_folder):
#         print(f"  data/docs/ folder not found — creating it.")
#         os.makedirs(docs_folder, exist_ok=True)
#         print(f"  Place your .docx policy files there and re-run this script.")
#         return summary

#     # Collect only supported files (exclude sub-directories like "downloaded/")
#     files = sorted([
#         f for f in os.listdir(docs_folder)
#         if not f.startswith(".")
#         and os.path.isfile(os.path.join(docs_folder, f))
#         and f.lower().endswith(SUPPORTED_EXTENSIONS)
#     ])

#     if not files:
#         print(f"  No documents found in data/docs/")
#         print(f"  Place your .docx policy files there and re-run.")
#         return summary

#     print(f"  Found {len(files)} document(s) in data/docs/:")
#     print()

#     for filename in files:
#         filepath = os.path.join(docs_folder, filename)
#         category = _detect_category(filename)
#         ext      = filename.lower().split(".")[-1]

#         print(f"  Loading: {filename}")
#         print(f"           Category: {category}")

#         if ext == "pdf":
#             result = kb.load_pdf(filepath, category=category)
#         elif ext == "docx":
#             result = kb.load_docx(filepath, category=category)
#         elif ext in ("txt", "md"):
#             result = kb.load_text_file(filepath, category=category)
#         else:
#             result = IngestionResult(filename, "skipped", message="unsupported format")

#         # Count by status
#         if result.status in ("added", "updated"):
#             summary["loaded"] += 1
#         elif result.status == "failed":
#             summary["failed"] += 1
#         elif result.status in ("skipped", "empty"):
#             summary["skipped"] += 1

#         print()

#     return summary


# def main():
#     parser = argparse.ArgumentParser(
#         description="Setup UOLi Chatbot Knowledge Base"
#     )
#     parser.add_argument(
#         "--force", action="store_true",
#         help="Force re-setup even if KB already has documents"
#     )
#     args = parser.parse_args()

#     # ── Header ────────────────────────────────────────────────
#     print()
#     print("═" * 62)
#     print("   University of Loralai — Chatbot Knowledge Base Setup")
#     print("═" * 62)
#     print(f"   University : {config.UNIVERSITY_NAME}")
#     print(f"   Bot Name   : {config.BOT_NAME}")
#     print(f"   Data source: Web scraping + Local documents")
#     print(f"   No hardcoded FAQs — all knowledge from real sources")
#     print("═" * 62)
#     print()

#     # ── Initialize KB ─────────────────────────────────────────
#     print("Initializing knowledge base...")
#     kb = KnowledgeBase()

#     if args.force:
#         kb.reset()

#     # ── Check existing docs ───────────────────────────────────
#     current_count = kb.get_doc_count()
#     if current_count > 0 and not args.force:
#         print(f"\nKnowledge base already has {current_count:,} document chunks.")
#         print()
#         print("Options:")
#         print("  y = Re-scrape and add more (keeps existing data)")
#         print("  n = Exit without changes")
#         print("  (Tip: run with --force to skip this prompt)")
#         choice = input("\nProceed? (y/n): ").strip().lower()
#         if choice != "y":
#             print("\nExiting. Knowledge base unchanged.")
#             print(f"Run: streamlit run app.py  to start the chatbot.\n")
#             return

#     print()
#     print()

#     # ── E2: Clear old parents before fresh rebuild ────────────────────────────
#     kb.parent_store.clear()

    
#     # ══════════════════════════════════════════════════════════
#     #  STEP 1 — Scrape UOLI Website
#     # ══════════════════════════════════════════════════════════
#     print("─" * 62)
#     print("  STEP 1 — Scraping UOLI Website Pages")
#     print("─" * 62)
#     t1 = time.time()
#     web_summary = step1_scrape_website(kb)
#     t1_elapsed  = round(time.time() - t1)
#     print()
#     print(f"  Web scraping done in {t1_elapsed}s:")
#     print(f"    ✓ Pages scraped  : {web_summary['scraped']}")
#     print(f"    ✗ Pages failed   : {web_summary['failed']}")
#     print(f"    - Pages skipped  : {web_summary['skipped']}")

#     print()

#     # ══════════════════════════════════════════════════════════
#     #  STEP 2 — Load Policy Documents from data/docs/
#     # ══════════════════════════════════════════════════════════
#     print("─" * 62)
#     print("  STEP 2 — Loading Policy Documents from data/docs/")
#     print("─" * 62)
#     t2 = time.time()
#     doc_summary = step2_load_documents(kb)
#     t2_elapsed  = round(time.time() - t2)
#     print(f"  Document loading done in {t2_elapsed}s:")
#     print(f"    ✓ Documents loaded : {doc_summary['loaded']}")
#     print(f"    ✗ Documents failed : {doc_summary['failed']}")
#     print(f"    - Documents skipped: {doc_summary['skipped']}")

#     print()

#     # ══════════════════════════════════════════════════════════
#     #  SUMMARY
#     # ══════════════════════════════════════════════════════════
#     # Rebuild BM25 once after all documents loaded
#     print("Rebuilding BM25 index...")
#     kb._rebuild_bm25()
#     kb._save_bm25_to_disk()
#     print("BM25 ready ✓")
#     # ── E2: Save all parent chunks to disk after KB is built ──────────────────
#     kb.parent_store.save()
#     print(f"Parent store saved ✓  ({len(kb.parent_store)} parents)")
#     total_chunks = kb.get_doc_count()
#     print("═" * 62)
#     print("   SETUP COMPLETE")
#     print("═" * 62)
#     print(f"   Web pages scraped   : {web_summary['scraped']} / {len(config.UNIVERSITY_PAGES)}")
#     print(f"   Documents loaded    : {doc_summary['loaded']}")
#     print(f"   ──────────────────────────────────────────────")
#     print(f"   Total KB chunks     : {total_chunks:,}")
#     print("═" * 62)
#     print()

#     if web_summary["scraped"] == 0 and doc_summary["loaded"] == 0:
#         print("⚠  WARNING: No content was loaded into the knowledge base!")
#         print("   Check your internet connection and data/docs/ folder.")
#         print()
#     else:
#         print("✓  Knowledge base is ready.")
#         print()
#         print("   Next steps:")
#         print("   1. Run:  streamlit run app.py     (Streamlit UI)")
#         print("   2. Run:  uvicorn api:app --reload (FastAPI server)")
#         print()
#         print("   The chatbot will auto-scrape the website every")
#         print(f"   {config.SCRAPE_INTERVAL_HOURS} hours to keep knowledge fresh.")
#     print()


# if __name__ == "__main__":
#     main() ## this is working only one url scrping i change it to below code so that next no all rerun required

import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_base import KnowledgeBase,IngestionResult,IngestionSummary
from web_scraper import scrape_page, scrape_all_pages
from advanced_crawler import UniversityCrawler
import config
# ADD with your other imports at the top of setup_knowledge_base.py
from parent_doc_store import ParentDocStore, split_into_parent_child
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ─────────────────────────────────────────────────────────────
#  CATEGORY HINTS — map filename keywords to intent categories
#  Used ONLY for local files in data/docs/ (.pdf/.docx/.txt/.md).
#  URLs are categorised by config.resolve_category(); this table exists because
#  a filename has no path structure for those rules to read.
#
#  Every value here must be in config.CATEGORIES — asserted below. Two values in
#  the previous version were not: "fees" and "schedule". Neither is read by any
#  retrieval profile in graph/Agents.py, so a file named "Fee_Schedule_2026.pdf"
#  or "Exam_Timetable.pdf" dropped into data/docs/ would have been embedded,
#  stored, counted in the totals and then been retrievable by nothing at all.
#  Only luck kept that dormant: all five .docx files currently present match a
#  "policy" keyword first.
#
#  Order matters — first matching keyword wins.
# ─────────────────────────────────────────────────────────────
CATEGORY_HINTS = {
    # ── Policy documents ─────────────────────────────────────
    "undergraduate": "policy",
    "graduate":      "policy",
    "disciplinary":  "policy",
    "conduct":       "policy",
    "harassment":    "policy",
    "anti-drug":     "policy",
    "drug":          "policy",
    "ed-rules":      "policy",
    "bpas":          "policy",
    "balochistan":   "policy",   # covers 04_Balochistan_Universities_Act
    "universities":  "policy",   # extra safety for act documents
    "act":           "policy",   # any official act or law document
    "hec":           "policy",   # HEC policy documents

    # ── Academic schedules ───────────────────────────────────
    # Was "schedule", which no agent reads. Exam and semester timetables are
    # academic-regulation artefacts and belong beside the academic rules, which
    # are already "policy" — that is also where the examination regulations the
    # policy agent answers from live.
    "schedule":      "policy",
    "timetable":     "policy",
    "calendar":      "policy",
    "exam":          "policy",

    # ── Fees ─────────────────────────────────────────────────
    # Was "fees", which no agent reads. The fees profile reads
    # ["admissions", "overview", "general", "offices"], so fee documents go to
    # "admissions" — the same decision made for /fee-structure/ in config.py.
    "fee":           "admissions",
    "fees":          "admissions",
    "finance":       "admissions",

    # ── Admissions ───────────────────────────────────────────
    "admission":     "admissions",
    "admissions":    "admissions",
    "prospectus":    "admissions",
    "eligibility":   "admissions",

    # ── Faculty ──────────────────────────────────────────────
    "faculty":       "faculty",
    "department":    "faculty",
    "staff":         "faculty",

    # ── General ──────────────────────────────────────────────
    "library":       "general",
    "hostel":        "general",
    "transport":     "general",
    "bus":           "general",
    "alumni":        "general",
    "downloads":     "general",
}

_bad_hints = {k: v for k, v in CATEGORY_HINTS.items() if v not in config.CATEGORIES}
if _bad_hints:
    raise ValueError(
        f"setup_knowledge_base.CATEGORY_HINTS maps to categories outside "
        f"config.CATEGORIES {config.CATEGORIES}: {_bad_hints}. Files matching "
        "those keywords would be embedded but unretrievable."
    )

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")

def _detect_category(filename: str) -> str:
    """Category for a LOCAL FILE, from filename keywords.

    Not for URLs — use config.resolve_category(url) for those. The name is kept
    because callers in this module already use it, but it no longer shadows a
    same-named URL function in web_scraper.py: that one has been deleted.
    """
    name = filename.lower()
    for keyword, category in CATEGORY_HINTS.items():
        if keyword in name:
            return category
    return config.FALLBACK_CATEGORY


def step1_scrape_website(kb: KnowledgeBase) -> dict:
    """
    Full-coverage UOLI website scraper.
    Strategy:
      1. scrape_all_pages() in web_scraper.py always starts from GUARANTEED_PAGES
         (all departments, offices, leadership pages).
      2. Then the crawler recursively discovers additional pages not in the static list.
      3. Both sets are merged and deduplicated before indexing.
    """
    from web_scraper import GUARANTEED_PAGES
    # The PDF keys are carried here as well as the page keys. They were not, and
    # the summary printed by main() reads web_summary.get("pdfs", 0) — so it
    # reported "PDFs ingested: 0" on every successful rebuild. That is exactly the
    # failure the comment beside that print warns about: a rebuild that ingested
    # no documents at all looked identical to one that ingested fifty-five.
    summary = {"scraped": 0, "failed": 0, "skipped": 0,
               "pdfs": 0, "pdfs_skipped": 0, "pdfs_refused": 0, "pdfs_failed": 0}

    def _merge_pdf_counts(*runs) -> None:
        """Sum the PDF counters across the scrape phases that produced them."""
        for key in ("pdfs", "pdfs_skipped", "pdfs_refused", "pdfs_failed"):
            summary[key] = sum(r.get(key, 0) for r in runs if r)

    # --- Phase A: Guaranteed Static Pages ---
    print("  Phase A: Scraping all guaranteed university pages...")
    results_a = scrape_all_pages(kb, urls=GUARANTEED_PAGES)
    print(f"  Phase A done — New: {results_a['scraped']}, Skipped: {results_a.get('skipped',0)}, Failed: {results_a['failed']}")
    print()

    # --- Phase B: Auto-Crawler (finds pages we missed) ---
    print("  Phase B: Running crawler to discover additional pages...")
    crawler = UniversityCrawler("https://uoli.edu.pk/", "uoli.edu.pk")
    all_crawled_urls = crawler.crawl(max_pages=300)

    # Only pass new URLs not already in our guaranteed list
    guaranteed_set = {u.rstrip("/") for u in GUARANTEED_PAGES}
    extra_urls = [
        u for u in all_crawled_urls
        if u.rstrip("/") not in guaranteed_set
    ]
    print(f"  Crawler found {len(all_crawled_urls)} pages, {len(extra_urls)} new to scrape.")

    if extra_urls:
        # include_guaranteed=False: Phase A has already fetched all 78 guaranteed
        # pages. scrape_all_pages used to prepend them unconditionally, so Phase B
        # re-fetched every one of them — always ending in "unchanged", so it cost
        # no embedding money, but it doubled the wall clock and the image
        # downloads of every rebuild.
        #
        # A run of only the extras is usually below page_furniture's 20-page
        # frequency floor, so it cannot measure the site's furniture for itself.
        # It does not need to: Phase A wrote the furniture set to
        # web_scraper.FURNITURE_CACHE, and Phase B reuses it. Without that cache
        # this change would silently re-admit the footer on crawler-discovered
        # pages, leaving the corpus clean or dirty depending on which phase
        # happened to ingest a page.
        results_b = scrape_all_pages(kb, urls=extra_urls, include_guaranteed=False)
        print(f"  Phase B done — New: {results_b['scraped']}, Skipped: {results_b.get('skipped',0)}, Failed: {results_b['failed']}")
        summary["scraped"] = results_a["scraped"] + results_b["scraped"]
        summary["failed"]  = results_a["failed"]  + results_b["failed"]
        summary["skipped"] = results_a.get("skipped", 0) + results_b.get("skipped", 0)
        _merge_pdf_counts(results_a, results_b)
    else:
        print("  No extra pages found by crawler.")
        summary["scraped"] = results_a["scraped"]
        summary["failed"]  = results_a["failed"]
        summary["skipped"] = results_a.get("skipped", 0)
        _merge_pdf_counts(results_a)

    return summary


def step2_load_documents(kb: KnowledgeBase) -> dict:
    """
    Load any local documents the owner has dropped in data/docs/ — OPTIONAL.

    THIS STEP IS NO LONGER REQUIRED, AND AN EMPTY data/docs/ IS THE NORMAL CASE.

    It used to be the only way policy documents entered the corpus. Five .docx
    files were downloaded by hand, opened in Word, stripped of their letterhead
    and scanner artefacts, and saved locally:

        01_HEC_Sexual_Harassment_Policy.docx   02_Graduate_Academic_Rules.docx
        03_Disciplinary_Rules.docx             04_Balochistan_Universities_Act_2022.docx
        05_Anti_Drug_Policy.docx

    That worked, and it also meant the corpus froze on the day it was done. By
    the time this comment was written the site had replaced or removed some of
    those documents and added roughly twenty more, and nothing in the pipeline
    noticed. A manual step that must be repeated by hand is a manual step that
    stops being repeated.

    Documents now arrive automatically: web_scraper pass 3 discovers every PDF the
    site links, reads it (OCR'ing it if it is a scan), and re-reads it only when
    its bytes change. See pdf_ingest.

    So this step is kept for one narrow purpose — a document the owner holds that
    the site does not publish — and it must never block a rebuild. A missing or
    empty data/docs/ is reported and skipped, not treated as an error.
    """
    summary = {"loaded": 0, "failed": 0, "skipped": 0}
    docs_folder = "./data/docs"

    if not os.path.exists(docs_folder):
        # Deliberately not created. An empty folder invites the belief that
        # something is supposed to go in it.
        print("  No data/docs/ folder — nothing to load.")
        print("  This is normal: documents come from the site automatically.")
        return summary

    # Collect only supported files (exclude sub-directories like "downloaded/")
    files = sorted([
        f for f in os.listdir(docs_folder)
        if not f.startswith(".")
        and os.path.isfile(os.path.join(docs_folder, f))
        and f.lower().endswith(SUPPORTED_EXTENSIONS)
    ])

    if not files:
        print("  data/docs/ is empty — nothing to load.")
        print("  This is normal: documents come from the site automatically")
        print("  (web_scraper pass 3). Local files are only for documents the")
        print("  university does not publish.")
        return summary

    print(f"  Found {len(files)} local document(s) in data/docs/:")
    print("  (optional extras — the site's own PDFs are handled in STEP 1)")
    print()

    for filename in files:
        filepath = os.path.join(docs_folder, filename)
        category = _detect_category(filename)
        ext      = filename.lower().split(".")[-1]

        print(f"  Loading: {filename}")
        print(f"           Category: {category}")

        if ext == "pdf":
            result = kb.load_pdf(filepath, category=category)
        elif ext == "docx":
            result = kb.load_docx(filepath, category=category)
        elif ext in ("txt", "md"):
            result = kb.load_text_file(filepath, category=category)
        else:
            result = IngestionResult(filename, "skipped", message="unsupported format")

        # Count by status
        if result.status in ("added", "updated"):
            summary["loaded"] += 1
        elif result.status == "failed":
            summary["failed"] += 1
        elif result.status in ("skipped", "empty"):
            summary["skipped"] += 1

        print()

    return summary


def _snapshot_stores_before_reset() -> str | None:
    """Copy all three knowledge stores aside before --force wipes them.

    WHY THIS EXISTS. --force resets the stores *first* and rebuilds *second*.
    That ordering is fine when the rebuild succeeds and catastrophic when it
    does not: on 2026-08-28 the scrape died of out-of-memory partway through
    and left Chroma at 0 rows, parent_chunks.json at 0 parents, and
    bm25_index.pkl deleted — a working knowledge base destroyed by a command
    that was only supposed to refresh it. Recovery was possible only because
    an unrelated repair script had happened to leave a backup behind.

    A snapshot costs a local file copy (~95 MB, no API calls, a few seconds)
    and makes that failure mode recoverable by definition rather than by luck.
    It is deliberately *not* conditional on free RAM or anything else: the
    whole point is that it runs before every wipe, including the ones nobody
    expects to fail.

    Returns the snapshot directory, or None if there was nothing to save.
    """
    import shutil
    from datetime import datetime

    targets = [
        (config.CHROMA_DB_PATH,        "chroma_db"),
        ("./data/bm25_index.pkl",      "bm25_index.pkl"),
        ("./data/parent_chunks.json",  "parent_chunks.json"),
    ]
    present = [(src, name) for src, name in targets if os.path.exists(src)]
    if not present:
        print("   No existing stores to snapshot (nothing to lose).")
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest  = os.path.join("data", f"_backup_preforce_{stamp}")
    os.makedirs(dest, exist_ok=True)

    print(f"   Snapshotting current stores → {dest}")
    for src, name in present:
        try:
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dest, name))
            else:
                shutil.copy2(src, os.path.join(dest, name))
            print(f"      saved {name}")
        except Exception as e:
            # A failed snapshot must abort the wipe, never proceed silently.
            raise RuntimeError(
                f"Could not snapshot {src} before --force reset: {e}. "
                f"Refusing to wipe the knowledge base without a backup."
            ) from e

    print(f"   Snapshot complete. If the rebuild fails, copy these three "
          f"items back into data/ to restore.")
    return dest


def main():
    parser = argparse.ArgumentParser(
        description="Setup UOLi Chatbot Knowledge Base"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-setup even if KB already has documents"
    )
    # ── CHANGE 1: Added --url flag ────────────────────────────
    parser.add_argument(
        "--url", type=str, default=None,
        help="Scrape and re-index a single URL only (e.g. --url https://uoli.edu.pk/vc-message)"
    )
    # ─────────────────────────────────────────────────────────
    args = parser.parse_args()

    # ── CHANGE 2: Early exit path for single URL ──────────────
    if args.url:
        print()
        print(f"  Single URL mode: {args.url}")
        kb = KnowledgeBase()
        from web_scraper import scrape_page as _scrape_one
        # config.resolve_category, not web_scraper's deleted _detect_category:
        # one decision, made in one place, for every ingest path.
        category = config.resolve_category(args.url)
        if category is None:
            print(f"  ✗ Skipped — {args.url} is excluded as non-content "
                  f"(see config._EXCLUDE_SEGMENTS).")
            print()
            return
        text = _scrape_one(args.url)
        if text:
            kb.load_scraped_page(text, args.url, category)
            kb._rebuild_bm25()
            kb._save_bm25_to_disk()
            # The parent store MUST be saved here too.
            #
            # load_scraped_page writes children to Chroma and parents to the
            # in-memory ParentDocStore. Without this call the parents die with
            # the process, and the children it just wrote are left pointing at
            # parent_ids that exist nowhere — 168 such orphans were produced on
            # 2026-08-28 re-indexing three pages that a rate-limited scrape had
            # missed.
            #
            # Two things then break. _fetch_parents silently stops upgrading
            # those children to their parent, so retrieval quietly loses the
            # surrounding context. Worse, setup_propositions.py builds its
            # entire corpus from the parents on disk and then rmtree's Chroma —
            # so a page re-indexed by --url is *deleted* by the next
            # proposition rebuild, and the log gives no hint that it happened.
            kb.parent_store.save()
            print(f"  ✓ Done — {args.url} indexed successfully as '{category}'.")
        else:
            print(f"  ✗ Failed — could not scrape {args.url}")
        print()
        return
    # ─────────────────────────────────────────────────────────

    # ── Header ────────────────────────────────────────────────
    print()
    print("═" * 62)
    print("   University of Loralai — Chatbot Knowledge Base Setup")
    print("═" * 62)
    print(f"   University : {config.UNIVERSITY_NAME}")
    print(f"   Bot Name   : {config.BOT_NAME}")
    print(f"   Data source: Web scraping + Local documents")
    print(f"   No hardcoded FAQs — all knowledge from real sources")
    print("═" * 62)
    print()

    # ── Initialize KB ─────────────────────────────────────────
    print("Initializing knowledge base...")
    kb = KnowledgeBase()

    if args.force:
        _snapshot_stores_before_reset()
        kb.reset()

    # ── Check existing docs ───────────────────────────────────
    current_count = kb.get_doc_count()
    if current_count > 0 and not args.force:
        print(f"\nKnowledge base already has {current_count:,} document chunks.")
        print()
        print("Options:")
        print("  y = Re-scrape and add more (keeps existing data)")
        print("  n = Exit without changes")
        print("  (Tip: run with --force to skip this prompt)")
        choice = input("\nProceed? (y/n): ").strip().lower()
        if choice != "y":
            print("\nExiting. Knowledge base unchanged.")
            print(f"Run: streamlit run app.py  to start the chatbot.\n")
            return

    print()
    print()

    # ── E2: Clear old parents before fresh rebuild ────────────────────────────
    if args.force:
        kb.parent_store.clear()

    
    # ══════════════════════════════════════════════════════════
    #  STEP 1 — Scrape UOLI Website
    # ══════════════════════════════════════════════════════════
    print("─" * 62)
    print("  STEP 1 — Scraping UOLI Website Pages")
    print("─" * 62)
    t1 = time.time()
    web_summary = step1_scrape_website(kb)
    t1_elapsed  = round(time.time() - t1)
    print()
    print(f"  Web scraping done in {t1_elapsed}s:")
    print(f"    ✓ Pages scraped  : {web_summary['scraped']}")
    print(f"    ✗ Pages failed   : {web_summary['failed']}")
    print(f"    - Pages skipped  : {web_summary['skipped']}")
    # Pass 3's numbers are printed here, next to the pages, because the documents
    # ARE part of the scrape now. Leaving them out of the summary is how a rebuild
    # that silently ingested zero PDFs would still look like a success — and the
    # site's PDFs are where most of the policy text lives.
    print(f"    ✓ PDFs ingested  : {web_summary.get('pdfs', 0)}")
    print(f"    - PDFs unchanged : {web_summary.get('pdfs_skipped', 0)}")
    # Refused is not a failure. It is the purpose gate in pdf_ingest keeping
    # student result gazettes out of the corpus; see pdf_skipped.md for the list
    # and the reason each one was refused.
    print(f"    ⊘ PDFs refused   : {web_summary.get('pdfs_refused', 0)} "
          f"(see pdf_skipped.md)")
    print(f"    ✗ PDFs failed    : {web_summary.get('pdfs_failed', 0)}")
    print(f"    - PDFs unchanged : {web_summary.get('pdfs_skipped', 0)}"
          f"   (byte-hash match — not re-read, not re-embedded)")
    print(f"    ✗ PDFs failed    : {web_summary.get('pdfs_failed', 0)}")

    print()

    # ══════════════════════════════════════════════════════════
    #  STEP 2 — Load Policy Documents from data/docs/
    # ══════════════════════════════════════════════════════════
    print("─" * 62)
    print("  STEP 2 — Optional local documents from data/docs/")
    print("─" * 62)
    t2 = time.time()
    doc_summary = step2_load_documents(kb)
    t2_elapsed  = round(time.time() - t2)
    print(f"  Document loading done in {t2_elapsed}s:")
    print(f"    ✓ Documents loaded : {doc_summary['loaded']}")
    print(f"    ✗ Documents failed : {doc_summary['failed']}")
    print(f"    - Documents skipped: {doc_summary['skipped']}")

    print()

    # ══════════════════════════════════════════════════════════
    #  SUMMARY
    # ══════════════════════════════════════════════════════════
    # Rebuild BM25 once after all documents loaded
    print("Rebuilding BM25 index...")
    kb._rebuild_bm25()
    kb._save_bm25_to_disk()
    print("BM25 ready ✓")
    # ── E2: Save all parent chunks to disk after KB is built ──────────────────
    kb.parent_store.save()
    print(f"Parent store saved ✓  ({len(kb.parent_store)} parents)")
    total_chunks = kb.get_doc_count()
    print("═" * 62)
    print("   SETUP COMPLETE")
    print("═" * 62)
    print(f"   Web pages scraped   : {web_summary['scraped']} / {len(config.UNIVERSITY_PAGES)}")
    print(f"   Site PDFs ingested  : {web_summary.get('pdfs', 0)}"
          f"  (+{web_summary.get('pdfs_skipped', 0)} unchanged)")
    print(f"   Local documents     : {doc_summary['loaded']}  (optional)")
    print(f"   ──────────────────────────────────────────────")
    print(f"   Total KB chunks     : {total_chunks:,}")
    print("═" * 62)
    print()

    # data/docs/ is no longer counted towards "did anything load?" — it is
    # expected to be empty. A rebuild that scraped no pages AND ingested no PDFs
    # is the real failure, and it is almost always the network or a wiped store.
    if (web_summary["scraped"] == 0
            and web_summary.get("pdfs", 0) == 0
            and web_summary.get("pdfs_skipped", 0) == 0
            and doc_summary["loaded"] == 0):
        print("⚠  WARNING: No content was loaded into the knowledge base!")
        print("   Check your internet connection.")
        print("   Do NOT run setup_propositions.py after this — it rebuilds from")
        print("   data/parent_chunks.json and would make the empty state final.")
        print()
    else:
        print("✓  Knowledge base is ready.")
        print()
        print("   Next steps:")
        print("   1. Run:  streamlit run app.py     (Streamlit UI)")
        print("   2. Run:  uvicorn api:app --reload (FastAPI server)")
        print()
        print("   The chatbot will auto-scrape the website every")
        print(f"   {config.SCRAPE_INTERVAL_HOURS} hours to keep knowledge fresh.")
    print()


if __name__ == "__main__":
    main()
    