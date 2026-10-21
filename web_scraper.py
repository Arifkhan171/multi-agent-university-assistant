# ═══════════════════════════════════════════════════════════════
#  web_scraper.py — UOLI website scraper
# ═══════════════════════════════════════════════════════════════
# The 1191 lines that used to sit above this point were two complete,
# commented-out earlier copies of this module. Between them they held three
# definitions of _detect_category(), two of scrape_page(), two of
# scrape_all_pages(), two GUARANTEED_PAGES lists and two _NAV_NOISE sets, all
# subtly different from the live ones and from each other. Anyone reading the
# file to answer "how is a category decided?" found the wrong answer first.
# They are preserved verbatim in web_scraper.py.pre-single-source.bak.
#
# Category decisions are NOT made in this file. Call config.resolve_category().


"""
web_scraper.py — Production-grade University Website Scraper
============================================================
UPGRADE LOG:
  Phase 1 (original): requests + BeautifulSoup — static HTML only
  Phase 2 (current):  Playwright + BeautifulSoup + EasyOCR
    - Playwright renders JavaScript → JS icon stats now captured
    - Playwright renders full page → hover/hidden text now visible
    - href parser extracts social media links from SVG icon anchors
    - EasyOCR reads text from advertisement images automatically
    - All original logic (dedup, retry, parallel, categories) unchanged
  Phase 3 (this version): 6 production fixes
    - Fix 1: Global template images blocked (logo, nav, icons)
    - Fix 2: seen_images dedup runs after URL normalization (bug fix)
    - Fix 4: Page hash computed from HTML text only (not OCR content)
    - Fix 6: Table rows use replace_with() — eliminates double extraction
"""

import os
import re
import sys
import json
import time
import logging
import hashlib
import importlib.util
import concurrent.futures
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logger = logging.getLogger(__name__)

import config
import page_furniture
import table_reconstruct
import ocr_quality
import pdf_ingest


# ── Playwright availability check ────────────────────────────
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
    logger.info("Playwright available — JS rendering enabled ✓")
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed — falling back to requests (no JS rendering)")


# ── EasyOCR availability check ───────────────────────────────
# find_spec only looks for the package; it does not import it. Building the reader
# loads a large model (and may download ~100 MB), which made every import of this
# module slow even when no image was ever read. The reader is built on first use.
def _package_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


OCR_AVAILABLE = _package_installed("easyocr") and _package_installed("PIL")
if OCR_AVAILABLE:
    logger.info("EasyOCR installed — image text extraction enabled ✓ (loads on first use)")
else:
    logger.warning("EasyOCR not installed — image ads will not be read")

_OCR_READER = None
_OCR_READER_FAILED = False


def _get_ocr_reader():
    """Return the shared EasyOCR reader, building it on first call; None if unavailable.

    A failed build is remembered so a broken install is not retried for every image.
    """
    global _OCR_READER, _OCR_READER_FAILED
    if _OCR_READER is not None:
        return _OCR_READER
    if not OCR_AVAILABLE or _OCR_READER_FAILED:
        return None
    try:
        import easyocr
        _OCR_READER = easyocr.Reader(['en'], gpu=False, verbose=False)
    except Exception as exc:
        _OCR_READER_FAILED = True
        logger.warning(f"EasyOCR reader could not be built: {exc}")
    return _OCR_READER


def normalize_url(url: str) -> str:
    return url.lower().rstrip("/").strip()


# Built from config so the scraper and the category rules can never disagree about
# which pages exist. The name stays because setup_knowledge_base.py imports it.
GUARANTEED_PAGES = [page["url"] for page in config.UNIVERSITY_PAGES]

# Footer credit lines. Used ONLY when a page has no main#main-content: on the React
# site the footer is already outside main, so applying this inside main would delete
# real lines such as "Curriculum developed by HEC". It no longer lists the old
# developer credit, because M. Khawar Abbas is now the real Computer Science HOD.
_FOOTER_NOISE = {
    "developed by",
    "all rights reserved",
    "© 2026",
    "why uoli?",
}


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return session

# ── CATEGORY DETECTION — DELETED ON PURPOSE ──────────────────
# _detect_category() used to live here: 84 lines of substring matching over the
# URL, including a hand-maintained list of ~30 news slugs that had to be edited
# by hand every time the university published an event page. It was a second,
# independent implementation of a decision config.py already declared, and it
# disagreed with config.UNIVERSITY_PAGES on 7 of 37 URLs. It won every one of
# those disagreements, because nothing ever read config.UNIVERSITY_PAGES.
#
# That is how /undergraduate and /graduate-programs (59 chunks of program lists)
# came to be stored as "policy", a category only the policy agent reads, so
# "what programs does UoL offer?" could not reach them from the admissions path.
#
# The single implementation is now config.resolve_category(url). Call that.
# It also returns None for pages that must not be ingested at all (WordPress
# feeds, tag/pagination archives, image galleries), which this function had no
# way to express.


def _extract_social_links(soup: BeautifulSoup, base_url: str) -> str:
    """
    Extracts social media URLs from anchor tags.
    Only runs on homepage — social links are global footer items.
    """
    if "uoli.edu.pk/" in base_url.lower() and base_url.lower().rstrip("/") not in [
        "https://uoli.edu.pk", "https://uoli.edu.pk/"
    ]:
        return ""

    social_map = {
        "facebook.com":  "Facebook",
        "youtube.com":   "YouTube",
        "linkedin.com":  "LinkedIn",
        "instagram.com": "Instagram",
        "twitter.com":   "Twitter",
        "x.com":         "Twitter/X",
    }

    found = {}
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        for domain, platform in social_map.items():
            if domain in href and platform not in found:
                if not href.startswith("http"):
                    href = "https://" + href.lstrip("/")
                found[platform] = href

    if not found:
        return ""

    lines = ["University of Loralai Social Media Pages:"]
    for platform, url in found.items():
        lines.append(f"{platform}: {url}")

    result = "\n".join(lines)
    logger.info(f"Social links extracted: {list(found.keys())}")
    return result


def _image_src(img, base_url: str) -> str:
    """The real URL of an image, wherever the theme hid it.

    WHY THIS FUNCTION EXISTS — MEASURED, NOT THEORETICAL
    ────────────────────────────────────────────────────
    The university turned on a lazy-loading plugin. Every <img> on the site now
    ships as

        <img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0..."
             data-src="https://uoli.edu.pk/wp-content/uploads/.../Screenshot.png">

    The `src` is a 1-pixel placeholder; the real file is in `data-src` and the
    browser swaps it in on scroll. The old loop read `src`, saw a `data:` URI,
    and did `continue`. Measured against the live site:

        /faculty/education   37 <img> tags, 37 base64 placeholders,
                             34 real URLs recoverable from data-src,
                              0 reachable by reading src
        /                    37 <img> tags, 37 base64 placeholders,
                             33 real URLs recoverable, 0 reachable

    So image reading had silently stopped completely. Proof from the store: of
    3061 rows, exactly 25 contain an [IMAGE TEXT] block and all 25 come from the
    one page that was re-scraped by hand. The admissions advertisement is absent
    from the corpus entirely — "ADMISSIONS OPEN", "FALL 2026", "Bank Challan" and
    "DEADLINE" each return zero hits.

    Attributes are tried in the order a theme is likely to hold the truth, and a
    `src` that is a real URL still wins over nothing. srcset is parsed last
    because it needs splitting and its first entry is the smallest rendition.
    """
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        value = (img.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return _absolute_url(value, base_url)

    for attr in ("data-srcset", "srcset"):
        value = (img.get(attr) or "").strip()
        if value:
            first = value.split(",")[0].strip().split(" ")[0]
            if first and not first.startswith("data:"):
                return _absolute_url(first, base_url)
    return ""


def _absolute_url(src: str, base_url: str) -> str:
    """Resolve one image reference against the page that carried it.

    Normalisation happens BEFORE any deduplication, because the same file appears
    on this site both as "/wp-content/x.png" and as the full https URL, and
    comparing the two unnormalised forms treats one image as two.
    """
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        from urllib.parse import urljoin
        return urljoin(base_url, src)
    if src.startswith("http"):
        return src
    return ""


# ── the global image cache ──────────────────────────────────────────────
#
# Two separate jobs, which is why there are two structures and not one.
#
# _OCR_CACHE answers "what does this picture say?", keyed by the md5 of the image
# BYTES and persisted to disk. OCR of a 14-megapixel scan takes tens of seconds of
# CPU; paying that again for a file already read — on this run or any run last
# month — is pure waste. Keyed by bytes rather than by URL because this university
# re-uploads a NEW advertisement under the SAME filename every semester, so a
# URL key would serve last semester's deadlines forever.
#
# `owner` answers "which page should this picture's text appear on?". A popup ad
# and a footer notice appear on dozens of pages. Emitting the text on every one of
# them is what the user asked to stop, and independently it is what damaged
# retrieval: identical text in many pages fills the context window with copies of
# one fact. The first page to read an image owns it, the decision is written to
# disk, and every other page skips it — including on later incremental runs,
# where the owner page may itself be skipped as unchanged and so could not
# re-claim the image in memory.
_OCR_CACHE_PATH = os.path.join("data", "ocr_cache.json")
_OCR_CACHE: dict | None = None

# url -> md5 of its bytes, for this process only. Saves re-downloading the same
# footer image once per page; the bytes themselves are not kept, only the digest.
_IMAGE_DIGEST_BY_URL: dict = {}


def _load_ocr_cache() -> dict:
    """Read the on-disk OCR cache, tolerating every way it can be absent."""
    global _OCR_CACHE
    if _OCR_CACHE is not None:
        return _OCR_CACHE
    try:
        with open(_OCR_CACHE_PATH, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        _OCR_CACHE = loaded if isinstance(loaded, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _OCR_CACHE = {}
    return _OCR_CACHE


def _save_ocr_cache() -> None:
    """Persist the OCR cache. A failure here must never fail a scrape.

    Written whole rather than appended: the file is a few hundred KB at most and
    an atomic replace cannot leave a half-written entry that the next run parses
    as valid JSON with a truncated table in it.
    """
    cache = _load_ocr_cache()
    try:
        os.makedirs(os.path.dirname(_OCR_CACHE_PATH), exist_ok=True)
        tmp = _OCR_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, _OCR_CACHE_PATH)
    except OSError as exc:
        logger.warning(f"could not save OCR cache: {exc}")


def reset_image_ownership() -> None:
    """Forget which page owns which image, keeping the read text.

    For the case where pages have been renamed or removed and image text should be
    allowed to settle on whichever page now carries it. Keeps the expensive part —
    the OCR output — and discards only the cheap bookkeeping.
    """
    cache = _load_ocr_cache()
    for entry in cache.values():
        if isinstance(entry, dict):
            entry.pop("owner", None)
    _save_ocr_cache()


def _read_image_text(image_data: bytes, digest: str, src: str) -> dict:
    """OCR one image's bytes, or return the cached reading.

    Returns {"text", "is_table", "confidence", "rows", "cols"}. Always a dict, so
    the caller never has to distinguish "failed" from "empty" — both give text "".
    """
    cache = _load_ocr_cache()
    hit = cache.get(digest)
    if isinstance(hit, dict) and "text" in hit:
        return hit

    result = {"text": "", "is_table": False, "confidence": 0.0,
              "rows": 0, "cols": 0}
    reader = _get_ocr_reader()
    if reader is None:
        # Not cached: a missing reader is a setup problem, not a fact about the image.
        return result
    try:
        from PIL import Image
        import io
        import numpy as np

        image = Image.open(io.BytesIO(image_data))
        if image.mode != "RGB":
            image = image.convert("RGB")

        # detail=1, paragraph=False — the two arguments this pipeline depends on.
        #
        # The previous call was readtext(image_np, detail=0, paragraph=True) and
        # each argument destroyed a table on its own. detail=0 discards the
        # bounding boxes, so no later stage can know which cells share a row.
        # paragraph=True merges neighbouring boxes, and in a table the nearest
        # neighbour of a cell is the cell BELOW it, so it merges down columns. The
        # measured output for the education faculty's course table was
        #
        #   S: No 1 2 3 5 6 | Course code 311 321 331 341 351 361 |
        #   Credit Hours 3(3+0) 2(2+0) 3(3+0) 3(3+0) 303+0) 3(3+0) 17
        #
        # Every figure present, every relationship between them gone. With the
        # boxes kept and table_reconstruct rebuilding the grid, the same image now
        # reads "| 3 | 331 | Foundation | Child Development | 3(3+0) |" — the
        # course code, its name and its credit hours on one line, which is the
        # only form in which the question "how many credits is 331?" is
        # answerable.
        boxes = reader.readtext(np.array(image), detail=1, paragraph=False)
        result = table_reconstruct.reconstruct(boxes)
    except Exception as exc:
        logger.debug(f"OCR failed for {src}: {exc}")
        # Cached as an empty reading on purpose. A file that cannot be decoded
        # will not decode next time either, and retrying it on every six-hourly
        # scrape costs a download and a decode attempt for nothing.

    cache[digest] = result
    return result


# Site furniture. Present on every page, contains no sentence, and reading it
# produced the three 57-79 character logo crops that used to reach the store.
#
# MODULE LEVEL, not inside _extract_images_text, so a test can import the real
# value instead of recovering it from the function's source text. The previous
# version of test_page_furniture.py did exactly that — inspect.getsource plus a
# regex plus exec — and when four of the names it looked for were deleted it
# raised AttributeError inside a try/except that reported one tidy failure. A
# test that reconstructs the rule it is testing cannot tell you the rule changed.
_GLOBAL_SKIP_PATTERNS = [
    "logo", "icon", "favicon", "arrow", "button",
    "avatar", "thumb", "social",
    "university-of-loralai-1",   # footer logo — on all pages
    "elementor/thumbs",           # nav logo thumbnail — all pages
    "pngtree",                    # whatsapp popup icon — all pages
    "flaticon.com",               # external CDN research icons — all pages
]

# Raster formats only. SVG is vector site furniture on this site (every icon), and
# OCR on a rasterised SVG returns the icon's own decorative strokes as garbage.
_RASTER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# Under this many bytes an image is an icon or a spacer at any realistic
# resolution, so downloading it to read it cannot pay for itself.
MIN_IMAGE_BYTES = 20_000


def image_passes_url_gate(src: str, is_homepage: bool = True) -> bool:
    """The pre-download half of the image gate, as a callable.

    Everything this can decide is decided from the URL alone: site furniture, a
    non-raster format, and the homepage-only rule for the popup advertisement.
    Whether an image is a DOCUMENT is deliberately not decided here — that needs
    the text, and ocr_quality.is_usable decides it after the read.

    Extracted from _extract_images_text so the rule has exactly one definition
    and a test can call it directly.
    """
    src_lower = src.lower()
    if "advertisement" in src_lower and not is_homepage:
        return False
    if any(p in src_lower for p in _GLOBAL_SKIP_PATTERNS):
        return False
    if not any(ext in src_lower for ext in _RASTER_EXTENSIONS):
        return False
    return True


def _extract_images_text(soup: BeautifulSoup, base_url: str) -> str:
    """
    Reads the text out of a page's images.

    WHAT CHANGED AND WHY
    ────────────────────
    Three decisions in this function used to be made from an image's FILENAME.
    They are now made from the image itself.

    1. WHICH IMAGES TO READ.  An allowlist of words — "screenshot", "notice",
       "prospectus", "fee" — had to appear in the URL or alt text. It existed for
       a real reason: reading everything pulled in 50 Facebook event posters and
       12 ceremony photographs, the corpus grew by ~600 chunks, and answers got
       worse. But it fails in the direction that loses content, because it can
       only admit names somebody thought of in advance. Reading is free — EasyOCR
       runs locally, no API call — so the gate moved to after the read, where the
       actual text can be judged. See ocr_quality.
       Photographs and site furniture are still refused before download: a
       ceremony photo and a nav logo contain no document text, so reading them can
       only produce noise, and downloading them costs bandwidth for nothing.

    2. WHETHER A REPEATED IMAGE IS READ AGAIN.  The old dedup set was created
       inside this function, so it only ever saw one page. The advertisement was
       special-cased to the homepage by name and every other repeated image — a
       footer notice, a banner on twelve department pages — was read and stored
       once per page. Now the md5 of the image bytes is the key, the first page to
       read an image owns it, and that decision persists to disk across runs.

    3. HOW A TABLE IS READ.  See _read_image_text.

    KNOWN LIMITS, stated rather than discovered later:
      * images referenced only from CSS `background-image` are not visible to
        BeautifulSoup and are not read;
      * <picture><source srcset> is not walked, only <img>;
      * if the page that owns an image later drops it, the text goes with it until
        reset_image_ownership() is called.
    """
    if not OCR_AVAILABLE:
        return ""

    extracted_texts = []
    seen_on_this_page = set()
    cache = _load_ocr_cache()
    cache_dirty = False

    # Advertisement.jpeg is a JS popup that fires on many pages. The byte-level
    # owner rule below would already keep it to one page, but which page won would
    # depend on crawl order, and this image belongs on the homepage: it is the
    # university's current admissions notice and the homepage is where a reader
    # looking for it would go.
    is_homepage = base_url.rstrip("/").lower() in [
        "https://uoli.edu.pk", "https://uoli.edu.pk/"
    ]

    # Site furniture, raster-only and the advertisement rule all live in
    # image_passes_url_gate at module level, so there is one definition of them.

    # There is deliberately NO filename-based photograph filter any more.
    #
    # There was one: dsc_, dsc-, img_, img-, _mg_, whatsapp, -scaled, gallery,
    # slider, slide-. Measured against the live site over the homepage, both
    # faculty pages and /downloads — 93 raster images — that list of ten patterns
    # blocked four images, and all four came from the single entry "whatsapp".
    # Every other pattern in it matched nothing at all.
    #
    # The four it did block include WhatsApp-Image-2025-07-06-at-11.34.57-AM.jpeg
    # on /faculty/education, which is a screenshot of a notice: real university
    # content, refused because of the app it arrived through. That is the same
    # mistake as the old social-export rule, which refused all 50 event posters by
    # the shape of their filenames.
    #
    # A filename cannot know whether an image is a document. ocr_quality.is_usable
    # can, because it reads the text first: a photograph of a convocation yields
    # either nothing or a handful of garbage tokens and is rejected on MIN_CHARS
    # and wordish, while a notice screenshot passes. The measured cost of removing
    # the list is four extra image reads, each cached by content hash so it is paid
    # once ever. The benefit is that requirement 2 — no image, icon or ad skipped —
    # is actually true.

    for img in soup.find_all("img"):
        src = _image_src(img, base_url)
        if not src:
            continue

        if src in seen_on_this_page:
            continue
        seen_on_this_page.add(src)

        if not image_passes_url_gate(src, is_homepage):
            continue

        try:
            digest = _IMAGE_DIGEST_BY_URL.get(src)
            image_data = None

            if digest is None:
                import urllib.request
                req = urllib.request.Request(
                    src, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as response:
                    image_data = response.read()

                # Under 20KB is an icon or a spacer at any realistic resolution.
                if len(image_data) < MIN_IMAGE_BYTES:
                    continue

                digest = hashlib.md5(image_data).hexdigest()
                _IMAGE_DIGEST_BY_URL[src] = digest

            entry = cache.get(digest)
            owner = entry.get("owner") if isinstance(entry, dict) else None

            # The heart of requirement 2: one image, read once, stored once.
            if owner and owner != base_url:
                logger.debug(f"image already owned by {owner}, skipping: {src}")
                continue

            if image_data is None and not isinstance(entry, dict):
                # A URL seen earlier this run whose bytes were never cached. Only
                # reachable if the cache file was deleted mid-run; re-download
                # rather than guess.
                import urllib.request
                req = urllib.request.Request(
                    src, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as response:
                    image_data = response.read()

            before = len(cache)
            reading = _read_image_text(image_data or b"", digest, src)
            cache_dirty = cache_dirty or len(cache) != before or entry is None

            keep, reason = ocr_quality.is_usable(
                reading.get("text", ""),
                reading.get("confidence", 0.0),
                reading.get("is_table", False),
            )
            if not keep:
                logger.debug(f"OCR text rejected ({reason}): {src}")
                continue

            # Claim ownership only for text that was actually stored. Claiming it
            # for a rejected reading would let one page's failed attempt block
            # every other page from ever trying.
            reading["owner"] = base_url
            cache[digest] = reading
            cache_dirty = True

            # Delimited, and without the src. page_furniture explains why both of
            # those matter; the short version is that an unclosed marker cost 88
            # parents most of their text, and the URL was 100+ characters of
            # upload path embedded in retrievable content. Provenance goes to the
            # log on the next line.
            extracted_texts.append(
                f"{page_furniture.IMAGE_BLOCK_OPEN}\n{reading['text']}\n"
                f"{page_furniture.IMAGE_BLOCK_CLOSE}"
            )
            shape = (f"table {reading['rows']}x{reading['cols']}"
                     if reading.get("is_table") else "prose")
            logger.info(
                f"OCR kept {len(reading['text'])} chars ({shape}, "
                f"conf {reading.get('confidence', 0):.2f}) from: {src}")

        except Exception as e:
            logger.debug(f"image read failed for {src}: {e}")
            continue

    if cache_dirty:
        _save_ocr_cache()

    return "\n\n".join(extracted_texts)


# A <header>/<nav>/<footer>/<aside> bigger than this is not chrome. Set well
# above the largest one measured on the live site (558 chars) and well below the
# smallest real page body, so it can only ever fire on a theme that has put
# content inside a landmark — in which case the page is kept and the log says so.
_MAX_CHROME_CHARS = 2000

# What counts as "this landmark is really the contact block". Deliberately
# specific: a bare "@" would match a social handle in the menu, so require a
# full email, an explicit tel: link, or a Pakistani phone number.
_CONTACT_SIGNAL_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"      # email address
    r"|tel:"                         # click-to-call link
    r"|\+92[\s\-(]*\d"               # +92 … international form
    r"|\(\d{3,4}\)\s*\d{5,}",        # (824) 410051 local form
    re.I)


# ── Person cards ───────────────────────────────────────────────────────────
#
# WHY THIS EXISTS
# Every staff member is a self-contained card with a name, a job, a department, an
# optional email and a "Profile" button. get_text() flattens the cards into loose
# lines, which destroys the one thing that matters: which email belongs to which
# person. One atomic line per person makes a name paired with a stranger's email
# impossible, and an explicit "Email not listed." stops the LLM from filling a gap
# from a neighbouring card.
#
# A card is found by its "Profile" button: the smallest box that holds the button
# and exactly one <h3> (the name). No class names are used, because the site's
# Tailwind classes change with every build.

_UNIT_RE = re.compile(r"^(department|directorate|office|cent(?:re|er))\b", re.I)
_HOD_BADGES = {"hod", "department head"}
_HOD_JOB_RE = re.compile(r"\bHOD\b|head of|chairperson|chairman", re.I)


def _find_person_cards(main) -> list:
    """Each person card in main: the smallest box around a Profile button and one <h3>."""
    cards, seen = [], set()
    for button in main.find_all(["a", "button"]):
        if not re.match(r"^Profile\b", button.get_text(" ", strip=True)):
            continue
        box = button
        while box is not None and box is not main and box.find("h3") is None:
            box = box.parent
        if box is None or box is main or len(box.find_all("h3")) != 1:
            logger.warning("Profile button without a single-name card — left as text")
            continue
        if id(box) not in seen:
            seen.add(id(box))
            cards.append(box)
    return cards


def _card_badges(card) -> list[str]:
    """Short labels such as HOD, Visiting or On Leave: spans that are not part of a
    paragraph, the email link or the Profile button."""
    badges = []
    for span in card.find_all("span"):
        inside_text = False
        for parent in span.parents:
            if parent is card:
                break
            if parent.name in ("p", "a", "button"):
                inside_text = True
                break
        text = span.get_text(" ", strip=True)
        if text and not inside_text:
            badges.append(text)
    return badges


def _person_lines(card, seen_emails: set) -> list[str]:
    """One person card as clean sentences; [] if this person was already written."""
    name = card.find("h3").get_text(" ", strip=True)
    mailto = card.select_one('a[href^="mailto:"]')
    email = ""
    if mailto is not None:
        email = (mailto.get("href", "")[len("mailto:"):].strip()
                 or mailto.get_text(strip=True))
    if "@" not in email:
        email = ""
    # The same person can be listed twice on one page (a department head appears in
    # the head box and again in the list); the second copy adds nothing.
    if email and email.lower() in seen_emails:
        return []
    if email:
        seen_emails.add(email.lower())

    paragraphs = [p.get_text(" ", strip=True) for p in card.find_all("p")]
    department = next((p for p in paragraphs if _UNIT_RE.match(p)), "")
    area = next((p for p in paragraphs if p.lower().startswith("area:")), "")
    job = next((p for p in paragraphs
                if p and p != department and p != area), "")

    badges = _card_badges(card)
    lowered = {b.lower() for b in badges}
    is_hod = bool(lowered & _HOD_BADGES) or bool(_HOD_JOB_RE.search(job))
    if "visiting" in lowered and job:
        job += " (Visiting)"
    status = [b for b in badges if b.lower() not in _HOD_BADGES | {"visiting"}
              and b.lower() != "profile" and b != "→" and "@" not in b]

    details = ", ".join(x for x in (job, department) if x)
    line = f"{name} — {details}." if details else f"{name}."
    if status:
        line += f" Status: {', '.join(status)}."
    if area:
        line += f" {area.rstrip('.')}."
    line += f" Email: {email}." if email else " Email not listed."
    tel = card.select_one('a[href^="tel:"]')
    if tel is not None:
        phone = tel.get("href", "")[len("tel:"):].strip() or tel.get_text(strip=True)
        line += f" Phone extension: {phone}."

    lines = [line]
    if is_hod and department:
        lines.append(f"{name} is the Head of the {department} "
                     f"at the {config.UNIVERSITY_NAME}.")
    return lines


def _extract_people(main, base_url: str) -> None:
    """Replace every person card in-place with its clean lines.

    Uses replace_with() for the same reason the <table> handler does: anything
    left in the soup is re-read by get_text() further down, so inserting text
    without removing the original markup duplicates every field.
    """
    cards = _find_person_cards(main)
    seen_emails: set = set()
    written = 0
    for card in cards:
        lines = _person_lines(card, seen_emails)
        if lines:
            card.replace_with("\n" + "\n".join(lines) + "\n")
            written += 1
        else:
            card.decompose()
    if cards:
        logger.info(f"person cards: {written} written, {len(cards) - written} "
                    f"repeated, on {base_url}")


def _remove_landmarks_outside_main(soup: BeautifulSoup, main) -> None:
    """Delete the site header, menu and footer that sit around main#main-content.

    Landmarks INSIDE main are content and stay: the page title is a <header> in
    main, and the event details are an <aside> in main. A landmark that wraps main
    is also kept, because deleting it would delete the page.
    """
    for tag in soup(["header", "nav", "footer", "aside"]):
        if getattr(tag, "decomposed", False):
            continue
        inside_main = any(p is main for p in tag.parents)
        wraps_main = any(p is tag for p in main.parents)
        if not inside_main and not wraps_main:
            tag.decompose()


def _remove_breadcrumb(main) -> None:
    # The trail ("Home › About") is inside main on every page and says nothing the
    # page title does not already say.
    for crumb in main.select('nav[aria-label="Breadcrumb" i]'):
        crumb.decompose()


# Exact UI labels that carry no information. Matched against a whole line (or a
# whole table cell), never as a substring, so a real sentence is never touched.
_BUTTON_LABELS = frozenset({
    "Share", "Print", "Profile", "Profile →", "→",
    "Tap course for syllabus", "Tap course for details", "View Outline →",
    "Fullscreen", "Try Again", "Ask AI", "Ask AI Assistant",
})
_LABEL_RE = re.compile(r"^(Sem \d+|Showing \d+ Courses)$")
_SEM_BUTTON_RE = re.compile(r"^\s*Sem\s*(\d+)", re.I)
_FILTER_BUTTON_RE = re.compile(r"^All\b")
_SEMESTER_ATTR = "data-uoli-semesters"
_MIN_OWN_CHARS = 150
_EMPTY_PAGE_MARKERS = ("Program Details Not Found", "We could not find the requested")


def _is_label(text: str) -> bool:
    text = text.strip("•#| ")
    return text in _BUTTON_LABELS or bool(_LABEL_RE.match(text))


def _remove_filter_bars(main, base_url: str = "") -> None:
    """Delete the buttons of every filter bar ("All Departments", "All Events (12)").

    They are page controls, not facts. A bar is found by one button that starts with
    the WORD "All" (so "Allied Health Sciences" is safe); the bar is the smallest box
    holding two or more buttons. Only the buttons go, never the box, so content that
    happens to share the box survives. Question buttons on the FAQ page sit in a
    different box and stay.
    """
    removed = []
    for button in main.find_all("button"):
        if button.parent is None or not _FILTER_BUTTON_RE.match(button.get_text(" ", strip=True)):
            continue
        box = button.parent
        while box is not None and box is not main and len(box.find_all("button")) < 2:
            box = box.parent
        if box is None or box is main or len(box.find_all("button")) > 15:
            continue
        for item in box.find_all("button"):
            removed.append(item.get_text(" ", strip=True))
            item.decompose()
    if removed:
        logger.info(f"filter bar buttons removed on {base_url}: {removed}")


def _course_area(main, section):
    """Smallest box that holds every 'Sem N' button outside `section` plus the table."""
    buttons = [b for b in main.find_all("button")
               if _SEM_BUTTON_RE.match(b.get_text(" ", strip=True))
               and section not in b.parents]
    tables = [t for t in main.find_all("table") if section not in t.parents]
    if not buttons or not tables:
        return None
    box = buttons[0]
    while box is not None and box is not main:
        if tables[0] in box.descendants and all(b in box.descendants for b in buttons):
            return box
        box = box.parent
    return None


def _remove_course_area(main, base_url: str = "") -> None:
    """Once every semester table has been copied into the marked section, delete the
    original course area: the first table, the Sem buttons and the phone copy of the
    list would otherwise repeat Semester I's courses.

    Found by structure (buttons + table), never by class names, which change with
    every site build.
    """
    section = main.select_one(f"section[{_SEMESTER_ATTR}]")
    if section is None:
        return
    area = _course_area(main, section)
    if area is None:
        logger.warning(f"semester copies found but no course area to remove: {base_url}")
        return
    logger.info(f"course area removed ({len(area.get_text(strip=True))} chars) on {base_url}")
    area.decompose()


def _is_empty_page(soup: BeautifulSoup) -> bool:
    """True if the page has no content of its own.

    Measured on a copy of main without the breadcrumb and the title header, so the
    page's own words are what is counted. The check is skipped when there is no
    main#main-content: such a page is judged by the old rules.
    """
    main = soup.select_one("main#main-content")
    if main is None:
        return False
    copy = BeautifulSoup(str(main), "html.parser")
    for tag in copy(["script", "style", "noscript", "svg", "iframe", "header"]):
        tag.decompose()
    _remove_breadcrumb(copy)
    text = copy.get_text(" ", strip=True)
    return len(text) < _MIN_OWN_CHARS or any(m in text for m in _EMPTY_PAGE_MARKERS)


def _remove_chrome_landmarks(soup: BeautifulSoup, base_url: str) -> None:
    """Fallback for a page with no main#main-content: delete the chrome landmarks.

    The contact page is not exempt outright. An earlier exemption kept the menu in
    the /contact parent, so menu labels were interleaved with the contact facts and
    "what is the phone number" retrieved the wrong documents. Instead, on that page
    a landmark is removed only once it has been shown not to carry contact details.
    """
    _is_contact_page = page_furniture.is_contact_page(base_url)
    for tag in soup(["header", "nav", "footer", "aside"]):
        # A landmark holding a page's actual content is a theme bug, and silently
        # deleting a thousand characters of real text only ever shows up later as
        # a lower RAGAS score, never as an error.
        if len(tag.get_text(strip=True)) > _MAX_CHROME_CHARS:
            logger.warning(
                f"<{tag.name}> holds {len(tag.get_text(strip=True))} chars — "
                f"too much to be chrome, keeping it: {base_url}")
            continue
        # Hrefs are checked as well as the text because a click-to-call link
        # carries the number in tel: and shows only "Call us".
        if _is_contact_page:
            _probe = tag.get_text(" ", strip=True) + " " + " ".join(
                a.get("href", "") for a in tag.find_all("a"))
            if _CONTACT_SIGNAL_RE.search(_probe):
                logger.info(f"contact page: keeping <{tag.name}> — "
                            f"it carries contact details")
                continue
        tag.decompose()


def _find_fallback_main(soup: BeautifulSoup):
    """Best content block for a page with no main#main-content (old-style pages)."""
    for selector in [
        "main",
        "article",
        '[id*="content"]',
        '[class*="entry-content"]',
        '[class*="page-content"]',
        '[class*="post-content"]',
        "body",
    ]:
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(strip=True)) > 100:
            return candidate
    return soup


def _extract_text(soup: BeautifulSoup, base_url: str = "") -> str:

    """
    Smart multi-stage content extractor.
    Fix 6: Tables use replace_with() — eliminates row duplication.
    """
    # Stage 1: Remove noise tags
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    # Stage 1a: Find the real content block and remove the site chrome around it.
    # The site nests two main#main-content tags; select_one returns the outer one,
    # which also holds the breadcrumb and the page-title <header>.
    main = soup.select_one("main#main-content")
    has_main = main is not None
    if has_main:
        _remove_landmarks_outside_main(soup, main)
    else:
        logger.info(f"no main#main-content, using the fallback selectors: {base_url}")
        _remove_chrome_landmarks(soup, base_url)

    # Stage 1b: Extract social links (anchor hrefs survive SVG removal)
    social_text = _extract_social_links(soup, base_url)

    # Stage 1c: Extract image text via OCR
    image_text = _extract_images_text(soup, base_url)

    # Stage 2: Content block
    if has_main:
        _remove_breadcrumb(main)
    else:
        main = _find_fallback_main(soup)

    # Stage 2b: Person cards → one atomic line per person.
    # Runs before the markdown markers below because a card is found by its single
    # <h3>, and those markers would otherwise turn the name into a "###" heading.
    _extract_people(main, base_url)

    # Stage 2c: page controls. The course area goes first because its Sem buttons
    # must still be there to find it; the filter bars after the person cards,
    # because their "Profile" buttons are already gone.
    _remove_course_area(main, base_url)
    _remove_filter_bars(main, base_url)

    # Stage 3: Add markdown markers for headings, paragraphs, lists
    for tag in main.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = tag.name[1]
        tag.insert_before(f"\n{'#' * int(level)} ")
        tag.insert_after("\n")

    for tag in main.find_all("p"):
        tag.insert_after("\n")

    for tag in main.find_all("li"):
        tag.insert_before("\n• ")

    # FIX 6: Replace entire table with clean markdown text
    # WHY: Old code used insert_before() on each <tr> which left the
    # original <td>/<th> text in the soup. get_text() then extracted
    # BOTH the inserted markdown row AND the raw cell text — every
    # table row appeared twice in the output.
    # replace_with() removes the original table entirely from soup.
    # get_text() sees only the clean markdown text. Zero duplication.
    for table in main.find_all("table"):
        rows_text = []
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            # A label cell ("Tap course for syllabus") is a button hint, not data.
            cells = [c for c in cells if not _is_label(c)]
            if any(c for c in cells if c):  # skip empty rows
                rows_text.append("| " + " | ".join(cells) + " |")
        if rows_text:
            table.replace_with("\n" + "\n".join(rows_text) + "\n")
        else:
            table.decompose()

    # Stage 4: Extract and clean text
    raw_text = main.get_text(separator="\n", strip=True)
    lines = raw_text.split("\n")
    cleaned = []
    seen = set()
    # A table row repeats only within its own table block, scoped by the last
    # ordinary line before it (for a semester table, the "Semester N" heading).
    # Without this, the header row and any equal "Total Courses: 6 | 18 Cr. Hrs"
    # row of a later semester were dropped as repeats of an earlier semester's.
    scope = ""

    IMPORTANT_SHORT = {"cs", "vc", "it", "hod", "bs", "ms", "phd"}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        line_lower = line.lower().strip("•#| ")
        if not has_main and any(signal in line_lower for signal in _FOOTER_NOISE):
            continue
        if _is_label(line):
            continue
        is_stat_number = bool(re.match(r'^\d+[%+]?$', line))
        if len(line) < 3 and line_lower not in IMPORTANT_SHORT and not is_stat_number:
            continue
        if line.startswith("http") and " " not in line:
            continue
        if re.search(r"[\{\};]", line) and len(line.split()) < 6:
            continue
        dedup_key = re.sub(r"^[#•|\s]+", "", line_lower)
        if line.startswith("|"):
            dedup_key = scope + " || " + dedup_key
        else:
            scope = dedup_key
        if dedup_key in seen and not is_stat_number:
            continue
        seen.add(dedup_key)
        cleaned.append(line)

    main_text = "\n".join(cleaned)

    # Stage 5: Append OCR and social link extras
    extras = []
    if social_text:
        extras.append(social_text)
    if image_text:
        extras.append(image_text)

    if extras:
        return main_text + "\n\n" + "\n\n".join(extras)
    return main_text


# textContent, not innerText: on the live site the short "Sem 1" label is a hidden
# span, which innerText leaves out, so no button would match.
_TABLE_JS = r"""() => {
  const main = document.querySelector('main#main-content');
  if (!main) return null;
  const btns = [...main.querySelectorAll('button')].filter(b => /^\s*Sem\s*\d+/i.test(b.textContent));
  if (!btns.length) return null;
  let box = btns[0];
  while (box && box !== main) {
    if (btns.every(b => box.contains(b)) && box.querySelector('table')) break;
    box = box.parentElement;
  }
  const table = box && box !== main ? box.querySelector('table') : null;
  const heading = box && box !== main ? box.querySelector('h1,h2,h3,h4,h5,h6') : null;
  return table ? {table: table.outerHTML, heading: heading ? heading.innerText : ''} : null;
}"""

_ADD_SECTION_JS = """(html) => {
  const main = document.querySelector('main#main-content');
  const section = document.createElement('section');
  section.setAttribute('data-uoli-semesters', '');
  section.innerHTML = html;
  main.appendChild(section);
}"""


def _scroll_to_bottom(page, max_rounds: int = 10) -> None:
    """Scroll until the page stops growing, so lazy sections are in the DOM."""
    try:
        last = 0
        for _ in range(max_rounds):
            height = page.evaluate("document.body.scrollHeight")
            if height == last:
                break
            last = height
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
    except Exception as exc:
        logger.debug(f"scroll failed: {exc}")


def _read_settled_table(page, max_rounds: int = 15):
    """Read the course table once it has stopped changing.

    The live table fills in steps (rows first, then the header and the "Total
    Courses" row), and the first change is not the finished table. So read again
    every 200 ms until two reads in a row are equal, for at most 3 s (15 rounds).
    """
    last = page.evaluate(_TABLE_JS)
    for _ in range(max_rounds):
        page.wait_for_timeout(200)
        current = page.evaluate(_TABLE_JS)
        if current == last:
            return current
        last = current
    logger.warning("table kept changing for 3 s, copied as it is")
    return last


def _expand_semester_tabs(page) -> None:
    """Click each 'Sem N' button in turn and keep every semester's table.

    The page shows Semester I only; the other tables exist only after a click. The
    copies are added to the end of main inside one marked <section>, so the text
    step reads them and can then drop the original course area. Tabs that are not
    'Sem N' are only logged, never clicked.
    """
    try:
        buttons = page.locator("main#main-content button").filter(
            has_text=re.compile(r"^\s*Sem\s*\d+", re.I))
        count = buttons.count()
        if count == 0:
            return
        other_tabs = [t for t in page.locator('main#main-content [role="tab"]').all_inner_texts()
                      if not _SEM_BUTTON_RE.match(t)]
        if other_tabs:
            logger.info(f"other tabs left unclicked: {other_tabs}")

        parts, heading = [], ""
        for i in range(count):
            button = buttons.nth(i)
            spans = [x.strip() for x in button.locator("span").all_inner_texts() if x.strip()]
            name = spans[1] if len(spans) > 1 else f"Semester {i + 1}"
            before = page.evaluate(_TABLE_JS)
            if i > 0:
                try:
                    button.click(timeout=5_000)
                except Exception as exc:
                    # Skipped, not copied: the table on screen would still be the
                    # previous semester's, stored under the wrong name.
                    logger.warning(f"click on {name!r} failed, semester skipped: {exc}")
                    continue
                for _ in range(25):                  # up to 5 s
                    page.wait_for_timeout(200)
                    current = page.evaluate(_TABLE_JS)
                    if current and before and current["table"] != before["table"]:
                        break
                else:
                    logger.warning(f"table did not change after clicking {name!r}")
            data = _read_settled_table(page)
            if not data:
                logger.warning(f"no table found for {name!r}")
                continue
            heading = heading or data["heading"]
            parts.append(f"<h3>{name}</h3>{data['table']}")
        if parts:
            title = f"<h2>{heading}</h2>" if heading else ""
            page.evaluate(_ADD_SECTION_JS, title + "".join(parts))
            logger.info(f"semester tables copied: {len(parts)} of {count}")
    except Exception as exc:
        logger.warning(f"semester tabs not read: {exc}")


def _fetch_with_playwright(url: str) -> str | None:
    """
    Fetches fully rendered HTML using headless Chromium.
    Waits for networkidle so all JS has run before reading DOM.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            def _block_resources(route):
                if route.request.resource_type in ["image", "media", "font"]:
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", _block_resources)

            try:
                page.goto(url, wait_until="networkidle", timeout=60_000)
            except Exception:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    page.wait_for_timeout(2000)
                except Exception as e:
                    logger.warning(f"Playwright navigation failed: {url} — {e}")
                    browser.close()
                    return None

            # The site loads some links and sections only when scrolled into view.
            _scroll_to_bottom(page)
            _expand_semester_tabs(page)

            html = page.content()
            browser.close()
            return html

    except Exception as e:
        logger.warning(f"Playwright error for {url}: {e}")
        return None


def _looks_dead(text: str) -> bool:
    """True if this text is a server error template rather than a page.

    Thin alias for page_furniture.looks_dead, kept because config.py's module
    docstring documents the URL rules as being enforced by
    "web_scraper._looks_dead()" and that name should resolve to something. The
    rule itself lives in page_furniture so that the ranking penalty in
    knowledge_base can share the one definition.
    """
    return page_furniture.looks_dead(text)


_PDF_REFERRERS: dict[str, set] = {}


def reset_pdf_links() -> None:
    """Forget every discovered PDF. For dry runs and repeated in-process scrapes."""
    _PDF_REFERRERS.clear()


def _note_pdf_links(html: str, page_url: str) -> None:
    """Record which page referenced which PDF.

    The referrer is kept, not just the PDF URL, because the referrer is what
    decides the document's category. A PDF's own URL is always
    /wp-content/uploads/YYYY/MM/... which says nothing about its subject, so
    resolving a category from it would file all 25 documents identically. The page
    that links it does know: a file linked from /downloads is policy, one linked
    from an admissions page is admissions.
    """
    try:
        for pdf_url in pdf_ingest.discover(html, page_url):
            _PDF_REFERRERS.setdefault(pdf_url, set()).add(page_url)
    except Exception as exc:                       # never fail a page over this
        logger.debug(f"PDF discovery failed on {page_url}: {exc}")


def _pdf_category(pdf_url: str, referrers: set):
    """Category for a PDF, or None to skip it.

    Delegates to config.resolve_pdf_category so the scraper and the
    proposition re-indexer agree on where every document belongs. The filename
    decides first (a file named "...-Policy.pdf" is policy no matter which page
    links it), the linking pages break ties, and procurement documents return
    None so they are never ingested.
    """
    return config.resolve_pdf_category(pdf_url, referrers)


def fetch_page(url: str, session: requests.Session = None) -> tuple[str | None, str]:
    """
    Scrapes one URL. Playwright first, requests fallback.

    Returns (text, reason). text is None for a page that has nothing to
    contribute; reason then says which of five different things happened:

        "unreachable"  the fetch failed, the server did not return HTML, or the
                       text step crashed on the page
        "dead"         a server error template — a soft 404
        "empty"        the page has no content of its own yet (for example a
                       program page that says "Program Details Not Found")
        "too_short"    under 100 chars of extracted text
        "ok"           text is usable

    The distinction matters operationally. All of them used to collapse into None,
    so a completely healthy run of the site reported "Failed: 1" — the 404 page —
    and there was no way to tell that from a network problem without reading the
    log line by line. "dead" is a correct outcome; "unreachable" is one to
    investigate.

    scrape_page() is the str-or-None view of this, kept because three other
    modules call it and only care whether they got text.
    """
    html = None

    # Check disk cache for web pages (e.g. from dryrun) so re-runs load instantly
    _cache_file = os.path.join("scratch", "_dryrun_web_cache.json")
    if False:  # disabled in production to avoid stale HTML hiding new PDFs
        try:
            with open(_cache_file, "r", encoding="utf-8") as fh:
                _wc = json.load(fh)
            if url in _wc and _wc[url].get("text"):
                logger.info(f"Using cached web page ({len(_wc[url]['text'])} chars): {url}")
                # Replay PDF links so _PDF_REFERRERS is populated even from cache
                for pdf_url in _wc[url].get("pdf_links", []):
                    _PDF_REFERRERS.setdefault(pdf_url, set()).add(url)
                return _wc[url]["text"], _wc[url].get("reason", "ok")
        except Exception:
            pass

    if PLAYWRIGHT_AVAILABLE:
        html = _fetch_with_playwright(url)
        if html:
            logger.debug(f"Playwright fetched: {url}")

    if not html:
        _session = session or _make_session()
        try:
            response = _session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = "utf-8"
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                logger.debug(f"Skipping non-HTML: {url}")
                return None, "unreachable"
            html = response.text
            logger.debug(f"Requests fallback fetched: {url}")
        except Exception as e:
            logger.warning(f"Requests also failed for {url}: {e}")
            return None, "unreachable"

    soup = BeautifulSoup(html, "html.parser")
    # Recorded from the HTML of EVERY page, before any downstream decision.
    #
    # The pages that link the most PDFs are precisely the ones pass 2 discards:
    # /advertisement-for-visiting-faculty and /full-bright-2027 are a heading, a
    # sentence and a button, so find_contentless flags them and they are never
    # ingested. Their PDFs must be, and that is where the content actually is.
    _note_pdf_links(html, url)

    # Checked before _extract_text, which edits the soup. An empty page is skipped
    # only for this run: it stays in the config.py list and is read again next time,
    # so it is stored as soon as the website fills it.
    if _is_empty_page(soup):
        logger.warning(f"Empty page (no content of its own), not ingesting: {url}")
        return None, "empty"

    try:
        text = _extract_text(soup, base_url=url)
    except Exception as exc:
        # One odd page must not stop a 115-page run.
        logger.error(f"Text extraction failed for {url}: {exc}", exc_info=True)
        return None, "unreachable"

    # A soft 404. Checked before the length test so the log names the real
    # reason: this site's error template is 179 chars, comfortably over the
    # 100-char floor, so without this it would be ingested as a normal page.
    #
    # It has to be rejected here rather than later because the damage is done at
    # ingest: /offices/faculty-and-staff became 6 children, 1 parent and 6 BM25
    # rows, and its propositions read "The document indicates that there was an
    # error in finding the requested page". A ranking penalty can push that down
    # but cannot stop it being written, and it is the URL that a question about
    # faculty and staff matches on first.
    #
    # requests.raise_for_status() does not catch it: the server answers 200. That
    # is what a soft 404 is, and why the test has to look at the text.
    if _looks_dead(text or ""):
        logger.warning(
            f"Dead page, not ingesting ({len(text or '')} chars): {url}")
        return None, "dead"

    if not text or len(text) < 100:
        logger.warning(f"Page too short ({len(text or '')} chars): {url}")
        return None, "too_short"

    import config
    max_chars = getattr(config, "MAX_PAGE_CHARS", 12_000)
    if len(text) > max_chars:
        logger.warning(f"Page cut from {len(text):,} to {max_chars:,} chars: {url}")
        text = text[:max_chars]

    logger.info(f"✓ Scraped {url}: {len(text):,} chars")

    # Save to disk cache (text + pdf_links) so crawler pages skip instantly on retry
    try:
        _wc_data = {}
        if os.path.exists(_cache_file):
            with open(_cache_file, "r", encoding="utf-8") as fh:
                _wc_data = json.load(fh)
        # Capture PDF links from this page so they replay on cache hits
        discovered_pdfs = []
        try:
            discovered_pdfs = list(pdf_ingest.discover(html, url))
        except Exception:
            pass
        _wc_data[url] = {"text": text, "reason": "ok", "pdf_links": discovered_pdfs}
        os.makedirs(os.path.dirname(_cache_file), exist_ok=True)
        with open(_cache_file, "w", encoding="utf-8") as fh:
            json.dump(_wc_data, fh, ensure_ascii=False)
    except Exception:
        pass

    return text, "ok"


def scrape_page(url: str, session: requests.Session = None) -> str | None:
    """
    Scrapes one URL, returning its text or None.

    Thin view over fetch_page for callers that only need the text:
    fix_parent_store, setup_knowledge_base's --url path, and test.py. None means
    "do not ingest", so nothing downstream pays to embed it.
    """
    return fetch_page(url, session=session)[0]


# Markers that separate scraped HTML text from material appended after it.
# Both are emitted by this module: _extract_images_text writes the first,
# _extract_social_links the second.
#
# The OCR one is no longer spelled out here. Its delimiters and the only correct
# way to remove a block live in page_furniture, so the emitter, the hash below,
# and any future cleanup all read one definition. What used to sit here was a
# half-definition — the opening marker only — and every script that needed the
# closing one invented its own guess.
_SOCIAL_MARKER = "\n\nUniversity of Loralai Social Media Pages:"


def content_hash(text: str, url: str, with_image: bool = True) -> str:
    """The one place a page's change-detection hash is computed.

    Everything that decides "has this page changed since we indexed it?" must call
    this function, so the value compared is always the value stored.

    Returns the md5 of the HTML text only, with the OCR and social blocks stripped,
    so a global image or a footer icon set cannot dirty a page whose prose is
    untouched (one swapped image used to re-embed every page that carried it).

    `url` and `with_image` are no longer used. They stay so existing callers and
    tests keep working: the old homepage popup advertisement is gone from the new
    site, so there is no image to fingerprint any more.
    """
    html_only = page_furniture.strip_image_blocks(text).split(_SOCIAL_MARKER)[0]
    return hashlib.md5(
        html_only.encode("utf-8", errors="ignore")).hexdigest()[:8]


def legacy_content_hash(text: str) -> str:
    """The hash scheme that is already written into 3124 live Chroma rows.

    Kept ONLY so that switching to content_hash does not bill a full re-embed.
    Every stored page_hash today is md5 of the full text; if page_unchanged
    compared against content_hash alone, not one of them would match, all 78
    pages would look new, and the "cost saving" fix would itself trigger the most
    expensive possible run. page_unchanged therefore accepts either scheme.

    Safe to delete after the next full rebuild, at which point every stored hash
    is a content_hash. Nothing else should ever call this.
    """
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:8]



FURNITURE_CACHE = "./data/page_furniture.json"
CONTACT_LINES_CACHE = "./data/page_contact_lines.json"


def _save_lines(path: str, lines: set, what: str) -> None:
    """Write a measured line set to disk. Never fails a scrape over a cache."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(lines), f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.warning(f"Could not save {what} cache: {e}")


def _load_lines(path: str) -> set:
    """A line set from the last full run, or an empty set."""
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_furniture(furniture: set) -> None:
    """Remember the furniture set a full run measured.

    Frequency needs a corpus. setup_knowledge_base calls scrape_all_pages twice —
    Phase A over the ~78 GUARANTEED_PAGES, Phase B over whatever extra URLs the
    crawler found — and Phase B is usually far below MIN_PAGES_FOR_FREQUENCY. So
    is the single-page path, setup_knowledge_base.py --url. Without a cache those
    runs cannot strip anything and quietly re-admit the footer that Phase A just
    removed, which is how a corpus ends up half-clean.
    """
    _save_lines(FURNITURE_CACHE, furniture, "furniture")


def _load_furniture() -> set:
    """The furniture set from the last full run, or an empty set."""
    return _load_lines(FURNITURE_CACHE)


def _save_contact_lines(lines: set) -> None:
    """Remember the site-wide contact block a full run measured.

    Cached for the same reason furniture is, and it matters MORE here. Phase B
    runs on a handful of crawler-found pages, every one of them carrying the
    contact block, and none of them the contact page. Without this cache Phase B
    would re-admit on those pages exactly what Phase A removed from 64 others.
    """
    _save_lines(CONTACT_LINES_CACHE, lines, "contact-lines")


def _load_contact_lines() -> set:
    """The contact-block line set from the last full run, or an empty set."""
    return _load_lines(CONTACT_LINES_CACHE)


def scrape_all_pages(kb, urls: list = None,
                     include_guaranteed: bool = True) -> dict:
    """
    Main scraping orchestrator. Fetches in one pass, then ingests in a second.

    include_guaranteed=False scrapes ONLY the urls given. Used by
    setup_knowledge_base's Phase B, which passes the URLs its crawler discovered
    on top of the guaranteed list — and which, while this function unconditionally
    prepended GUARANTEED_PAGES, re-fetched all 78 guaranteed pages that Phase A
    had just fetched. The second fetch always ended in "unchanged", so it cost no
    embedding money, but it doubled the wall-clock and the image downloads of
    every full rebuild.

    WHY TWO PASSES
    ──────────────
    Three of the ingest-time rules cannot be decided from a single page, because
    what they test is whether a line belongs to the PAGE or to the SITE:

        page_furniture.find_furniture     "Scholarships", "Hostels", "Follow Us"
                                         and the empty "###" heading appear on 72
                                         of 78 pages. One line of markup becomes
                                         72 corpus chunks, and one of them was
                                         measured ranking 5th for "What
                                         departments does UoL have?".
        page_furniture.find_contentless   /alumni, /offices, three tender pages and
                                         /advertisement-for-visiting-faculty
                                         consist of nothing else. They produced 57
                                         propositions, all restating the footer.
        page_furniture.drop_orphan_stats  positional, so it does not strictly need
                                         the corpus — but it must run before any
                                         line is removed from around a counter
                                         number, so it belongs in the same place.

    The old single-pass loop fetched and ingested one page at a time, so at the
    moment it decided what to do with page 1 it had never seen page 2. None of
    these rules could be applied, and neither could be applied later for free:
    once a footer line is inside a chunk it has been embedded and paid for.

    Change detection is delegated to content_hash() and load_scraped_page(). See
    content_hash() for what went wrong when this function made that decision too.
    """
    import config

    results = {"scraped": 0, "failed": 0, "skipped": 0, "excluded": 0,
               "contentless": 0, "dead": 0, "empty": 0, "aliases": 0, "pages": [],
               "pdfs": 0, "pdfs_skipped": 0, "pdfs_failed": 0,
               "pdfs_refused": 0,
               "pdf_files": []}

    reset_pdf_links()

    base_urls = list(dict.fromkeys(GUARANTEED_PAGES)) if include_guaranteed else []
    if urls:
        for u in urls:
            if u not in base_urls and u.startswith("http"):
                base_urls.append(u)

    # Drop non-content URLs BEFORE fetching. The crawler in
    # setup_knowledge_base.step1_scrape_website walks up to 300 pages of a
    # WordPress site, which means it reaches /feed/, /tag/<x>/, /category/<x>/,
    # /author/admin/ and /page/<n>/ — every one of them a re-run of text already
    # indexed from the real page. Filtering here saves the fetch, the OCR and the
    # embedding spend; the same check is repeated at the ingest call below so a
    # URL added by any other path cannot slip through.
    kept = [u for u in base_urls if config.resolve_category(u) is not None]
    if len(kept) < len(base_urls):
        dropped = [u for u in base_urls if config.resolve_category(u) is None]
        results["excluded"] = len(dropped)
        logger.info(f"Excluded {len(dropped)} non-content URLs before fetching: "
                    f"{', '.join(dropped[:5])}{' ...' if len(dropped) > 5 else ''}")
    base_urls = kept

    logger.info(f"Total URLs to process: {len(base_urls)}")

    session = _make_session()
    visited_urls = set()

    def _fetch(url: str) -> tuple[str, str | None, str]:
        """Fetch one page. Returns (url, raw_text, reason).

        Fetching only. No hash, no category, no ingest — those all now happen in
        pass 2, where the whole run is visible.
        """
        if url in visited_urls:
            return url, None, "duplicate"
        visited_urls.add(url)

        text, reason = fetch_page(url, session=session)
        time.sleep(1.0)   # our rule: at least 1 second between pages
        return url, text, reason

    # ── pass 1: fetch everything ─────────────────────────────
    #
    # WORKER COUNT IS DERIVED FROM FREE RAM, NOT HARDCODED.
    #
    # _fetch_with_playwright launches a *fresh* headless Chromium per page
    # (see the `p.chromium.launch(...)` inside it), so N workers means N
    # simultaneous browsers. A Chromium with a rendered page resident costs
    # roughly 0.6 GB. A fixed max_workers=3 therefore needs ~1.8 GB free and
    # killed a run on 2026-08-28 on an 8 GB machine that had 0.5 GB free:
    # V8 raised "JavaScript heap out of memory", the Playwright driver
    # connection dropped mid-run, and because setup_knowledge_base.py --force
    # resets the stores *before* rebuilding, it left all three stores empty.
    # Three parallel browsers also made uoli.edu.pk answer 503.
    #
    # Deriving the count means the same code is safe on a small laptop and
    # still fast on a big box — no per-machine editing, and no repeat of the
    # empty-database failure mode.
    _BROWSER_RAM_GB = 0.6          # measured headroom needed per Chromium
    _RESERVE_RAM_GB = 0.8          # left for this process (Chroma + embeddings)
    try:
        import psutil
        _free_gb = psutil.virtual_memory().available / (1024 ** 3)
        _workers = 1   # Force 1 worker for stability on 8GB RAM machines
    except Exception:
        _workers = 1               # cannot measure → assume the worst
    logger.info(f"Fetch concurrency: {_workers} worker(s) "
                f"(each runs its own Chromium)")

    fetched: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_workers) as executor:
        futures = {executor.submit(_fetch, url): url for url in base_urls}

        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            url = futures[future]
            try:
                url, text, reason = future.result()
            except Exception as e:
                logger.error(f"Worker failed for {url}: {e}")
                results["failed"] += 1
                continue

            if reason == "duplicate":
                logger.info(f"[{i}/{len(base_urls)}] Duplicate URL in this run: {url}")
                results["skipped"] += 1
            elif text:
                fetched[url] = text
                logger.info(f"[{i}/{len(base_urls)}] Fetched {len(text):,} chars: {url}")
            elif reason == "dead":
                # A soft 404, already logged by fetch_page. Counted separately
                # from "failed" because it is the CORRECT outcome — the page has
                # nothing to give and refusing it is the fix, not a fault. Lumping
                # the two together made a perfectly healthy run report a failure.
                results["dead"] += 1
            elif reason == "empty":
                # A page with no content of its own yet. Not a fault either: it
                # is retried on the next run and stored once the site fills it.
                results["empty"] += 1
            else:
                # "unreachable" or "too_short" — fetch_page logged which.
                results["failed"] += 1

    if not fetched:
        logger.warning("Nothing was fetched — no ingest pass.")
        return results

    # ── between passes: what belongs to the site, not a page ──
    furniture = page_furniture.find_furniture(fetched)
    if furniture:
        _save_furniture(furniture)
        logger.info(f"Site furniture measured across {len(fetched)} pages: "
                    f"{len(furniture)} lines — {', '.join(sorted(furniture)[:6])}"
                    f"{' ...' if len(furniture) > 6 else ''}")
    else:
        # Too few pages to measure. Reuse the last full run's answer rather than
        # ingesting furniture that a previous run was careful to remove.
        furniture = _load_furniture()
        logger.info(
            f"Only {len(fetched)} page(s) — too few to measure frequency "
            f"(needs {page_furniture.MIN_PAGES_FOR_FREQUENCY}). "
            f"Using {len(furniture)} cached furniture lines from {FURNITURE_CACHE}.")

    # The site-wide contact block: the university's phone, email, domain and
    # address, repeated as chrome on every page. Kept on the contact page and
    # removed everywhere else, so the facts are stated once instead of 65 times.
    # Measured before this fix: 245 near-duplicate children, 8.1% of web chunks.
    contact_lines = page_furniture.find_site_contact_lines(
        fetched, university_name=config.UNIVERSITY_NAME)
    if contact_lines:
        _save_contact_lines(contact_lines)
        logger.info(f"Site-wide contact block: {len(contact_lines)} line(s), "
                    f"kept on the contact page and stripped from the rest — "
                    f"{', '.join(sorted(contact_lines)[:3])}"
                    f"{' ...' if len(contact_lines) > 3 else ''}")
    else:
        # Either too few pages to measure, or no contact page in this run. Both
        # are normal for Phase B, and both mean the last full run's answer is the
        # best available one. Without this, Phase B re-admits on its handful of
        # pages exactly what Phase A removed from sixty-four.
        contact_lines = _load_contact_lines()
        if contact_lines:
            logger.info(f"Using {len(contact_lines)} cached contact-block lines "
                        f"from {CONTACT_LINES_CACHE}.")

    contentless = page_furniture.find_contentless(fetched)
    if contentless:
        # Logged at WARNING, individually, and never silently. A page can be empty
        # because it IS empty or because _extract_text's selector list missed its
        # content — the second is a bug in this module, and dropping the page
        # quietly is exactly what would hide it.
        logger.warning(f"{len(contentless)} page(s) carry no content of their own "
                       f"and will NOT be ingested. If any of these has visible "
                       f"content on the website, the fault is in _extract_text:")
        for url in sorted(contentless):
            logger.warning(f"    no own content ({len(fetched[url])} chars): {url}")

    # Two URLs serving the same page. Decided here rather than by ingesting both
    # and relying on query-time dedup, because dedup runs at the END of retrieval:
    # by then the second copy has already taken a CANDIDATE_K slot from a distinct
    # document, and it has already been paid for. Measured on this site: 6 groups,
    # 144 of 5486 children.
    #
    # Computed after contentless removal on purpose. The three tender pages are
    # byte-identical to each other, so running this first would collapse them into
    # one page and then drop it, which reaches the same place by a route whose log
    # says the wrong thing.
    live = {u: t for u, t in fetched.items() if u not in contentless}
    aliases = page_furniture.find_alias_groups(live)
    alias_drop = {u for others in aliases.values() for u in others}
    if aliases:
        logger.info(f"{len(aliases)} page(s) are served at more than one URL; "
                    f"ingesting one copy of each:")
        for keep, others in sorted(aliases.items()):
            logger.info(f"    keeping {keep}")
            for u in sorted(others):
                logger.info(f"      alias of the above, skipped: {u}")

    # ── pass 2: clean, then ingest ───────────────────────────
    for n, (url, raw) in enumerate(fetched.items(), 1):
        if url in contentless:
            results["contentless"] += 1
            continue

        if url in alias_drop:
            results["aliases"] += 1
            continue

        # THE only category decision in the ingest path. None means the URL is
        # site machinery (feed, tag archive, gallery) that a crawl reaches but no
        # student ever asks about — do not ingest it, and do not silently file it
        # under "general" the way the old substring matcher did.
        category = config.resolve_category(url)
        if category is None:
            results["excluded"] += 1
            logger.info(f"[{n}/{len(fetched)}] Excluded (not content): {url}")
            continue

        # Orphan stats first — it decides per line from the line that FOLLOWS, so
        # it must see the text before strip_furniture removes anything.
        #
        # The contact page keeps the contact block; every other page loses it.
        # One set, one call: strip_furniture takes whatever it is given, so the
        # per-page decision is made HERE and there is no second stripping path
        # that could drift out of step with this one.
        strip_set = furniture
        if not page_furniture.is_contact_page(url):
            strip_set = furniture | contact_lines

        text = page_furniture.strip_furniture(
            page_furniture.drop_orphan_stats(raw), strip_set)

        if not text.strip():
            # Everything on the page was furniture, yet find_contentless did not
            # flag it. That means the two rules disagree, which should not happen
            # and is worth seeing rather than ingesting an empty page.
            results["contentless"] += 1
            logger.warning(f"[{n}/{len(fetched)}] Nothing left after stripping "
                           f"furniture, not ingesting: {url}")
            continue

        if len(text) < len(raw):
            logger.debug(f"[{n}/{len(fetched)}] Stripped "
                         f"{len(raw) - len(text):,} chars of furniture: {url}")

        # BOTH hashes are computed from the RAW text, deliberately.
        #
        # The hash answers "has the website changed?", and that is a fact about
        # the page as served, not about what we chose to keep from it. Hashing the
        # stripped text would also be expensive in a way that is easy to miss:
        # every one of the 3124 stored web chunks carries a hash of the raw full
        # text, so a stripped-text hash would match none of them, all 71 pages
        # would look new, and this change — made to STOP needless re-embedding —
        # would bill a complete re-index of the corpus.
        #
        # The consequence to be aware of: editing the furniture rules does NOT by
        # itself cause pages to be re-ingested, because their raw text has not
        # changed. That is the safe default. A deliberate re-clean needs
        # setup_knowledge_base.py --force.
        result = kb.load_scraped_page(
            text, url, category,
            page_hash=content_hash(raw, url),
            legacy_hash=legacy_content_hash(raw))

        # Count from the ingestion result, not from having reached this line. The
        # old code incremented "scraped" for every page it handed over, including
        # the ones load_scraped_page skipped as unchanged, so the summary reported
        # a full re-index on every run and there was no way to tell a real update
        # from a no-op.
        if result.status == "skipped":
            results["skipped"] += 1
            logger.info(f"[{n}/{len(fetched)}] Unchanged: {url}")
        elif result.status in ("added", "updated"):
            results["scraped"] += 1
            results["pages"].append(url)
            logger.info(f"[{n}/{len(fetched)}] "
                        f"{result.status.capitalize()} ({category}): {url}")
        else:
            results["failed"] += 1
            logger.warning(f"[{n}/{len(fetched)}] Ingest {result.status}: {url}")

    # ── pass 3: the documents the pages point at ─────────────
    #
    # Runs after the page loop, not inside it, so a PDF linked from six pages is
    # read once. It is also why discovery is recorded in fetch_page: by this point
    # the contentless and alias pages have been dropped, and those are exactly the
    # pages whose entire content is a link to a PDF.
    #
    # Each file's identity is the md5 of its BYTES, handed to load_scraped_page as
    # page_hash. That is what makes requirement 7 apply to documents as well as
    # pages: a notification the university has not touched is not re-read, not
    # re-OCR'd and not re-embedded, while one it has replaced is picked up
    # automatically with no manual download step.
    if _PDF_REFERRERS:
        logger.info(f"{len(_PDF_REFERRERS)} PDF document(s) referenced by the "
                    f"site — reading each once:")
    for n, pdf_url in enumerate(sorted(_PDF_REFERRERS), 1):
        total = len(_PDF_REFERRERS)
        category = _pdf_category(pdf_url, _PDF_REFERRERS[pdf_url])
        if category is None:
            # Procurement / tender documents — no student query is about these,
            # and they only dilute retrieval. Skip before download or OCR.
            results["pdfs_skipped"] += 1
            logger.info(f"[pdf {n}/{total}] Skipped (procurement/tender, no "
                        f"student query): {pdf_url}")
            continue
        try:
            doc = pdf_ingest.read_pdf(pdf_url, session=session)
        except Exception as exc:
            results["pdfs_failed"] += 1
            logger.warning(f"[pdf {n}/{total}] Failed to read {pdf_url}: "
                           f"{type(exc).__name__}: {exc}")
            continue

        if doc is None or not doc["text"].strip():
            # A document the purpose gate refused is not a failure — it is the
            # gate doing its job, and counting it as a failure would make every
            # scrape log show thirteen failures forever. See pdf_skipped.md for
            # what was refused and why.
            if pdf_ingest.was_refused(pdf_url):
                results["pdfs_refused"] += 1
                logger.info(f"[pdf {n}/{total}] Refused — not university "
                            f"information: {pdf_url}")
            else:
                results["pdfs_failed"] += 1
                logger.warning(f"[pdf {n}/{total}] No text recovered: {pdf_url}")
            continue

        result = kb.load_scraped_page(
            doc["text"], pdf_url, category,
            page_hash=doc["doc_hash"], source_type="pdf")

        if result.status == "skipped":
            results["pdfs_skipped"] += 1
            logger.info(f"[pdf {n}/{total}] Unchanged: {pdf_url}")
        elif result.status in ("added", "updated"):
            results["pdfs"] += 1
            results["pdf_files"].append(pdf_url)
            logger.info(
                f"[pdf {n}/{total}] {result.status.capitalize()} ({category}, "
                f"{doc['kind']}, {doc['pages']}pg, {doc['ocr_pages']} OCR'd, "
                f"{doc['tables']} table pages, {len(doc['text']):,} chars): "
                f"{pdf_url}")
        else:
            results["pdfs_failed"] += 1
            logger.warning(f"[pdf {n}/{total}] Ingest {result.status}: {pdf_url}")

    logger.info(
        f"Scraping complete — "
        f"New: {results['scraped']}, "
        f"Skipped: {results['skipped']}, "
        f"Excluded: {results['excluded']}, "
        f"Contentless: {results['contentless']}, "
        f"Alias copies: {results['aliases']}, "
        f"Dead: {results['dead']}, "
        f"Failed: {results['failed']} | "
        f"PDFs new: {results['pdfs']}, "
        f"unchanged: {results['pdfs_skipped']}, "
        f"refused: {results['pdfs_refused']}, "
        f"failed: {results['pdfs_failed']}"
    )
    return results


# python setup_knowledge_base.py python setup_propositions.py
#  uvicorn api:app --reload --port 8000
#  python -c "import valkey; valkey.Valkey(db=1).flushdb()"
