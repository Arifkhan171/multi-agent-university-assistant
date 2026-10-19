"""URL health check for every uoli.edu.pk link written in our .py files.

Read-only tool: it never edits project files and never touches the database.
Pages, PDFs and images get their own report files so they never get mixed up.
"""
import argparse
import csv
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "url_health"
SKIP_DIRS = {"data", ".claude", "tests", "tools", "reports", "__pycache__",
             "venv", ".venv", "env", ".git", "node_modules"}
DOMAIN = "uoli.edu.pk"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")
USER_AGENT = "Mozilla/5.0 (UOLI url health check)"
DELAY_SECONDS = 2.0      # spec says at least 1s; 2s because the site started blocking us at 1s
CONNECT_TIMEOUT, READ_TIMEOUT = 15, 60  # a slow answer is not a dead link
MIN_TEXT_CHARS = 200     # below this a page is "very little text"
TYPES = ("page", "pdf", "image")

WORKING, REDIRECTED, DEAD, REVIEW, ERROR = (
    "WORKING", "REDIRECTED", "DEAD", "NEEDS_REVIEW", "ERROR")

# Any text that contains the domain; classified afterwards.
_MENTION_RE = re.compile(r"[\w.@/:-]*uoli\.edu\.pk[^\s\\\"'<>)\]},;|`]*", re.I)


class FetchTimeout(Exception):
    """Network or browser timeout."""


class RedirectLoop(Exception):
    """Too many redirects."""


@dataclass
class Mention:
    text: str
    file: str
    line: int


@dataclass
class Fetched:
    status: int
    final_url: str
    content_type: str
    head: bytes


@dataclass
class Row:
    link: str
    found: str
    type: str
    final: str
    result: str
    reason: str


# ── Step 1: find links in code ──────────────────────────────────────────────

def iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        yield path


def classify_mention(raw: str) -> tuple[str | None, str]:
    """Return (clean_url_or_None, reason_if_ignored)."""
    text = raw.rstrip(".:")
    if "@" in text:
        return None, "email address"
    if not re.match(r"(https?://|www\.)", text, re.I) and "/" not in text:
        return None, "domain name only (not a link)"
    if not re.match(r"(https?://|www\.|uoli\.edu\.pk)", text, re.I):
        return None, "part of another address (e.g. a sub-domain)"
    if re.search(r"[{}*]|\.\.\.|%s", text):
        return None, "pattern or placeholder, not a real link"
    return text, ""


def find_mentions(text: str, relpath: str) -> tuple[list[Mention], list[tuple[Mention, str]]]:
    links, ignored = [], []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _MENTION_RE.finditer(line):
            mention = Mention(m.group(0), relpath, lineno)
            url, reason = classify_mention(m.group(0))
            if url:
                mention.text = url
                links.append(mention)
            else:
                ignored.append((mention, reason))
    return links, ignored


def normalize(url: str) -> str:
    """Same-link key: ignores http/https, www., trailing slash, #fragment."""
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = "?" + parts.query if parts.query else ""
    return "https://" + host + path + query


def full_url(url: str) -> str:
    """The address we actually open (adds https:// when the code left it out)."""
    return url if re.match(r"https?://", url, re.I) else "https://" + url


def classify_type(url: str) -> str:
    path = urlsplit(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(IMAGE_EXT):
        return "image"
    return "page"


def collect_links(root: Path = ROOT):
    """Return (unique links in file order, ignored mentions)."""
    unique: dict[str, dict] = {}
    ignored_all: list[tuple[Mention, str]] = []
    for path in iter_py_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        links, ignored = find_mentions(text, rel)
        ignored_all.extend(ignored)
        for mention in links:
            key = normalize(mention.text)
            entry = unique.setdefault(key, {
                "url": full_url(mention.text), "type": classify_type(mention.text), "found": []})
            place = f"{mention.file}:{mention.line}"
            if place not in entry["found"]:  # same line can hold the link twice
                entry["found"].append(place)
    return list(unique.values()), ignored_all


# ── Step 2: network helpers ─────────────────────────────────────────────────

def fetch(url: str) -> Fetched:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                            allow_redirects=True, stream=True)
    except requests.exceptions.TooManyRedirects as exc:
        raise RedirectLoop(str(exc)) from exc
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        raise FetchTimeout(str(exc)) from exc
    try:
        head = next(resp.iter_content(chunk_size=2048), b"")
    finally:
        resp.close()
    return Fetched(resp.status_code, resp.url, resp.headers.get("Content-Type", ""), head)


class BrowserRenderer:
    """Opens pages in a real browser and returns what a visitor would see."""

    def __init__(self) -> None:
        self._pw = self._browser = self._page = None

    def __enter__(self) -> "BrowserRenderer":
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page(user_agent=USER_AGENT)
        return self

    def __exit__(self, *exc) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def fetch(self, url: str) -> Fetched:
        """Download a file with the browser's own identity (the site blocks plain requests)."""
        from playwright.sync_api import Error as PwError
        try:
            resp = self._page.context.request.get(url, timeout=READ_TIMEOUT * 1000)
            return Fetched(resp.status, resp.url, resp.headers.get("content-type", ""), resp.body()[:2048])
        except PwError as exc:
            raise FetchTimeout(str(exc)) from exc

    def render(self, url: str) -> tuple[str, str]:
        from playwright.sync_api import TimeoutError as PwTimeout
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=10000)
            except PwTimeout:
                pass  # some pages poll forever; what is visible so far is enough
            self._page.wait_for_timeout(1000)  # let a client-side redirect finish
            return self._page.url, self._page.inner_text("body")
        except PwTimeout as exc:
            raise FetchTimeout(str(exc)) from exc


def with_retries(fn, url: str, tries: int = 3):
    """Timeouts get 2 more tries (spec), then the error is raised."""
    for attempt in range(tries):
        try:
            return fn(url)
        except FetchTimeout:
            if attempt == tries - 1:
                raise
            time.sleep(2)


# ── Step 3: learn what "Not found" looks like ───────────────────────────────

def _lines(text: str) -> list[str]:
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]


def learn_not_found(fake1: str, fake2: str, home: str) -> list[str]:
    """Lines shown on both fake pages but not on the home page = the 'Not found' message."""
    home_lines, second = set(_lines(home)), set(_lines(fake2))
    return [l for l in _lines(fake1) if l in second and l not in home_lines]


def looks_not_found(text: str, signature: list[str]) -> bool:
    flat = " ".join(text.split())
    if signature and all(l in flat for l in signature):
        return True
    return len(flat) < 400 and bool(re.search(r"page not found|\b404\b|not found", flat, re.I))


def probe_not_found(renderer, home_url: str) -> tuple[list[str], str, str | None]:
    """Return (signature, home_text, warning). Never raises: failure only means a warning."""
    token = f"{random.randrange(10**8):08d}"
    try:
        _, home = with_retries(renderer.render, home_url)
        _, fake1 = with_retries(renderer.render, f"{home_url}/zz-fake-page-{token}")
        _, fake2 = with_retries(renderer.render, f"{home_url}/zz-fake-section-{token}/nothing-here")
    except Exception as exc:  # noqa: BLE001 - any failure just disables learning
        return [], "", f"Could not learn the site's 'Not found' page ({exc}). Results may be less reliable."
    signature = learn_not_found(fake1, fake2, home)
    if not signature:
        return [], home, ("The fake pages looked the same as the home page, so the 'Not found' "
                          "page could not be learned. Only simple checks were used.")
    return signature, home, None


# ── Step 4: judge one link ──────────────────────────────────────────────────

def judge_binary(link: str, kind: str, fetched: Fetched) -> tuple[str, str, str]:
    """PDF and image links: the file itself must come back, not a web page."""
    final = fetched.final_url
    ctype = fetched.content_type.lower()
    if fetched.status >= 400:
        return DEAD, final, f"HTTP error {fetched.status}"
    ok = ("pdf" in ctype or fetched.head.startswith(b"%PDF")) if kind == "pdf" else ctype.startswith("image/")
    if not ok:
        return DEAD, final, f"{kind.upper()} link opens a web page instead of a file ({ctype or 'unknown type'})"
    if normalize(final) != normalize(link):
        return REDIRECTED, final, "file moved to a new address"
    return WORKING, final, f"file found ({ctype.split(';')[0]})"


def judge_page(link: str, status: int, final: str, text: str,
               signature: list[str], home_text: str) -> tuple[str, str, str]:
    """Decide the result for a page, given what the browser showed."""
    flat = " ".join(text.split())
    is_home_link = urlsplit(normalize(link)).path == ""
    if looks_not_found(text, signature):
        return DEAD, final, 'page shows the site\'s "Not found" message'
    moved = normalize(final) != normalize(link)
    if not is_home_link and urlsplit(normalize(final)).path == "":
        return REVIEW, final, "redirects to the home page"
    if status >= 400:
        if len(flat) < MIN_TEXT_CHARS:
            return DEAD, final, f"HTTP error {status}"
        # The React site answers 404 to a plain request, then its own router
        # moves the browser to the new address; a visitor still gets the page.
        if moved:
            return REDIRECTED, final, f"server says HTTP {status}, but the browser moves to a new address with content"
        return REVIEW, final, f"server says HTTP {status} but the browser shows content"
    if not is_home_link and home_text and flat == " ".join(home_text.split()):
        return REVIEW, final, "shows exactly the same text as the home page"
    if len(flat) < MIN_TEXT_CHARS:
        return REVIEW, final, f"very little text ({len(flat)} characters). Page says: {flat[:100]!r}"
    if normalize(final) != normalize(link):
        return REDIRECTED, final, "page moved to a new address"
    return WORKING, final, "opens with real content"


def fetch_unblocked(url: str) -> Fetched:
    """403/429 means the site blocked us: wait, try once more."""
    fetched = with_retries(fetch, url)
    if fetched.status in (403, 429):
        time.sleep(10)
        fetched = with_retries(fetch, url)
    return fetched


def retry_with_browser(url: str, kind: str, fetched: Fetched, renderer) -> tuple[Fetched, bool]:
    """Plain requests got blocked: pages are judged by what the browser shows,
    files are downloaded by the browser. Returns (result, still_blocked)."""
    if kind == "page":
        return Fetched(200, url, "text/html", b""), False
    again = with_retries(renderer.fetch, url)
    return again, again.status in (403, 429)


def check_link(entry: dict, renderer, signature: list[str], home_text: str) -> Row:
    url, kind = entry["url"], entry["type"]
    found = "; ".join(entry["found"])

    def row(result: str, final: str, reason: str) -> Row:
        return Row(url, found, kind, final, result, reason)

    try:
        fetched = fetch_unblocked(url)
        if fetched.status in (403, 429):
            fetched, blocked = retry_with_browser(url, kind, fetched, renderer)
            if blocked:
                return row(REVIEW, fetched.final_url, f"site blocked us even in the browser (HTTP {fetched.status})")
        if kind != "page":
            return row(*judge_binary(url, kind, fetched))
        final, text = with_retries(renderer.render, url)
        return row(*judge_page(url, fetched.status, final, text, signature, home_text))
    except RedirectLoop:
        return row(DEAD, "", "redirect loop")
    except FetchTimeout as exc:
        return row(ERROR, "", f"timeout or network problem: {str(exc)[:120]}")
    except Exception as exc:  # noqa: BLE001 - one bad link must not stop the run
        if "Download is starting" in str(exc):
            return row(WORKING, url, "link downloads a file")
        return row(ERROR, "", f"unexpected problem: {str(exc)[:120]}")


# ── Step 5: reports ─────────────────────────────────────────────────────────

CSV_HEADER = ["link", "where found", "type", "final address", "result", "reason"]


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def build_summary(rows: list[Row], ignored_count: int, warnings: list[str], stamp: str) -> str:
    lines = [f"# URL health check — {stamp}", "",
             f"Links checked: {len(rows)}. Ignored mentions (emails etc.): {ignored_count} "
             f"(see the `_ignored.csv` file).", ""]
    for w in warnings:
        lines += [f"> WARNING: {w}", ""]
    for kind in TYPES:
        part = [r for r in rows if r.type == kind]
        if not part:
            continue
        lines += [f"## {kind.upper()} links ({len(part)})", ""]
        for res in (WORKING, REDIRECTED, DEAD, REVIEW, ERROR):
            lines.append(f"- {res}: {sum(r.result == res for r in part)}")
        lines.append("")
        lines += _detail_lists(part)
    return "\n".join(lines) + "\n"


def _detail_lists(part: list[Row]) -> list[str]:
    out = []
    for res, title in ((DEAD, "Dead"), (REDIRECTED, "Redirected (old -> new)"),
                       (REVIEW, "Needs review"), (ERROR, "Errors")):
        items = [r for r in part if r.result == res]
        if not items:
            continue
        out += [f"### {title}", ""]
        for r in items:
            arrow = f"{r.link} -> {r.final}" if res == REDIRECTED else r.link
            out.append(f"- {arrow} — {r.reason} ({r.found})")
        out.append("")
    return out


def write_reports(rows: list[Row], ignored, warnings: list[str], out_dir: Path = REPORT_DIR,
                  tag: str = "") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    written = []
    for kind in TYPES:
        part = [r for r in rows if r.type == kind]
        if part:
            path = out_dir / f"url_health_{stamp}_{kind}s.csv"
            write_csv(path, CSV_HEADER, [[r.link, r.found, r.type, r.final, r.result, r.reason] for r in part])
            written.append(path)
    ignored_path = out_dir / f"url_health_{stamp}_ignored{tag}.csv"
    write_csv(ignored_path, ["text found", "file", "line", "why ignored"],
              [[m.text, m.file, m.line, why] for m, why in ignored])
    summary_path = out_dir / f"url_health_{stamp}_summary{tag}.md"
    summary_path.write_text(build_summary(rows, len(ignored), warnings, stamp), encoding="utf-8")
    return written + [ignored_path, summary_path]


# ── Step 6: run ─────────────────────────────────────────────────────────────

def run(limit: int | None, kind: str) -> list[Path]:
    entries, ignored = collect_links()
    if kind != "all":
        entries = [e for e in entries if e["type"] == kind]
    if limit:
        entries = entries[:limit]
    print(f"Links to check: {len(entries)} | ignored mentions: {len(ignored)}")

    rows: list[Row] = []
    warnings: list[str] = []
    try:
        with BrowserRenderer() as renderer:
            signature, home_text, warn = probe_not_found(renderer, "https://" + DOMAIN)
            if warn:
                warnings.append(warn)
            for i, entry in enumerate(entries, 1):
                rows.append(check_link(entry, renderer, signature, home_text))
                print(f"[{i}/{len(entries)}] {rows[-1].result:<12} {entry['url']}")
                time.sleep(DELAY_SECONDS)
    except KeyboardInterrupt:
        warnings.append(f"Stopped early with Ctrl+C: {len(rows)} of {len(entries)} links done.")
        print("Stopped. Saving what is done so far...")
    # A partial run gets its own summary name so it never overwrites the full one.
    return write_reports(rows, ignored, warnings, tag="" if kind == "all" else f"_{kind}_only")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check every uoli.edu.pk link in our .py files.")
    parser.add_argument("--limit", type=int, default=None, help="check only the first N links")
    parser.add_argument("--type", choices=["page", "pdf", "image", "all"], default="all")
    args = parser.parse_args()
    for path in run(args.limit, args.type):
        print("Wrote", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
