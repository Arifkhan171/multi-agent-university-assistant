"""Find every real page and PDF on uoli.edu.pk (spec 02).

Read-only tool: it never edits project files and never stores page text.
Only the title and the text length are kept; the text is hashed in memory
to spot pages that show the same content under different addresses.
"""
import argparse
import hashlib
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests

if __package__ in (None, ""):  # run as `python tools/find_site_urls.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import url_health_check as uh  # noqa: E402

logger = logging.getLogger(__name__)

REPORT_DIR = uh.ROOT / "reports" / "new_urls"
SITE = "https://" + uh.DOMAIN
MAX_DEPTH = 3
MAX_PAGES = 300
BLOCK_WAIT_SECONDS = 10   # spec: wait 10 s after 403/429, then retry once
BLOCKED_CODES = (403, 429)
# Downloads are not pages: the browser starts a download instead of showing content.
DOWNLOAD_EXT = (".xlsx", ".xls", ".csv", ".doc", ".docx", ".ppt", ".pptx", ".zip", ".rar")

REAL, NOT_FOUND, DUPLICATE, THIN, ERROR, SKIPPED = (
    "REAL", "NOT_FOUND", "DUPLICATE", "THIN", "ERROR", "SKIPPED")
REAL_PDF, BLOCKED, NOT_A_PDF = "REAL_PDF", "BLOCKED", "NOT_A_PDF"

# Words that appear in many addresses and say nothing about the topic.
FILLER_WORDS = {"of", "the", "and", "us", "office", "offices", "message", "page", "pages"}
MENU_WARNING = ("Links that only appear after a click are not seen by the crawl. "
                "Do the human check: compare the main menu and footer with new_pages.")

PAGE_HEADER = ["address", "section", "title", "text length", "found on",
               "in sitemap", "result", "duplicate of", "reason"]
PDF_HEADER = ["address", "found on", "link text", "size", "result", "reason"]
OLD_HEADER = ["old address", "likely new address", "why"]


class Blocked(Exception):
    """The site answered 403/429 twice."""


@dataclass
class PageView:
    final: str
    text: str
    title: str
    links: list[tuple[str, str]]   # (absolute href, link text)
    has_login: bool
    status: int


@dataclass
class PageRow:
    address: str
    section: str
    title: str
    text_length: int
    found_on: str
    in_sitemap: bool
    result: str
    duplicate_of: str
    reason: str

    def as_list(self) -> list:
        return [self.address, self.section, self.title, self.text_length, self.found_on,
                "yes" if self.in_sitemap else "no", self.result, self.duplicate_of, self.reason]


@dataclass
class PdfRow:
    address: str
    found_on: str
    link_text: str
    size: str
    result: str
    reason: str

    def as_list(self) -> list:
        return [self.address, self.found_on, self.link_text, self.size, self.result, self.reason]


# ── Step 1: reading a page in the browser ───────────────────────────────────

class LinkRenderer(uh.BrowserRenderer):
    """Browser that also reports every link on the page, even hidden menu links."""

    def render_page(self, url: str) -> PageView:
        statuses: list[int] = []

        def on_response(resp) -> None:
            if resp.request.is_navigation_request() and resp.frame == self._page.main_frame:
                statuses.append(resp.status)

        self._page.on("response", on_response)
        try:
            final, text = self.render(url)
        finally:
            self._page.remove_listener("response", on_response)
        links = self._page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.href, (e.innerText || e.textContent || '').trim()])")
        return PageView(final, text, self._page.title(), [tuple(x) for x in links],
                        self._page.locator("input[type=password]").count() > 0,
                        statuses[0] if statuses else 0)

    def get_text(self, url: str) -> str:
        """Download a text file (the sitemap) with the browser's identity."""
        resp = self._page.context.request.get(url, timeout=uh.READ_TIMEOUT * 1000)
        if resp.status >= 400:
            raise uh.FetchTimeout(f"HTTP {resp.status} for {url}")
        return resp.text()


# ── Step 2: which links we follow ───────────────────────────────────────────

def clean_link(href: str, base: str) -> tuple[str, str] | None:
    """Return (same-link key, 'page' or 'pdf'), or None when the link must be skipped."""
    href = href.strip()
    if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None
    parts = urlsplit(urljoin(base, href))
    host = parts.netloc.lower().removeprefix("www.")
    if parts.scheme not in ("http", "https") or host != uh.DOMAIN:
        return None
    if parts.query:  # page 2, filters: endless variations of the same page
        return None
    kind = uh.classify_type(parts.path)
    if kind == "image" or parts.path.lower().endswith(DOWNLOAD_EXT):
        return None
    return uh.normalize(f"https://{host}{parts.path}"), kind


def section_of(url: str) -> str:
    """First part of the path only. Categories are decided in config.py, not here."""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return segments[0] if segments else "(home)"


# ── Step 3: sitemap ─────────────────────────────────────────────────────────

def parse_sitemap(xml_text: str) -> tuple[list[str], bool]:
    """Return (all <loc> addresses, is_index). An index lists other sitemaps."""
    root = ET.fromstring(xml_text)
    locs = [el.text.strip() for el in root.iter() if el.tag.endswith("}loc") or el.tag == "loc"
            if el.text and el.text.strip()]
    return locs, root.tag.endswith("sitemapindex")


def read_sitemap(get_text: Callable[[str], str]) -> tuple[list[str], list[str]]:
    """Return (page and pdf addresses, warnings). A failure only means a warning."""
    warnings: list[str] = []
    pending, done, found = [f"{SITE}/sitemap.xml"], set(), []
    while pending and len(done) < 20:  # index files should be shallow; stop runaway chains
        url = pending.pop(0)
        done.add(url)
        try:
            locs, is_index = parse_sitemap(get_text(url))
        except Exception as exc:  # noqa: BLE001 - no sitemap must not stop the crawl
            warnings.append(f"Could not read {url} ({str(exc)[:100]}). Crawl starts from the home page only.")
            continue
        (pending if is_index else found).extend(locs)
    return found, warnings


# ── Step 4: judge one page ──────────────────────────────────────────────────

def text_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()


def judge_new_page(requested: str, final: str, text: str, signature: list[str],
                   home_text: str, seen: dict[str, str], has_login: bool) -> tuple[str, str, str]:
    """Return (result, duplicate_of, reason).

    The React site shows some page for any address, so "it opened" proves
    nothing: an address that lands somewhere else is never REAL."""
    flat = " ".join(text.split())
    if has_login:
        return SKIPPED, "", "login"
    if uh.looks_not_found(text, signature):
        return NOT_FOUND, "", 'page shows the site\'s "Not found" message'
    if uh.normalize(final) != uh.normalize(requested):
        return DUPLICATE, final, "address moves to a different page"
    is_home = urlsplit(uh.normalize(requested)).path == ""
    if not is_home and home_text and flat == " ".join(home_text.split()):
        return DUPLICATE, SITE, "shows exactly the same text as the home page"
    twin = seen.get(text_hash(text))
    if twin and twin != uh.normalize(requested):
        return DUPLICATE, twin, "shows exactly the same text as another address"
    if len(flat) < uh.MIN_TEXT_CHARS:
        return THIN, "", f"very little text ({len(flat)} characters)"
    return REAL, "", "has its own content"


def open_page(renderer, url: str, sleep: Callable[[float], None]) -> PageView:
    """403/429 means the site blocked us: wait, try once more, then give up."""
    view = uh.with_retries(renderer.render_page, url)
    if view.status in BLOCKED_CODES:
        sleep(BLOCK_WAIT_SECONDS)
        view = uh.with_retries(renderer.render_page, url)
        if view.status in BLOCKED_CODES:
            raise Blocked(f"HTTP {view.status}")
    return view


def error_row(url: str, found_on: str, in_sitemap: bool, reason: str) -> PageRow:
    return PageRow(url, section_of(url), "", 0, found_on, in_sitemap, ERROR, "", reason)


# ── Step 5: crawler ─────────────────────────────────────────────────────────

@dataclass
class CrawlResult:
    rows: list[PageRow]
    pdfs: dict[str, tuple[str, str]]   # address -> (found on, link text)
    warnings: list[str]


def _enqueue_links(view: PageView, url: str, depth: int, queued: set[str],
                   queue: deque, pdfs: dict[str, tuple[str, str]]) -> None:
    for href, link_text in view.links:
        cleaned = clean_link(href, url)
        if not cleaned:
            continue
        key, kind = cleaned
        if kind == "pdf":
            pdfs.setdefault(key, (url, link_text))
        elif key not in queued and depth < MAX_DEPTH:
            queued.add(key)
            queue.append((key, depth + 1, url))


def visit(renderer, url: str, found_on: str, in_sitemap: bool, ctx: dict,
          sleep: Callable[[float], None]) -> tuple[PageRow, PageView | None]:
    try:
        view = open_page(renderer, url, sleep)
    except Blocked as exc:
        return error_row(url, found_on, in_sitemap, f"site blocked us ({exc})"), None
    except uh.FetchTimeout as exc:
        return error_row(url, found_on, in_sitemap, f"timeout or network problem: {str(exc)[:100]}"), None
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the crawl
        return error_row(url, found_on, in_sitemap, f"unexpected problem: {str(exc)[:100]}"), None
    result, dup_of, reason = judge_new_page(url, view.final, view.text, ctx["signature"],
                                            ctx["home_text"], ctx["seen"], view.has_login)
    if result in (REAL, THIN):
        ctx["seen"].setdefault(text_hash(view.text), uh.normalize(url))
    row = PageRow(url, section_of(url), view.title.strip(), len(" ".join(view.text.split())),
                  found_on, in_sitemap, result, dup_of, reason)
    return row, view


def crawl(renderer, sitemap_urls: list[str], signature: list[str], home_text: str,
          limit: int | None = None, sleep: Callable[[float], None] = time.sleep) -> CrawlResult:
    """Breadth-first, one page at a time, never faster than DELAY_SECONDS."""
    cap = min(limit, MAX_PAGES) if limit else MAX_PAGES
    ctx = {"signature": signature, "home_text": home_text, "seen": {}}
    result = CrawlResult([], {}, [])
    in_sitemap: set[str] = set()
    queue: deque = deque()
    queued: set[str] = set()

    def push(key: str, depth: int, found_on: str) -> None:
        if key not in queued:
            queued.add(key)
            queue.append((key, depth, found_on))

    for raw in sitemap_urls:
        cleaned = clean_link(raw, SITE)
        if not cleaned:
            continue
        key, kind = cleaned
        if kind == "pdf":
            result.pdfs.setdefault(key, ("sitemap", ""))
        else:
            in_sitemap.add(key)
            push(key, 0, "sitemap")
    push(uh.normalize(SITE), 0, "start")
    try:
        while queue and len(result.rows) < cap:
            url, depth, found_on = queue.popleft()
            row, view = visit(renderer, url, found_on, url in in_sitemap, ctx, sleep)
            result.rows.append(row)
            print(f"[{len(result.rows)}] {row.result:<9} {url}")
            if view and row.result in (REAL, THIN):
                _enqueue_links(view, url, depth, queued, queue, result.pdfs)
            elif view and row.result == DUPLICATE and row.duplicate_of:
                cleaned = clean_link(row.duplicate_of, SITE)
                if cleaned and cleaned[1] == "page":
                    push(cleaned[0], depth, url)  # the real page behind it gets its own row
            sleep(uh.DELAY_SECONDS)
    except KeyboardInterrupt:
        result.warnings.append(f"Stopped early with Ctrl+C: {len(result.rows)} pages done.")
        print("Stopped. Saving what is done so far...")
    if queue and len(result.rows) >= cap:
        result.warnings.append(f"Stopped at {cap} pages; {len(queue)} addresses were not opened.")
    return result


# ── Step 6: PDFs ────────────────────────────────────────────────────────────

def pdf_size(url: str) -> str:
    """Size is a nice-to-have; a blocked or missing header just means 'unknown'."""
    try:
        resp = requests.head(url, headers={"User-Agent": uh.USER_AGENT}, allow_redirects=True, timeout=15)
        length = resp.headers.get("Content-Length", "")
        return f"{int(length) // 1024} KB" if length.isdigit() else "unknown"
    except requests.RequestException:
        return "unknown"


def check_pdf(url: str, found_on: str, link_text: str, renderer) -> PdfRow:
    def row(result: str, reason: str, size: str = "unknown") -> PdfRow:
        return PdfRow(url, found_on, link_text, size, result, reason)

    try:
        fetched = uh.fetch_unblocked(url)
        if fetched.status in BLOCKED_CODES:
            fetched, still_blocked = uh.retry_with_browser(url, "pdf", fetched, renderer)
            if still_blocked:
                return row(BLOCKED, f"site blocked us even in the browser (HTTP {fetched.status})")
        result, _, reason = uh.judge_binary(url, "pdf", fetched)
    except (uh.FetchTimeout, uh.RedirectLoop) as exc:
        return row(ERROR, f"timeout or network problem: {str(exc)[:100]}")
    if result == uh.DEAD:
        return row(NOT_A_PDF, reason)
    return row(REAL_PDF, reason, pdf_size(url))


def check_pdfs(pdfs: dict[str, tuple[str, str]], renderer, rows: list[PdfRow],
               sleep: Callable[[float], None] = time.sleep) -> None:
    """Appends to `rows` so a Ctrl+C keeps what is already checked."""
    for url, (found_on, link_text) in pdfs.items():
        rows.append(check_pdf(url, found_on, link_text, renderer))
        print(f"[pdf] {rows[-1].result:<9} {url}")
        sleep(uh.DELAY_SECONDS)


# ── Step 7: old address -> new address ──────────────────────────────────────

def topic_words(url: str) -> frozenset[str]:
    last = [s for s in urlsplit(url).path.split("/") if s][-1:]
    words = re.split(r"[^a-z0-9]+", last[0].lower()) if last else []
    return frozenset(w for w in words if w and w not in FILLER_WORDS)


def match_old_link(old_url: str, candidates: list[str]) -> tuple[str, str]:
    """Only suggest when the address words clearly match; a human decides in spec 03."""
    old_key = uh.normalize(old_url)
    if old_key in candidates:
        return old_key, "same address still exists"
    old_words = topic_words(old_key)
    if not old_words:
        return "", "no match found"
    scored = []
    for cand in sorted(candidates, key=lambda c: (len(c), c)):
        words = topic_words(cand)
        overlap = len(old_words & words) / len(old_words | words) if words else 0
        if overlap >= 0.5:
            scored.append((-overlap, cand, overlap))
    if not scored:
        return "", "no match found"
    best = min(scored)
    why = "same last words" if best[2] == 1 else f"{int(best[2] * 100)}% of the last words match"
    return best[1], why


def match_old_links(old_entries: list[dict], pages: list[PageRow], pdfs: list[PdfRow]) -> list[list[str]]:
    page_pool = [uh.normalize(r.address) for r in pages if r.result == REAL]
    pdf_pool = [uh.normalize(r.address) for r in pdfs if r.result == REAL_PDF]
    out = []
    for entry in old_entries:
        pool = {"page": page_pool, "pdf": pdf_pool}.get(entry["type"], [])
        new, why = match_old_link(entry["url"], pool)
        out.append([entry["url"], new or "no match found", why])
    return out


# ── Step 8: reports ─────────────────────────────────────────────────────────

def build_summary(pages: list[PageRow], pdfs: list[PdfRow], old_rows: list[list[str]],
                  warnings: list[str], stamp: str) -> str:
    lines = [f"# New site pages — {stamp}", "", f"Pages opened: {len(pages)}. PDFs found: {len(pdfs)}.", ""]
    for w in warnings + [MENU_WARNING]:
        lines += [f"> WARNING: {w}", ""]
    lines += ["## Pages", ""]
    lines += [f"- {res}: {sum(r.result == res for r in pages)}"
              for res in (REAL, NOT_FOUND, DUPLICATE, THIN, ERROR, SKIPPED)]
    lines += ["", "## PDFs", ""]
    lines += [f"- {res}: {sum(r.result == res for r in pdfs)}"
              for res in (REAL_PDF, BLOCKED, NOT_A_PDF, ERROR)]
    extra = [r.address for r in pages if r.result == REAL and not r.in_sitemap]
    lines += ["", f"## Real pages not in the sitemap ({len(extra)})", ""] + [f"- {a}" for a in extra]
    missing = [r[0] for r in old_rows if r[1] == "no match found"]
    lines += ["", f"## Old links with no match ({len(missing)} of {len(old_rows)})", ""]
    lines += [f"- {a}" for a in missing]
    return "\n".join(lines) + "\n"


def write_reports(pages: list[PageRow], pdfs: list[PdfRow], old_rows: list[list[str]],
                  warnings: list[str], out_dir: Path = REPORT_DIR, tag: str = "") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    jobs = [
        (f"new_pages_{stamp}{tag}.csv", PAGE_HEADER, [r.as_list() for r in pages]),
        (f"new_pdfs_{stamp}{tag}.csv", PDF_HEADER, [r.as_list() for r in pdfs]),
        (f"old_to_new_{stamp}{tag}.csv", OLD_HEADER, old_rows),
    ]
    written = []
    for name, header, rows in jobs:
        uh.write_csv(out_dir / name, header, rows)
        written.append(out_dir / name)
    summary = out_dir / f"summary_{stamp}{tag}.md"
    summary.write_text(build_summary(pages, pdfs, old_rows, warnings, stamp), encoding="utf-8")
    return written + [summary]


# ── Step 9: run ─────────────────────────────────────────────────────────────

def run(limit: int | None) -> list[Path]:
    warnings: list[str] = []
    pdf_rows: list[PdfRow] = []
    crawled = CrawlResult([], {}, [])
    with LinkRenderer() as renderer:
        signature, home_text, warn = uh.probe_not_found(renderer, SITE)
        if warn:
            warnings.append(warn)
        sitemap_urls, sitemap_warnings = read_sitemap(renderer.get_text)
        warnings += sitemap_warnings
        print(f"Sitemap addresses: {len(sitemap_urls)}")
        crawled = crawl(renderer, sitemap_urls, signature, home_text, limit)
        try:
            check_pdfs(crawled.pdfs, renderer, pdf_rows)
        except KeyboardInterrupt:
            warnings.append(f"Stopped early with Ctrl+C: {len(pdf_rows)} of {len(crawled.pdfs)} PDFs checked.")
    old_entries, _ = uh.collect_links()
    old_rows = match_old_links(old_entries, crawled.rows, pdf_rows)
    # A limited test run gets its own file names so it never overwrites a full run.
    return write_reports(crawled.rows, pdf_rows, old_rows, warnings + crawled.warnings,
                         tag=f"_limit{limit}" if limit else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find every real page and PDF on uoli.edu.pk.")
    parser.add_argument("--limit", type=int, default=None, help="stop after N pages (small tests)")
    args = parser.parse_args()
    for path in run(args.limit):
        print("Wrote", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
