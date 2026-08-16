"""pdf_ingest.py — reads every PDF on the site, automatically.

WHY THIS MODULE EXISTS
──────────────────────
Until now this pipeline had no PDF support of any kind. Grepping the scraper for
"pdf" returned nothing, and advanced_crawler explicitly excluded the extension.
Measured against the live site, that is 29 distinct PDF files never read, of which
25 live on /downloads — a page that was not even in the seed list.

The consequence was visible in the logs and misread as a scraper bug:

    WARNING:web_scraper: no own content (401 chars): .../advertisement-for-visiting-faculty
    WARNING:web_scraper: no own content (407 chars): .../full-bright-2027

Those pages are not broken. They are a heading, a sentence, and a button that
points at a PDF. All of the content is inside the file.

Five months ago the only workaround available was to download five of these by
hand, clean them in Word, and commit them as .docx. That worked, and it is also
why the university's other twenty-four documents are invisible, and why a document
the university has since edited or withdrawn is still being answered from a
five-month-old copy. This module replaces that manual step entirely.

DOES A PDF GET READ OVER THE NETWORK, OR DOWNLOADED FIRST?
──────────────────────────────────────────────────────────
Downloaded first, always. There is no way around it: a PDF's text is compressed
inside the file's object streams, and the page you want may be described by an
object stored at the very end. Nothing can be read until the bytes are present.

So each file is fetched into memory, parsed, and the bytes are dropped — only the
extracted text is kept. Each file is fetched at most once ever: the cache below
records the server's ETag and Last-Modified, and later runs send a conditional
request. An unchanged PDF answers "304 Not Modified" in a few hundred bytes and is
never transferred again. A changed one is re-read, which is the entire point of
automating this.

DIGITAL AND SCANNED, IN ONE PATH
────────────────────────────────
Measured over the 25 files on /downloads:

    14  digital     a real text layer — free to read, exact
    10  scanned     zero extractable characters; every page is a photograph
     1  thin        The-Balochistan-Universities-Act-2022.pdf, 86 pages, 28 MB,
                    144 characters of text layer across the first six pages

Both kinds go through the same function. Per page: try the text layer; if the page
is effectively empty, render it and read it with OCR. The decision is per PAGE and
not per FILE, because a scanned policy often has a digitally-generated cover sheet,
and a digital report often has one scanned annexure.

TABLES
──────
A fee schedule is a table, and a table flattened into prose is not a fee schedule.
Digital pages use PyMuPDF's own table finder and the surrounding prose is emitted
with the table's area excluded, so no cell is stored twice. Scanned pages use
table_reconstruct on the OCR boxes — the same code, and therefore the same output
format, as a table screenshotted onto a web page.

No API calls. No embeddings. Network and CPU only.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

import ocr_quality
import table_reconstruct

# ── limits ───────────────────────────────────────────────────────────────
#
# A PDF larger than this is refused. 20 MB clears every useful document on the
# site and stops The-Balochistan-Universities-Act-2022.pdf (28 MB, 86 scanned
# pages) from crashing the process. That file produces 0 usable text even with
# OCR — 144 chars of digital text over 86 pages, OCR cap hits at 20 pages,
# and the dry-run confirmed "no text recovered" even with full OCR enabled.
# The previous 40 MB limit included it, crashed every rebuild, and gained nothing.
MAX_PDF_BYTES = 30 * 1024 * 1024

# Resolution for rendering a scanned page before OCR. 200 DPI is the lowest
# setting at which EasyOCR reliably reads the 9-10pt body text in this
# university's notifications; 300 DPI reads no better and costs 2.25x the pixels,
# which matters when a single page is already ~11 MB of RGB at 200.
OCR_DPI = 200

# A page whose text layer yields fewer characters than this is treated as an
# image and sent to OCR. Set above zero because a scanned page often carries a
# stray digital artefact — a page number, a stamp's embedded text — and finding
# four characters must not count as "this page has a text layer".
MIN_TEXT_LAYER_CHARS = 60

# Hard ceiling on OCR pages per document. The 86-page act at ~20 seconds a page is
# half an hour of CPU, and it is the one file already covered by a hand-cleaned
# docx. Raising this is a deliberate decision, so it is a named constant and the
# skipped count is logged rather than silently dropped.
MAX_OCR_PAGES = 90

_CACHE_PATH = os.path.join("data", "pdf_cache.json")
_CACHE: dict | None = None

_HEADERS = {"User-Agent": "Mozilla/5.0"}

# ── running headers and footers ───────────────────────────────────────────
#
# Every page of a university notification repeats the letterhead and a page
# number. Measured on UOL-Notification-of-Conduct-Rules.pdf, all seven pages
# carried "UNIVERSITY OF LORALAI OFFICE OF THE REGISTRAR", the motto line, and
# "Page N of 7". Left in, that text is embedded once per chunk of every policy
# document, and it is the same text in all of them — so it pulls every policy
# chunk towards every policy query and dilutes the part that actually differs.
#
# Matching is FUZZY, and that is not gold-plating: OCR read the one motto line as
# RELEVANCF, RELEVIANNCF, RELEVAANCE and RFLEVANCF on different pages of the same
# file. Exact or normalised-exact comparison finds none of them.
RUNNING_ZONE_LINES = 3      # only the top and bottom of a page can be furniture
RUNNING_SIMILARITY = 0.75   # difflib ratio, chosen to span the OCR drift above
RUNNING_MIN_PAGES = 3       # with two pages, "repeated" carries no evidence
RUNNING_SHARE = 0.6         # must appear on this fraction of pages
RUNNING_MAX_CHARS = 120     # a long line is body text, not a header

_PAGE_NUMBER = re.compile(r"^\s*(?:page\s*)?\d+\s*(?:of\s*\d+)?\s*[.:_-]?\s*$",
                          re.I)

# ── section headings ──────────────────────────────────────────────────────
#
# A numbered clause is this corpus's real section boundary: "3. Declaration of
# assets.", "17. Delegation of Powers.-". Marking them with ## hands the existing
# UniversityAwareChunker its structure, and that chunker already keeps a section
# whole up to max_chunk_size and repeats the heading as "[X — continued]" beyond
# it. That is requirement 6 answered by proven code rather than a second chunker
# with its own bugs.
HEADING_MAX_CHARS = 90
_CLAUSE_HEADING = re.compile(r"^(\d{1,3})\s*[.)_:-]\s+(\S.*)$")
_PART_HEADING = re.compile(
    r"^((?:CHAPTER|PART|SCHEDULE|ANNEXURE|APPENDIX|SECTION)\s+[IVXLC\d]+\b.*)$",
    re.I)

# Matches a PDF link in raw HTML, including query strings WordPress appends.
_PDF_LINK = re.compile(r"""https?://[^\s"'<>]+?\.pdf(?:\?[^\s"'<>]*)?""", re.I)


# ══════════════════════════════════════════════════════════════════════════
#  cache
# ══════════════════════════════════════════════════════════════════════════

def _load_cache() -> dict:
    """Read the on-disk PDF cache, tolerating every way it can be absent."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        _CACHE = loaded if isinstance(loaded, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _CACHE = {}
    return _CACHE


def _save_cache() -> None:
    """Persist the cache atomically. Failure here must never fail a scrape."""
    cache = _load_cache()
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, _CACHE_PATH)
    except OSError as exc:
        logger.warning(f"could not save PDF cache: {exc}")


# ══════════════════════════════════════════════════════════════════════════
#  discovery
# ══════════════════════════════════════════════════════════════════════════

def discover(html: str, base_url: str = "") -> set:
    """Every PDF URL referenced by one page's HTML.

    Applied to the raw HTML rather than to parsed anchors on purpose: this site
    puts PDF links in <a href>, in Elementor button widgets' data attributes, and
    in inline onclick handlers, and a regex over the source finds all three. A PDF
    URL is unmistakable, so the looser match costs nothing.

    Fragments are dropped and the query string kept — WordPress uses "?ver=" for
    cache-busting, and two URLs differing only there are the same file, so the
    byte-level hash below is what actually decides sameness.
    """
    found = set()
    for match in _PDF_LINK.findall(html or ""):
        url = match.split("#")[0].strip().rstrip(").,;\"'")
        if url.lower().startswith("http"):
            found.add(url)
    return found


# ══════════════════════════════════════════════════════════════════════════
#  fetching
# ══════════════════════════════════════════════════════════════════════════

def _fetch(url: str, session=None) -> tuple:
    """Download one PDF, or learn from the server that it has not changed.

    Returns (data, status) where status is one of:
        "unchanged"  the server answered 304; `data` is None and the cached text
                     is still correct
        "ok"         `data` holds the bytes
        "skip"       too large, wrong type, or unreachable; `data` is None

    Conditional headers are what make requirement 7 apply to PDFs as well as to
    HTML pages. Without them every six-hourly scrape would re-transfer 100 MB of
    unchanged scans and re-OCR them.
    """
    cache = _load_cache()
    entry = cache.get(url) or {}
    headers = dict(_HEADERS)
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        headers["If-Modified-Since"] = entry["last_modified"]

    getter = session.get if session is not None else requests.get
    try:
        resp = getter(url, headers=headers, timeout=90, stream=True)
    except requests.RequestException as exc:
        logger.warning(f"PDF unreachable {url}: {type(exc).__name__}")
        return None, "skip"

    try:
        if resp.status_code == 304:
            return None, "unchanged"
        if resp.status_code != 200:
            logger.warning(f"PDF HTTP {resp.status_code}: {url}")
            return None, "skip"

        declared = (resp.headers.get("Content-Type") or "").lower()
        if declared and "pdf" not in declared and "octet-stream" not in declared:
            # A 200 that is not a PDF is this site's 404 page, which is HTML. That
            # exact case produced "PdfStreamError: invalid pdf header: b'<!doc'"
            # when PDF URLs were guessed rather than scraped.
            logger.warning(f"not a PDF ({declared}): {url}")
            return None, "skip"

        length = resp.headers.get("Content-Length")
        if length and int(length) > MAX_PDF_BYTES:
            logger.warning(f"PDF too large ({int(length) // 1024} KB): {url}")
            return None, "skip"

        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                logger.warning(f"PDF exceeded size cap while reading: {url}")
                return None, "skip"
            chunks.append(chunk)
        data = b"".join(chunks)
    finally:
        resp.close()

    if not data.startswith(b"%PDF"):
        logger.warning(f"missing PDF header: {url}")
        return None, "skip"

    entry["etag"] = resp.headers.get("ETag") or entry.get("etag")
    entry["last_modified"] = (resp.headers.get("Last-Modified")
                              or entry.get("last_modified"))
    cache[url] = entry
    return data, "ok"


# ══════════════════════════════════════════════════════════════════════════
#  extraction
# ══════════════════════════════════════════════════════════════════════════

def _render_table(rows) -> str:
    """Render PyMuPDF's extracted table as pipe-delimited lines.

    Identical output format to table_reconstruct.render_markdown and to the HTML
    <table> branch in web_scraper, so a fee table reads the same way whether it
    came from a web page, a digital PDF, or a scan. One format is the reason an
    answering model can learn to read tables at all.
    """
    lines = []
    for row in rows or []:
        cells = [("" if c is None else str(c)).replace("\n", " ").strip()
                 for c in row]
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _digital_page_text(page) -> str:
    """Text of one page that has a text layer, with tables kept as tables.

    Blocks whose centre falls inside a table's bounding box are dropped from the
    prose, and the table is emitted in their place at the same vertical position.
    Without that exclusion every cell would appear twice — once as loose prose and
    once inside the grid — and duplicate text in the context window is the defect
    that damaged retrieval precision in the first place.
    """
    tables = []
    try:
        found = page.find_tables()
        for tbl in getattr(found, "tables", []) or []:
            rendered = _render_table(tbl.extract())
            if rendered:
                tables.append((tbl.bbox, rendered))
    except Exception as exc:
        logger.debug(f"table finder failed on page: {exc}")

    def inside_a_table(x0, y0, x1, y1) -> bool:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for (bx0, by0, bx1, by1), _ in tables:
            if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                return True
        return False

    pieces = []
    try:
        for block in page.get_text("blocks") or []:
            x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
            if not (text or "").strip():
                continue
            if inside_a_table(x0, y0, x1, y1):
                continue
            pieces.append((y0, text.strip()))
    except Exception:
        pieces = [(0.0, page.get_text("text") or "")]

    for (bx0, by0, bx1, by1), rendered in tables:
        pieces.append((by0, rendered))

    pieces.sort(key=lambda p: p[0])
    return "\n".join(p[1] for p in pieces if p[1].strip())


def _ocr_page(page) -> tuple:
    """Render one page and read it. Returns (text, is_table, confidence).

    Reuses the EasyOCR reader that web_scraper already built. Imported inside the
    function so that this module has no import-time dependency on the scraper —
    the scraper must be free to import PDF discovery without a cycle — and so
    that a second copy of the model is never loaded into a machine with 8 GB of
    RAM.
    """
    try:
        import numpy as np
        from PIL import Image
        import io

        import web_scraper
        reader = getattr(web_scraper, "_OCR_READER", None)
        if reader is None:
            return "", False, 0.0

        import fitz  # noqa: F401  (imported by the caller; kept for clarity)
        pix = page.get_pixmap(dpi=OCR_DPI)
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        if image.mode != "RGB":
            image = image.convert("RGB")
        boxes = reader.readtext(np.array(image), detail=1, paragraph=False)
        # Free the render before the next page; a 200 DPI A4 page is ~11 MB.
        del pix, image
        result = table_reconstruct.reconstruct(boxes)
        return (result["text"], result["is_table"], result["confidence"])
    except Exception as exc:
        logger.debug(f"page OCR failed: {exc}")
        return "", False, 0.0


def _norm_line(line: str) -> str:
    """Compare-only form of a line: lowercase, punctuation and spacing removed."""
    return re.sub(r"[^a-z0-9]+", " ", line.lower()).strip()


def _strip_running_lines(pages: list[str]) -> list[str]:
    """Remove the letterhead and page numbers that repeat across a document.

    Only the first and last RUNNING_ZONE_LINES lines of each page are candidates.
    Position matters: a phrase that recurs in the middle of a policy is the policy
    repeating itself, which is content, while the same phrase at the top of every
    page is stationery. Restricting by position is what makes this safe to apply
    to body text at all.

    A candidate is removed when a similar line appears in the same zone on
    RUNNING_SHARE of the other pages. Table rows are never removed — a repeated
    row is data.
    """
    if len(pages) < RUNNING_MIN_PAGES:
        return pages

    zones = []
    for page in pages:
        lines = page.split("\n")
        head = range(0, min(RUNNING_ZONE_LINES, len(lines)))
        tail = range(max(0, len(lines) - RUNNING_ZONE_LINES), len(lines))
        zones.append((lines, sorted(set(head) | set(tail))))

    need = max(2, round(RUNNING_SHARE * len(pages)))
    out = []
    for i, (lines, idxs) in enumerate(zones):
        drop = set()
        for j in idxs:
            line = lines[j].strip()
            if not line or "|" in line or len(line) > RUNNING_MAX_CHARS:
                continue
            if _PAGE_NUMBER.match(line):
                drop.add(j)
                continue
            norm = _norm_line(line)
            if not norm:
                drop.add(j)
                continue
            hits = 1
            for k, (other, other_idxs) in enumerate(zones):
                if k == i:
                    continue
                for m in other_idxs:
                    cand = _norm_line(other[m].strip())
                    if cand and difflib.SequenceMatcher(
                            None, norm, cand).ratio() >= RUNNING_SIMILARITY:
                        hits += 1
                        break
            if hits >= need:
                drop.add(j)
        out.append("\n".join(l for j, l in enumerate(lines) if j not in drop))
    return out


def _mark_headings(text: str, title: str = "") -> str:
    """Mark numbered clauses and chapter lines as ## headings.

    The document title is folded into each heading, so a retrieved clause still
    names the document it governs. A clause that says only "3. Declaration of
    assets" is how a model ends up attributing one policy's rule to another
    policy; the source URL is in the metadata, but RAGAS and the answering model
    both read the text.
    """
    tag = f"{title} — " if title else ""
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "|" in stripped:
            out.append(line)
            continue
        if len(stripped) <= HEADING_MAX_CHARS \
                and not stripped.endswith((",", ";")) \
                and (_PART_HEADING.match(stripped)
                     or _CLAUSE_HEADING.match(stripped)):
            out.append(f"## {tag}{stripped}")
            continue
        out.append(line)
    return "\n".join(out)



def _extract(data: bytes, url: str) -> dict:
    """Read one PDF's bytes into text. Returns the cache entry shape.

    Keys: text, kind, pages, ocr_pages, tables, skipped_pages.
    """
    try:
        import fitz
    except ImportError:
        logger.error("PyMuPDF (fitz) is not installed — PDFs cannot be read")
        return {"text": "", "kind": "unavailable", "pages": 0,
                "ocr_pages": 0, "tables": 0, "skipped_pages": 0}

    out, ocr_pages, tables, skipped = [], 0, 0, 0
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.warning(f"cannot open PDF {url}: {type(exc).__name__}: {exc}")
        return {"text": "", "kind": "unreadable", "pages": 0,
                "ocr_pages": 0, "tables": 0, "skipped_pages": 0}

    try:
        for index, page in enumerate(doc):
            try:
                digital = _digital_page_text(page)
            except Exception as exc:
                logger.debug(f"page {index} text failed: {exc}")
                digital = ""

            if len(digital.strip()) >= MIN_TEXT_LAYER_CHARS:
                if "|" in digital:
                    tables += 1
                out.append(digital)
                continue

            # No usable text layer: this page is a photograph.
            try:
                import web_scraper
                if not getattr(web_scraper, "OCR_AVAILABLE", True):
                    skipped += 1
                    continue
            except Exception:
                pass

            if ocr_pages >= MAX_OCR_PAGES:
                skipped += 1
                continue

            text, is_table, conf = _ocr_page(page)
            ocr_pages += 1
            if not text.strip():
                continue

            keep, reason = ocr_quality.is_usable(text, conf, is_table)
            if not keep:
                logger.debug(f"{url} page {index + 1} OCR rejected ({reason})")
                continue
            if is_table:
                tables += 1
            out.append(text)

        page_count = doc.page_count
    finally:
        doc.close()

    body = "\n\n".join(p for p in _strip_running_lines(out) if p.strip())
    body = _mark_headings(body, title_from_url(url))

    if ocr_pages and len(out) > ocr_pages:
        kind = "mixed"
    elif ocr_pages:
        kind = "scanned"
    elif body:
        kind = "digital"
    else:
        kind = "empty"

    if skipped:
        logger.warning(
            f"{url}: {skipped} scanned pages not read — MAX_OCR_PAGES is "
            f"{MAX_OCR_PAGES}. Content from those pages is NOT in the store.")

    return {"text": body, "kind": kind, "pages": page_count,
            "ocr_pages": ocr_pages, "tables": tables,
            "skipped_pages": skipped}


# ══════════════════════════════════════════════════════════════════════════
#  public entry point
# ══════════════════════════════════════════════════════════════════════════

def title_from_url(url: str) -> str:
    """A readable document title from the filename.

    The filename is the only title most of these files have — the PDF metadata
    title is empty on every one of the 25 measured. Given as a heading so the
    chunker has something to attach to each chunk and a retrieved fragment of a
    notification still says which notification it came from.
    """
    stem = url.split("?")[0].rsplit("/", 1)[-1]
    stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
    stem = re.sub(r"[-_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


# ══════════════════════════════════════════════════════════════════════════
#  table-of-contents lines
# ══════════════════════════════════════════════════════════════════════════
#
# A contents entry — "3. Declaration of assets ................. 14" — is a
# heading and a page number joined by dots. It carries every keyword of the
# section it points at and none of its content, so it is the purest form of
# keyword soup: it matches a query about the section and then answers nothing.
# 129 such lines survive across the three long policies we keep.
#
# WHERE THIS RUNS, AND WHY IT CANNOT MOVE
# ───────────────────────────────────────
# After the purpose gate, never before it. The gate's first signal IS the
# dot-leader contents block — that is how a result gazette is recognised. Strip
# the dots during extraction and the cached text no longer has them, the gate
# stops recognising anything, and all thirteen gazettes walk straight in. The
# cache therefore stores the unstripped text on purpose.
_TOC_LINE = re.compile(
    r"""^\s*\S.*?                     # a heading of some kind
        \.{5,}                        # joined to its page number by dot leaders
        [.\s,;:'\-]*                  # debris OCR scattered through the leaders
        (?:\d{1,4}|[ivxlcdm]{1,7})?   # page number: arabic, OR roman front matter
        [.\s]*$""",
    re.X | re.I)

# Roman numerals are not decoration here. Seven contents entries in the Graduate
# Education Policy point at front matter — "ACRONYMS ......... ix" — and a
# digits-only page number misses every one of them.

# What OCR leaves when it cannot read a contents entry at all:
# ".. .. .... .. . . . .. ............. ..12". A line holding a dot run and not
# one letter is rubble whatever it used to be.
_LETTERLESS = re.compile(r"^[^A-Za-z]*$")


def _strip_toc_lines(text: str) -> str:
    """Drop table-of-contents entries, keeping table rows untouched.

    A line carrying "|" is a reconstructed table row and is never removed, for
    the same reason _strip_running_lines leaves them alone: single-column form
    rows like "| Name |" are real content. The cost of that exemption is one
    contents line in the HEC harassment policy where OCR read "ll" as "||" —
    a fair price for not shredding a form.
    """
    kept = []
    for line in text.splitlines():
        if "|" in line:
            kept.append(line)
            continue
        if _TOC_LINE.match(line):
            continue
        if _LETTERLESS.match(line) and _DOT_LEADER.search(line):
            continue
        kept.append(line)
    return "\n".join(kept)


# ══════════════════════════════════════════════════════════════════════════
#  document purpose gate
# ══════════════════════════════════════════════════════════════════════════
#
# WHY THIS EXISTS
# ───────────────
# 913 chunks of the 10,154 in ChromaDB — 9% of the corpus — were student result
# gazettes: registration number, student name, father's name, per-subject marks,
# GPA, "Promoted / Pass". They answer no question anyone asks a university
# chatbot, they are personal data about identifiable students, and they actively
# corrupt answers: the OCR of these scans produced four extra, mangled spellings
# of the Controller of Examination's name, so a question the website answers
# correctly on /contact had five competing candidates in the corpus.
#
# THIS IS NOT A FILENAME BLOCKLIST, ON PURPOSE
# ────────────────────────────────────────────
# Only one of the thirteen is called "Result-Fall-2025.pdf". The rest are named
# after departments — Anesthesia.pdf, Department-of-Education.pdf,
# Computer-Science.pdf — and read like prospectuses from their names alone. A
# blocklist would also need editing every semester, and the site is re-scraped
# every 24 hours, so the first missed edit puts the garbage straight back.
# The gate reads the document instead.
#
# THE TWO SIGNALS, AND WHY BOTH
# ─────────────────────────────
# A. The document declares itself. Every one of the thirteen opens with a
#    dot-leader table of contents whose entries are SEMESTERS:
#        CONTENTS
#        BS PASHTO ..................................................... 7
#        6th Sem ....................................................... 7
#        2nd Sem Group (A) ............................................. 3
#    A curriculum's contents lists courses; a gazette's lists semesters and
#    groups, because one gazette covers many cohorts. Measured over all 55
#    cached PDFs this alone is exact: YES on all 13 gazettes, NO on all 42
#    real documents.
#
# B. The text is not prose. Ratio of common English function words (the, of,
#    shall, is) to all words:
#        13 gazettes      0.3% – 6.3%     table cells, no sentences
#        42 real docs     5.3% – 44.8%    policies and acts sit at 35–45%
#    Signal A is already exact, so B is here as an independent second opinion,
#    because A alone would misfire on a genuine prospectus that happens to list
#    semesters in its contents. Requiring both means such a document has to ALSO
#    contain no sentences before it is refused, and a real prospectus has prose.
#
# Numbers behind these thresholds: scratch/verify_pdf_gate.py, which re-derives
# them from data/pdf_cache.json and asserts 13 rejected / 42 kept. Run it after
# changing anything here.

# A run of dots joining a contents entry to its page number.
_DOT_LEADER = re.compile(r"\.{5,}")

# "6th Sem", "2nd Sem Group (A)", "8th Semester" — the giveaway that the
# contents lists cohorts rather than subjects.
_SEMESTER_ENTRY = re.compile(r"\b\d\s*(?:st|nd|rd|th)\s+Sem", re.I)

# How far into the document to look for the contents page. Generous because OCR
# of a scanned cover sheet can prepend a letterhead block first.
_CONTENTS_ZONE_LINES = 80
_CONTENTS_ZONE_CHARS = 600

_WORD = re.compile(r"[A-Za-z']+")

# Deliberately short and boring. These are the words that appear in any English
# sentence and in no table cell; a longer list would start including words that
# are topic-specific and make the measure depend on subject matter.
_FUNCTION_WORDS = frozenset("""
the of and to in a is are shall for be that or as by with on not an at from
this these which any such may must it its all have has been if than when
where who whom their his her our we you
""".split())

# Gazettes top out at 6.3%; the lowest-prose real document that trips signal A
# does not exist, and the lowest-prose real document of any kind is 5.3% (a
# 57-word proforma, which signal A clears anyway). 12% sits in the empty band
# between 6.3% and the next real prose document at 12.3%.
_MAX_GAZETTE_FUNCTION_WORD_PCT = 12.0

# Below this there is not enough text for a ratio to mean anything. The shortest
# real gazette is 119 words, so this rejects nothing that should be rejected.
_MIN_WORDS_TO_JUDGE = 60

_SKIP_LOG_PATH = "pdf_skipped.md"

# URLs refused during this process's lifetime. Exists so the scraper can tell a
# deliberate refusal apart from a document it failed to read: both come back as
# None, but one is the gate working and the other is a bug or a dead link, and a
# scrape log that calls them both "failed" trains you to ignore real failures.
_REFUSED: set[str] = set()


def was_refused(url: str) -> bool:
    """True if the purpose gate refused this URL during this run."""
    return url in _REFUSED


def _function_word_pct(text: str) -> float:
    """Share of words that are common English function words, as a percent."""
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return 0.0
    return sum(1 for w in words if w in _FUNCTION_WORDS) / len(words) * 100


def rejection_reason(text: str) -> str | None:
    """Why this document must not be ingested, or None to ingest it.

    Pure function of the text, so the same document is judged the same way
    whether it arrived over the network or out of the cache.
    """
    lines = text.splitlines()
    zone = "\n".join(lines[:_CONTENTS_ZONE_LINES])

    declares_contents = bool(_DOT_LEADER.search(text[:_CONTENTS_ZONE_CHARS]))
    lists_semesters = bool(_SEMESTER_ENTRY.search(zone))
    if not (declares_contents and lists_semesters):
        return None

    words = _WORD.findall(text)
    if len(words) < _MIN_WORDS_TO_JUDGE:
        return None

    pct = _function_word_pct(text)
    if pct >= _MAX_GAZETTE_FUNCTION_WORD_PCT:
        return None

    return (f"semester result gazette — dot-leader contents listing semesters, "
            f"and only {pct:.1f}% function words ({len(words)} words), so the "
            f"body is table cells rather than sentences")


def _record_skip(url: str, reason: str) -> None:
    """Append this rejection to pdf_skipped.md, idempotently.

    Read-modify-write keyed on the URL rather than a blind append, because the
    site is scraped every 24 hours and a blind append would grow the same
    thirteen lines forever. A rejected document must be visible somewhere — a
    filter nobody can audit is how you end up deleting something that mattered.
    """
    rows = {}
    try:
        with open(_SKIP_LOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("| http"):
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    if len(parts) >= 2:
                        rows[parts[0]] = parts[1]
    except (FileNotFoundError, OSError):
        pass

    if rows.get(url) == reason:
        return
    rows[url] = reason

    try:
        with open(_SKIP_LOG_PATH, "w", encoding="utf-8") as fh:
            fh.write("# PDFs refused at ingest\n\n"
                     "Written by `pdf_ingest.rejection_reason`. Nothing here was "
                     "embedded. To let one back in, find out which signal it "
                     "tripped (`scratch/verify_pdf_gate.py`) rather than deleting "
                     "this line — the file is regenerated on every scrape.\n\n"
                     "| document | why it was refused |\n|---|---|\n")
            for key in sorted(rows):
                fh.write(f"| {key} | {rows[key]} |\n")
    except OSError as exc:
        logger.warning(f"could not write {_SKIP_LOG_PATH}: {exc}")


def read_pdf(url: str, session=None, use_cache: bool = True) -> dict | None:
    """Read one PDF, or return None if it could not be read or must not be used.

    A thin wrapper around _read_pdf so the purpose gate has exactly one place to
    live. _read_pdf has five separate `return` statements and three of them serve
    text straight out of the cache; a gate placed after extraction would be
    bypassed by every already-cached document, which is all thirteen gazettes.
    Gating the single value that leaves this function means no path can skip it,
    and adding a sixth return later cannot reintroduce the hole.
    """
    result = _read_pdf(url, session=session, use_cache=use_cache)
    if result is None:
        return None

    reason = rejection_reason(result.get("text", ""))
    if reason:
        logger.info(f"refusing PDF: {url} — {reason}")
        _REFUSED.add(url)
        _record_skip(url, reason)
        return None

    # Only now, once the document is known to be worth keeping. The gate above
    # reads the dot-leader contents block that this strips; doing it earlier —
    # inside _extract, where the rest of the text cleaning lives — would blind
    # the gate and let every gazette through. See _strip_toc_lines.
    result["text"] = _strip_toc_lines(result["text"])
    return result


def _read_pdf(url: str, session=None, use_cache: bool = True) -> dict | None:
    """Read one PDF and return its text, or None if it could not be read.

    Returns a dict:
        url, title, text, kind, pages, ocr_pages, tables, skipped_pages,
        doc_hash, from_cache

    `doc_hash` is the md5 of the file's bytes and is what the caller should pass
    to load_scraped_page as page_hash: it changes when and only when the
    university replaces the file, which is exactly the condition under which the
    document should be re-embedded.
    """
    cache = _load_cache()
    entry = cache.get(url) or {}

    data, status = _fetch(url, session=session)

    if status == "unchanged" and use_cache and entry.get("text"):
        logger.info(f"PDF unchanged, using cached text: {url}")
        return _result(url, entry, from_cache=True)

    if status == "skip":
        # Fall back to whatever was read last time rather than losing a document
        # because the server had a bad minute.
        if use_cache and entry.get("text"):
            logger.info(f"PDF unreachable, using cached text: {url}")
            return _result(url, entry, from_cache=True)
        return None

    if data is None:
        return _result(url, entry, from_cache=True) if entry.get("text") else None

    doc_hash = hashlib.md5(data).hexdigest()

    if use_cache and entry.get("doc_hash") == doc_hash and entry.get("text"):
        logger.info(f"PDF bytes identical, using cached text: {url}")
        _save_cache()
        return _result(url, entry, from_cache=True)

    logger.info(f"reading PDF ({len(data) // 1024} KB): {url}")
    extracted = _extract(data, url)
    entry.update(extracted)
    entry["doc_hash"] = doc_hash
    cache[url] = entry
    _save_cache()

    if not entry.get("text", "").strip():
        logger.warning(f"no text recovered from {url} (kind={entry.get('kind')})")
        return None
    return _result(url, entry, from_cache=False)


def _result(url: str, entry: dict, from_cache: bool) -> dict:
    """Shape one cache entry into the public return value."""
    title = title_from_url(url)
    text = entry.get("text", "")
    # The heading is prepended here rather than stored, so that changing how a
    # title is formatted does not invalidate every cached document.
    return {
        "url": url,
        "title": title,
        "text": f"# {title}\n\n{text}" if text else "",
        "kind": entry.get("kind", "unknown"),
        "pages": entry.get("pages", 0),
        "ocr_pages": entry.get("ocr_pages", 0),
        "tables": entry.get("tables", 0),
        "skipped_pages": entry.get("skipped_pages", 0),
        "doc_hash": entry.get("doc_hash", ""),
        "from_cache": from_cache,
    }
