"""Dry run of the real scraper on a small set of live pages (spec 07).

Reads about 16 pages and 3 PDFs of the live site with the same code the rebuild
uses (web_scraper.scrape_all_pages), but hands the result to a stand-in database
that only remembers what it is given. It makes no database write and no API call,
and it never reads or writes the cache files in data/. Reports go to
reports/dry_run_07/.

It changes no existing file. The knowledge_base module (ChromaDB, OpenAI) is never
imported.
"""
import argparse
import csv
import logging
import json
import os
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

if __package__ in (None, ""):  # run as `python tools/dry_run_scrape.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import page_furniture  # noqa: E402
import pdf_ingest  # noqa: E402
import web_scraper  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "reports" / "dry_run_07"
PAGES_CSV = ROOT / "url_lists" / "pages.csv"
PDFS_CSV = ROOT / "url_lists" / "pdfs.csv"
SAMPLES_CSV = ROOT / "tests" / "fixtures" / "site" / "index.csv"

PDF_COUNT = 3


@dataclass(frozen=True)
class Pick:
    """One slot of the selection: `count` pages whose path passes `match`."""
    name: str
    count: int
    match: callable


def _under(prefix: str):
    return lambda path: path.startswith(prefix)


def _other_campus_life(path: str) -> bool:
    # Sports is the largest campus group and has its own pick.
    return path.startswith("/campus-life/") and not (
        path == "/campus-life/sports" or path.startswith("/campus-life/sports/"))


# The order here is the order of the report. The slots add up to 16 pages.
PICKS = (
    Pick("home", 1, lambda path: path == "/"),
    Pick("program", 3, _under("/academics/programs/")),
    Pick("department", 2, _under("/academics/departments/")),
    Pick("about message", 1,
         lambda path: path.startswith("/about/") and path.endswith("-message")),
    Pick("about vision", 1, lambda path: path == "/about/vision-mission"),
    Pick("office", 2, _under("/offices/")),
    Pick("campus sports", 1, _under("/campus-life/sports/")),
    Pick("campus other", 1, _other_campus_life),
    Pick("research", 1, _under("/research/")),
    Pick("news", 2, _under("/news/")),
    Pick("service", 1, _under("/services/")),
)


def _path_of(address: str) -> str:
    return urlsplit(address).path.rstrip("/") or "/"


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ── 1. selection ────────────────────────────────────────────────────────────

def select_pages(pages_csv: Path = PAGES_CSV,
                 samples_csv: Path = SAMPLES_CSV) -> tuple[list[str], list[str]]:
    """The pages to read and a list of notes about slots that could not be filled
    the preferred way. Addresses are copied from the CSV text, never built here."""
    kept = [r["address"] for r in _read_csv(pages_csv) if r["keep"].strip().lower() == "yes"]
    samples = {_path_of(r["address"]) for r in _read_csv(samples_csv)}
    chosen: list[str] = []
    notes: list[str] = []
    for pick in PICKS:
        # Pages already covered by the 25 saved copies teach us least, so they
        # come last. Sorting makes the choice repeatable.
        pool = sorted(a for a in kept if pick.match(_path_of(a)) and a not in chosen)
        fresh = [a for a in pool if _path_of(a) not in samples]
        old = [a for a in pool if _path_of(a) in samples]
        take = (fresh + old)[:pick.count]
        chosen.extend(take)
        if len(take) < pick.count:
            notes.append(f"{pick.name}: asked for {pick.count}, only {len(take)} exist")
        used_samples = [a for a in take if _path_of(a) in samples]
        if used_samples:
            notes.append(f"{pick.name}: saved sample page used ({', '.join(used_samples)})")
    return chosen, notes


def select_pdfs(pdfs_csv: Path = PDFS_CSV, count: int = PDF_COUNT) -> list[str]:
    rows = [r["address"] for r in _read_csv(pdfs_csv) if r["keep"].strip().lower() == "yes"]
    return rows[:count]


# ── 2. cache redirect and data guard ────────────────────────────────────────

class DataPathError(RuntimeError):
    """A cache path points into data/, which a dry run must never touch."""


def redirect_caches(cache_dir: Path) -> dict[str, str]:
    """Point every cache the scraper reads or writes at `cache_dir`.

    The old lists in data/ were measured on the old WordPress site. If a run of
    fewer than 20 pages fell back on them, they could delete real lines of the
    new pages without a warning.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "furniture": str(cache_dir / "page_furniture.json"),
        "contact_lines": str(cache_dir / "page_contact_lines.json"),
        "site_note": str(cache_dir / "site_note.json"),
        "ocr": str(cache_dir / "ocr_cache.json"),
        "pdf": str(cache_dir / "pdf_cache.json"),
        "pdf_skipped": str(cache_dir / "pdf_skipped.md"),
    }
    web_scraper.FURNITURE_CACHE = paths["furniture"]
    web_scraper.CONTACT_LINES_CACHE = paths["contact_lines"]
    web_scraper.SITE_NOTE_CACHE = paths["site_note"]
    web_scraper._OCR_CACHE_PATH = paths["ocr"]
    web_scraper._OCR_CACHE = None            # loaded again from the new path
    web_scraper._IMAGE_DIGEST_BY_URL.clear()  # image owners belong to the old cache
    pdf_ingest._CACHE_PATH = paths["pdf"]
    pdf_ingest._CACHE = None
    pdf_ingest._SKIP_LOG_PATH = paths["pdf_skipped"]
    return paths


def current_cache_paths() -> dict[str, str]:
    """The paths the code will really use now, read back from the modules."""
    return {
        "furniture": web_scraper.FURNITURE_CACHE,
        "contact_lines": web_scraper.CONTACT_LINES_CACHE,
        "site_note": web_scraper.SITE_NOTE_CACHE,
        "ocr": web_scraper._OCR_CACHE_PATH,
        "pdf": pdf_ingest._CACHE_PATH,
        "pdf_skipped": pdf_ingest._SKIP_LOG_PATH,
    }


def check_no_data_paths(paths: dict[str, str], data_dir: Path = DATA_DIR) -> None:
    """Stop before any fetch if a cache path is inside data/."""
    data_root = Path(data_dir).resolve()
    for name, path in paths.items():
        resolved = Path(path).resolve()
        if resolved == data_root or data_root in resolved.parents:
            raise DataPathError(
                f"Stopped: the {name} cache path points into data/ ({path}). "
                f"A dry run must not touch data/.")


def data_fingerprint(data_dir: Path = DATA_DIR) -> dict[str, tuple[int, int]]:
    """Name, size and modified time of every file in data/ (no file is opened).

    Most of data/ is in .gitignore, so `git status` cannot show a change there.
    """
    found = {}
    for folder, _dirs, files in os.walk(data_dir):
        for name in files:
            full = Path(folder) / name
            stat = full.stat()
            found[str(full.relative_to(data_dir))] = (stat.st_size, stat.st_mtime_ns)
    return found


# ── 3. the stand-in database ────────────────────────────────────────────────

class RecordingKB:
    """Stands in for KnowledgeBase: remembers every page it is given.

    scrape_all_pages only reads `.status` from the answer, so a tiny object is
    enough, and knowledge_base (ChromaDB, OpenAI) is never imported.
    """

    def __init__(self) -> None:
        self.stored: dict[str, dict] = {}

    def load_scraped_page(self, text, url, category, page_hash=None,
                          legacy_hash=None, source_type="web_scrape"):
        self.stored[url] = {"text": text, "category": category,
                            "source_type": source_type}
        return SimpleNamespace(status="added")


# ── 4. watching the fetch and the PDF reader ────────────────────────────────

FETCH_RETRY_WAIT = 10.0   # seconds before the one retry of an unreachable page
SLOW_SECONDS = 30         # a page that took longer than this is marked "slow"
PAGE_DELAY_MIN = 1.0      # spec: never less than 1 second between requests


class FetchWatch:
    """Stands in for web_scraper.fetch_page for the length of one run.

    It calls the real function and keeps what the scraper does not hand back:
    the raw text, the time taken and the reason. fetch_page cannot show the HTTP
    status (the site answers 404 for pages that work), so only "unreachable"
    triggers the wait and the one retry.
    """

    def __init__(self, original, wait: float = FETCH_RETRY_WAIT) -> None:
        self.original = original
        self.wait = wait
        self.records: dict[str, dict] = {}
        self.stopped = False
        self.interrupted = False

    def __call__(self, url: str, session=None):
        if self.stopped:
            self._record(url, "not_fetched", None, None, None)
            return None, "unreachable"
        started = time.monotonic()
        try:
            text, reason = self.original(url, session=session)
            if reason == "unreachable":
                logger.warning("unreachable, waiting %.0f s before one retry: %s", self.wait, url)
                time.sleep(self.wait)
                text, reason = self.original(url, session=session)
                if reason == "unreachable":
                    self.stopped = True   # the site is in trouble; do not hammer it
        except KeyboardInterrupt:
            self.stopped = self.interrupted = True
            raise
        self._record(url, reason, text, started, time.monotonic())
        return text, reason

    def _record(self, url, reason, text, started, ended) -> None:
        seconds = (ended - started) if started is not None else 0.0
        self.records[url] = {"url": url, "reason": reason, "raw": text,
                             "started": started, "ended": ended,
                             "seconds": seconds, "slow": seconds > SLOW_SECONDS}

    def smallest_gap(self) -> float | None:
        """Shortest wait between the end of one page and the start of the next."""
        done = sorted((r for r in self.records.values() if r["started"] is not None),
                      key=lambda r: r["started"])
        gaps = [b["started"] - a["ended"] for a, b in zip(done, done[1:])]
        return min(gaps) if gaps else None


class PdfWatch:
    """Stands in for pdf_ingest.read_pdf: reads only the chosen PDFs.

    scrape_all_pages reads every PDF that the fetched pages link to, and the home
    footer links all 12. Anything not chosen gets None, which keeps the run small
    and gentle on the website. The rows come from this record, not from the
    scraper's `pdfs_failed` count, which would mix "not chosen" with real failures.
    """

    def __init__(self, original, allowed: list[str]) -> None:
        self.original = original
        self.allowed = list(allowed)
        self.records: dict[str, dict] = {}

    def __call__(self, url: str, session=None, use_cache: bool = True):
        if url not in self.allowed:
            return None
        try:
            doc = self.original(url, session=session, use_cache=use_cache)
        except Exception as exc:
            self.records[url] = {"url": url, "result": "failed", "text": "",
                                 "reason": f"{type(exc).__name__}: {exc}"}
            raise
        finally:
            time.sleep(PAGE_DELAY_MIN)
        self.records[url] = self._row(url, doc)
        return doc

    @staticmethod
    def _row(url: str, doc: dict | None) -> dict:
        if doc and doc.get("text", "").strip():
            return {"url": url, "result": "ok", "text": doc["text"], "reason": "",
                    "pages": doc.get("pages")}
        if pdf_ingest.was_refused(url):
            return {"url": url, "result": "refused", "text": "",
                    "reason": "refused: not university information"}
        return {"url": url, "result": "failed", "text": "", "reason": "no text recovered"}

    def read_missing(self) -> None:
        """Read the chosen PDFs that no fetched page linked to."""
        for url in self.allowed:
            if url not in self.records:
                try:
                    self(url)
                except Exception:
                    pass   # already recorded as failed


# ── 5. the run ──────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    pages: list[str]
    pdfs: list[str]
    notes: list[str]
    results: dict
    kb: RecordingKB
    fetch: FetchWatch
    pdf: PdfWatch
    data_before: dict
    data_after: dict
    interrupted: bool
    seconds: float
    ocr: bool

    @property
    def data_unchanged(self) -> bool:
        return self.data_before == self.data_after


def _on_ctrl_c(watch: FetchWatch):
    def handler(_signum, _frame):
        # Stops the queued pages too, so the pool does not fetch the rest while
        # it shuts down.
        watch.stopped = watch.interrupted = True
        raise KeyboardInterrupt
    return handler


def run(limit: int | None = None, selection: tuple | None = None,
        out_dir: Path = OUT_DIR) -> RunResult:
    """Select, guard, scrape with the real code, and return everything seen.

    selection is (pages, notes, pdfs); tests pass it, the command line does not.
    Raises DataPathError before any fetch if a cache path points into data/.
    """
    pages, notes, pdfs = selection or (*select_pages(), select_pdfs())
    if limit is not None:
        pages, pdfs = pages[:limit], []   # a quick first test reads no PDFs

    redirect_caches(Path(out_dir) / "cache")
    check_no_data_paths(current_cache_paths())
    data_before = data_fingerprint()

    kb = RecordingKB()
    fetch = FetchWatch(web_scraper.fetch_page)
    pdf = PdfWatch(pdf_ingest.read_pdf, pdfs)
    original_fetch, original_pdf = web_scraper.fetch_page, pdf_ingest.read_pdf
    in_main = threading.current_thread() is threading.main_thread()
    original_handler = signal.signal(signal.SIGINT, _on_ctrl_c(fetch)) if in_main else None

    results: dict = {}
    interrupted = False
    started = time.monotonic()
    web_scraper.fetch_page, pdf_ingest.read_pdf = fetch, pdf
    try:
        results = web_scraper.scrape_all_pages(kb, urls=pages, include_guaranteed=False)
        if not fetch.stopped:
            pdf.read_missing()
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("stopped with Ctrl+C; the report covers the pages done so far")
    finally:
        # Always put the real functions back, whatever happened.
        web_scraper.fetch_page, pdf_ingest.read_pdf = original_fetch, original_pdf
        if in_main:
            signal.signal(signal.SIGINT, original_handler)

    return RunResult(pages=pages, pdfs=pdfs, notes=notes, results=results, kb=kb,
                     fetch=fetch, pdf=pdf, data_before=data_before,
                     data_after=data_fingerprint(), interrupted=interrupted,
                     seconds=time.monotonic() - started,
                     ocr=web_scraper.OCR_AVAILABLE)


# ── 6. facts about one page ─────────────────────────────────────────────────

_URL = re.compile(r"https?://[^\s)]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SEMESTER = re.compile(r"^semester\s+([ivx]+|\d+)\b", re.IGNORECASE)
_COURSE_ROW = re.compile(r"^\|\s*[A-Z]{2,}[- ]?\d{2,}")
_BARE_HEADING = re.compile(r"^#+$")

# Words that are menu or button text, not content. Whole-line words are matched
# exactly, so "Share" is junk but "Share of seats" is not.
JUNK_PHRASES = ("uoli ai assistant", "developed by", "skip to main content", "home ›")
JUNK_WHOLE_LINE = ("share", "print", "profile", "read article")
JUNK_STARTS = ("back to", "return to")
MIN_TITLE = 3
MAX_TITLE = 150


def count_links(text: str) -> int:
    """Link addresses and email addresses kept in the text."""
    return len(_URL.findall(text)) + len(_EMAIL.findall(text))


def count_people(text: str) -> int:
    """Person lines of a department or faculty page ("Name — Role. Email: ...")."""
    return sum(1 for ln in text.splitlines() if "Email:" in ln)


def has_head_line(text: str) -> bool:
    return "is the Head of the Department" in text


def count_semesters(text: str) -> int:
    labels = set()
    for ln in text.splitlines():
        found = _SEMESTER.match(ln.strip())
        if found:
            labels.add(found.group(1).upper())
    return len(labels)


def count_course_rows(text: str) -> int:
    return sum(1 for ln in text.splitlines() if _COURSE_ROW.match(ln.strip()))


def find_junk(text: str) -> list[str]:
    junk = []
    for ln in text.splitlines():
        line = ln.strip()
        key = page_furniture.normalise(line)
        if (_BARE_HEADING.match(line)
                or any(p in key for p in JUNK_PHRASES)
                or key in JUNK_WHOLE_LINE
                or key.startswith(JUNK_STARTS)):
            junk.append(line)
    return junk


def has_title(text: str) -> bool:
    """The first line looks like a page title: a short line of words.

    The scraper writes the title as plain text on the first line (no "#"), so a
    page that starts with a table row, a bullet or an address has lost it.
    """
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return (MIN_TITLE <= len(first) <= MAX_TITLE
            and not first.startswith(("|", "•", "#", "http")))


def removed_lines(raw: str, final: str) -> list[str]:
    """Lines of the raw text that are not in the stored text."""
    kept = {page_furniture.normalise(ln) for ln in final.splitlines()}
    return [ln for ln in raw.splitlines()
            if ln.strip() and page_furniture.normalise(ln) not in kept]


def would_remove(raw_texts: dict[str, str]) -> dict[str, list[str]]:
    """Report only: lines the cleaner would drop if its 20-page minimum were lifted.

    Nothing is removed. The input is the same raw text pass 2 of the scraper sees.
    """
    furniture = page_furniture.find_furniture(raw_texts, min_pages=1)
    return {url: [ln for ln in text.splitlines()
                  if ln.strip() and page_furniture.normalise(ln) in furniture]
            for url, text in raw_texts.items()}


def repeated_lines(texts: dict[str, str], min_pages: int = 3) -> list[tuple[str, int]]:
    """(line, number of pages) for lines found on at least `min_pages` pages."""
    freq = page_furniture.document_frequency(texts)
    shown: dict[str, str] = {}
    for text in texts.values():
        for ln in text.splitlines():
            shown.setdefault(page_furniture.normalise(ln), ln.strip())
    found = [(shown[key], count) for key, count in freq.items() if count >= min_pages]
    return sorted(found, key=lambda item: (-item[1], item[0]))


# ── 7. one row per page and per PDF ─────────────────────────────────────────

def page_kind(url: str) -> str:
    path = _path_of(url)
    if path.startswith("/academics/programs/"):
        return "program"
    if path.startswith("/academics/departments/"):
        return "department"
    return "other"


def _page_result(url: str, record: dict | None, stored: bool) -> str:
    if record is None:
        return "not_fetched"
    if record["reason"] != "ok":
        return record["reason"]
    return "ok" if stored else "not_stored"   # fetched, then dropped by the cleaner


def build_rows(res: RunResult) -> list[dict]:
    """The facts for every selected page, in selection order."""
    raw = {u: r["raw"] for u, r in res.fetch.records.items() if r["raw"]}
    would = would_remove(raw) if raw else {}
    rows = []
    for url in res.pages:
        record = res.fetch.records.get(url)
        stored = url in res.kb.stored
        if stored:
            text, cleaned = res.kb.stored[url]["text"], True
        else:
            text, cleaned = (record or {}).get("raw") or "", False
        result = _page_result(url, record, stored)
        if result == "not_stored" and res.interrupted:
            result = "not_cleaned"   # the run stopped before the cleaning pass
        kind = page_kind(url)
        rows.append({
            "url": url, "kind": kind, "result": result, "text": text,
            "chars": len(text), "links": count_links(text),
            "people": count_people(text) if kind == "department" or "/faculty" in url else None,
            "head_line": has_head_line(text) if kind == "department" else None,
            "semesters": count_semesters(text) if kind == "program" else None,
            "course_rows": count_course_rows(text) if kind == "program" else None,
            "junk": find_junk(text), "title": has_title(text) if text else False,
            "slow": bool(record and record["slow"]),
            "removed_by_run": len(removed_lines(raw[url], text)) if cleaned and url in raw else 0,
            "would_remove": would.get(url, []),
        })
    return rows


def build_pdf_rows(res: RunResult) -> list[dict]:
    rows = []
    for url in res.pdfs:
        rec = res.pdf.records.get(url)
        if rec is None:
            rec = {"url": url, "result": "not_read", "text": "", "reason": "not read"}
        rows.append({**rec, "chars": len(rec["text"])})
    return rows


# ── 8. the four report sections ─────────────────────────────────────────────

SITE_NOTE_TITLES = ("Official social media pages", "Portals and services",
                    "Contact", "Rules, policies and forms")


def site_note_text(kb: RecordingKB) -> str:
    """The site note (the same text is stored once per category)."""
    import config
    for address in config.SITE_INFO_URLS.values():
        if address in kb.stored:
            return kb.stored[address]["text"]
    return ""


def problems(rows: list[dict], pdf_rows: list[dict]) -> list[str]:
    found = []
    for row in rows:
        if row["result"] != "ok":
            found.append(f"{row['url']}: result is {row['result']}")
            continue
        if row["chars"] == 0:
            found.append(f"{row['url']}: 0 characters")
        if not row["title"]:
            found.append(f"{row['url']}: no title on the first line")
        if row["slow"]:
            found.append(f"{row['url']}: slow (over {SLOW_SECONDS} seconds)")
        found.extend(f"{row['url']}: junk line {line!r}" for line in row["junk"])
    found.extend(f"{r['url']}: PDF {r['result']} ({r['reason']})"
                 for r in pdf_rows if r["result"] != "ok")
    return found


def expectations(rows: list[dict], note: str) -> list[tuple[str, bool]]:
    """Spec 05/06 expectations as (sentence, met)."""
    programs = [r for r in rows if r["kind"] == "program"]
    departments = [r for r in rows if r["kind"] == "department"]
    checks = [(f"Semester tables found on {r['url']}", bool(r["semesters"]))
              for r in programs]
    checks += [(f"Head-of-department line on {r['url']}", bool(r["head_line"]))
               for r in departments]
    checks += [(f"Site note has the section '{title}'", title in note)
               for title in SITE_NOTE_TITLES]
    return checks


# ── 9. output files ─────────────────────────────────────────────────────────

def address_to_filename(url: str) -> str:
    """File name in the style of tests/fixtures/site: "/" becomes "__", home is "home"."""
    path = _path_of(url)
    name = "home" if path == "/" else path.strip("/").replace("/", "__")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]


def _pdf_filename(url: str) -> str:
    stem = Path(urlsplit(url).path).stem or "pdf"
    return re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:120]


def _fresh_folder(folder: Path) -> Path:
    """An empty folder: files of an earlier run (for example --limit 3) are removed."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.txt"):
        old.unlink()
    return folder


def write_texts(res: RunResult, rows: list[dict], pdf_rows: list[dict], out_dir: Path) -> None:
    pages_dir = _fresh_folder(out_dir / "pages")
    for row in rows:
        if not row["text"]:
            continue
        body = row["text"]
        if row["result"] == "not_cleaned":
            body = "[NOT CLEANED: the run was stopped before the cleaning step]\n" + body
        (pages_dir / f"{address_to_filename(row['url'])}.txt").write_text(body, encoding="utf-8")
    pdfs_dir = _fresh_folder(out_dir / "pdfs")
    for row in pdf_rows:
        if row["text"]:
            (pdfs_dir / f"{_pdf_filename(row['url'])}.txt").write_text(row["text"], encoding="utf-8")
    (out_dir / "site_note.txt").write_text(site_note_text(res.kb), encoding="utf-8")
    stored = {u: {"category": v["category"], "source_type": v["source_type"],
                  "chars": len(v["text"])} for u, v in res.kb.stored.items()}
    (out_dir / "results.json").write_text(
        json.dumps({"results": res.results, "stored": stored}, indent=2,
                   ensure_ascii=False, default=str), encoding="utf-8")


# ── 10. report.md ───────────────────────────────────────────────────────────

def _bullets(items) -> list[str]:
    return [f"- {item}" for item in items] or ["- none"]


def _top_section(res: RunResult, limit: int | None) -> list[str]:
    gap = res.fetch.smallest_gap()
    lines = ["# Dry run report (spec 07)", "",
             f"- Run time: {res.seconds:.0f} seconds"
             + (f", limited to the first {limit} pages, no PDFs" if limit else ""),
             f"- OCR: {'on' if res.ocr else 'OFF (EasyOCR not installed; image text is missing)'}",
             f"- data/ unchanged: {'yes' if res.data_unchanged else 'NO - CHECK IMMEDIATELY'}",
             "- Smallest wait between pages: "
             + (f"{gap:.1f} seconds" if gap is not None else "not measured")]
    if res.fetch.stopped:
        lines.append("- STOPPED EARLY: "
                     + ("Ctrl+C." if res.interrupted else "the site did not answer twice in a row."))
    return lines + ["", "## Selection", ""] + _bullets(res.pages) + [""] \
        + _bullets(f"Note: {n}" for n in res.notes) + [""] \
        + _bullets(f"PDF: {u}" for u in res.pdfs)


def _page_table(rows: list[dict], pdf_rows: list[dict]) -> list[str]:
    def cell(value):
        return "-" if value is None else str(value)
    lines = ["", "## Pages", "",
             "| Page | Result | Chars | Links | People | Semesters | Course rows | Junk | Cleaner removed |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['url']} | {r['result']} | {r['chars']} | {r['links']} | "
                     f"{cell(r['people'])} | {cell(r['semesters'])} | {cell(r['course_rows'])} | "
                     f"{len(r['junk'])} | {r['removed_by_run']} |")
    lines += ["", "## PDFs", "", "| PDF | Result | Chars | Note |", "|---|---|---|---|"]
    lines += [f"| {r['url']} | {r['result']} | {r['chars']} | {r['reason']} |" for r in pdf_rows]
    return lines


def _repeat_section(rows: list[dict]) -> list[str]:
    texts = {r["url"]: r["text"] for r in rows if r["text"]}
    lines = ["", "## 3. Lines that repeat on 3 or more pages", "",
             "A human decides for each line: real content or junk.", ""]
    lines += _bullets(f"{count} pages: {line}" for line, count in repeated_lines(texts))
    removal: dict[str, int] = {}
    for r in rows:
        for line in set(r["would_remove"]):
            removal[line] = removal.get(line, 0) + 1
    lines += ["", "Cleaner would remove (minimum lifted, nothing was removed):", ""]
    lines += _bullets(f"{n} pages: {line}" for line, n in
                      sorted(removal.items(), key=lambda kv: (-kv[1], kv[0])))
    return lines


def build_report(res: RunResult, limit: int | None = None) -> str:
    rows, pdf_rows = build_rows(res), build_pdf_rows(res)
    note = site_note_text(res.kb)
    lines = _top_section(res, limit) + _page_table(rows, pdf_rows)
    lines += ["", "## 1. Problems", ""] + _bullets(problems(rows, pdf_rows))
    lines += ["", "## 2. New links", ""] + _bullets(res.results.get("new_links", []))
    lines += _repeat_section(rows)
    lines += ["", "## 4. Comparison with spec 05/06 expectations", ""]
    lines += _bullets(f"{'OK' if met else 'MISSING'}: {sentence}"
                      for sentence, met in expectations(rows, note))
    return "\n".join(lines) + "\n"


def write_outputs(res: RunResult, out_dir: Path = OUT_DIR, limit: int | None = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_texts(res, build_rows(res), build_pdf_rows(res), out_dir)
    report = out_dir / "report.md"
    report.write_text(build_report(res, limit), encoding="utf-8")
    return report


# ── 11. command line ────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=None,
                        help="read only the first N selected pages and no PDFs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not web_scraper.PLAYWRIGHT_AVAILABLE:
        logger.error("Playwright is not installed; the dry run cannot start.")
        return 1
    try:
        res = run(limit=args.limit)
    except DataPathError as exc:
        logger.error("%s", exc)
        return 1
    report = write_outputs(res, OUT_DIR, args.limit)
    print(f"report written to {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
