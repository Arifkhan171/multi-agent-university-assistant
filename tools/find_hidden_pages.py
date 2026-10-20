"""Find pages that only open by clicking a card or button (spec 02b).

Spec 02 reads <a href> links. Cards like "Read Article" or buttons like
"View All Events" move to a page with JavaScript and have no href. This tool
opens the hub (list) pages, clicks those elements and records where they go.

Read-only: it never edits project files and never stores page text. It only
writes review files in reports/new_urls/.
"""
import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

if __package__ in (None, ""):  # run as `python tools/find_hidden_pages.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from tools import build_url_lists as bl  # noqa: E402
from tools import find_site_urls as fs  # noqa: E402
from tools import url_health_check as uh  # noqa: E402

logger = logging.getLogger(__name__)

MAX_HUBS = 40
MAX_CLICKS = 400
ONLY_MAX_HUBS = 2          # --only: the named hub and one events page found from it
MAX_LOAD_MORE = 5          # a "Load more" button that never ends must not run forever
MAX_HUB_PATH_PARTS = 2
MIN_DELAY = 1.0            # spec: never faster than one action per second
DELAY_SECONDS = max(uh.DELAY_SECONDS, MIN_DELAY)
CLICK_WAIT_MS = 1500
MAX_CLICK_TEXT = 300       # a card is a title plus a sentence; longer text is a whole section
MARK = "data-hp-click"

# Words that make a button or card look like a "go to more content" action.
WANT_TEXT = re.compile(
    r"\b(read|view|see|more|all|load|next|details?|explore|events?|articles?)\b", re.I)
LOAD_MORE_TEXT = re.compile(r"\b(load|see|show|view)\s+more\b", re.I)
# Actions that send data, log in, or download: never clicked.
FORBIDDEN_TEXT = re.compile(
    r"log\s?in|sign\s?in|sign\s?up|apply|download|submit|register|subscribe|send", re.I)
# \bbot\b so "chat-bot" matches but "bottom" does not.
CHAT_WIDGET = re.compile(r"chat|assistant|\bbot\b|launcher", re.I)
# Carousel arrows only cost clicks. Real paging ("Next page", "Next", "Load more") must stay clickable.
CAROUSEL_TEXT = re.compile(r"\b(next|prev(ious)?)\s+slide\b|carousel|slider", re.I)
FORBIDDEN_LINEAGE ={"header", "nav", "footer", "form", "a"}
NOISE_LABELS = {"january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december"}

REVIEW_HEADER = ["address", "category", "keep", "note"]
LOG_HEADER = ["hub page", "what was clicked", "new address", "result"]

PAGE, PDF, EXTERNAL, NONE, CLICK_ERROR = "page", "pdf", "external", "none", "error"

COLLECT_JS = f"""
() => {{
  const out = [];
  let n = 0;
  const pointerOf = el => getComputedStyle(el).cursor === 'pointer';
  document.querySelectorAll('*').forEach(el => {{
    if (el.closest('a[href]')) return;
    const explicit = el.matches('button,[role=button],[onclick]');
    const pointer = pointerOf(el) && !(el.parentElement && pointerOf(el.parentElement));
    if (!explicit && !pointer) return;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (cs.visibility === 'hidden' || cs.display === 'none' || r.width < 5 || r.height < 5) return;
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '')
      .trim().replace(/\\s+/g, ' ');
    if (!text) return;
    const lineage = [];
    let ident = '';
    for (let p = el; p && p !== document.documentElement; p = p.parentElement) {{
      lineage.push(p.tagName.toLowerCase());
      ident += ' ' + (p.id || '') + ' ' + (p.getAttribute('class') || '') + ' ' + (p.getAttribute('aria-label') || '');
    }}
    el.setAttribute('{MARK}', String(n));
    out.push({{idx: n, text: text, tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
              lineage: lineage, ident: ident,
              top: Math.round((r.top + window.scrollY) / 20) * 20, left: Math.round(r.left / 20) * 20}});
    n += 1;
  }});
  return out;
}}
"""


# ── Step 1: hub pages ───────────────────────────────────────────────────────

def path_parts(address: str) -> list[str]:
    return [s for s in urlsplit(address).path.split("/") if s]


def is_news_article(address: str) -> bool:
    """/news/<slug>: one story, not a list. /news itself is the list."""
    parts = path_parts(address)
    return len(parts) == 2 and parts[0] == "news"


def is_events_list(address: str) -> bool:
    """/research/innovation/events, /campus-life/sports/events: pages that list event pages."""
    parts = path_parts(address)
    return bool(parts) and parts[-1] == "events"


def choose_hubs(rows: list[dict], limit: int | None = None) -> tuple[list[str], list[str]]:
    """Return (hubs to open, hubs left out by the 40-page cap).

    Short addresses first. News stories are not hubs. Events list pages are, even when deeper."""
    kept = [r["address"] for r in rows if r["keep"] == "yes"]
    hubs = [a for a in kept if len(path_parts(a)) <= MAX_HUB_PATH_PARTS and not is_news_article(a)]
    hubs.sort(key=lambda a: len(path_parts(a)))  # sort is stable: CSV order inside a depth
    hubs += [a for a in kept if is_events_list(a) and a not in hubs]
    left_out = hubs[MAX_HUBS:]
    chosen = hubs[:MAX_HUBS]
    return (chosen[:limit] if limit else chosen), left_out


# ── Step 2: what may be clicked ─────────────────────────────────────────────

@dataclass
class Clickable:
    idx: int
    text: str
    tag: str = "div"
    input_type: str = ""
    lineage: tuple[str, ...] = ()   # tag names from the element up to <body>
    ident: str = ""                 # ids, classes and labels of the element and its parents
    top: int = 0
    left: int = 0

    @property
    def key(self) -> str:
        """Same text at the same place = the same card (clicked once)."""
        return f"{self.text}@{self.top}@{self.left}"


def is_safe_target(item: Clickable) -> bool:
    """One place decides what is never clicked."""
    if set(item.lineage) & FORBIDDEN_LINEAGE or item.tag in ("input", "select", "textarea"):
        return False
    if item.input_type.lower() in ("submit", "reset", "password"):
        return False
    if CHAT_WIDGET.search(item.ident):
        return False
    if FORBIDDEN_TEXT.search(item.text):
        return False
    return 0 < len(item.text) <= MAX_CLICK_TEXT


def is_wanted(item: Clickable) -> bool:
    return (is_safe_target(item) and bool(WANT_TEXT.search(item.text))
            and not CAROUSEL_TEXT.search(item.text))


def pick_clickables(items: list[Clickable]) -> list[Clickable]:
    """Wanted items, each card once. 'Load more' buttons are handled separately (5-press limit)."""
    picked, seen = [], set()
    for item in items:
        if is_wanted(item) and not LOAD_MORE_TEXT.search(item.text) and item.key not in seen:
            seen.add(item.key)
            picked.append(item)
    return picked


def find_load_more(items: list[Clickable]) -> Clickable | None:
    for item in items:
        if is_safe_target(item) and LOAD_MORE_TEXT.search(item.text):
            return item
    return None


# ── Step 3: what a click did ────────────────────────────────────────────────

@dataclass
class ClickOutcome:
    kind: str          # page, pdf, external, none, error
    address: str = ""
    note: str = ""


def _outcome_for(url: str) -> ClickOutcome | None:
    """None when the address says nothing (blank tab, address with a query, image)."""
    if not url or url.startswith("about:"):
        return None
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if host and host != uh.DOMAIN:
        return ClickOutcome(EXTERNAL, url)
    cleaned = fs.clean_link(url, fs.SITE)
    if not cleaned:
        return None
    return ClickOutcome(PDF if cleaned[1] == "pdf" else PAGE, cleaned[0])


def classify_outcome(before: str, after: str, popup_urls: list[str],
                     download_urls: list[str]) -> ClickOutcome:
    """A download or PDF is a candidate and never fetched; a moved tab or a new tab is a page."""
    if download_urls:
        return ClickOutcome(PDF, uh.normalize(download_urls[0]), "download cancelled")
    moved = [after] if uh.normalize(after) != uh.normalize(before) else []
    for url in popup_urls + moved:
        outcome = _outcome_for(url)
        if outcome:
            return outcome
    return ClickOutcome(NONE)


# ── Step 4: the browser ─────────────────────────────────────────────────────

class ClickRenderer(fs.LinkRenderer):
    """LinkRenderer that can also scroll and click."""

    def scroll_bottom(self) -> None:
        page = self._page
        for _ in range(20):  # lazy lists load as we pass them; cap in case the page never ends
            at_bottom = page.evaluate(
                "() => { window.scrollBy(0, window.innerHeight);"
                " return window.scrollY + window.innerHeight >= document.body.scrollHeight - 2; }")
            page.wait_for_timeout(400)
            if at_bottom:
                break

    def page_links(self) -> list[tuple[str, str]]:
        raw = self._page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.href, (e.innerText || e.textContent || '').trim()])")
        return [tuple(x) for x in raw]

    def clickables(self) -> list[Clickable]:
        return [Clickable(d["idx"], d["text"], d["tag"], d["type"], tuple(d["lineage"]),
                          d["ident"], d["top"], d["left"]) for d in self._page.evaluate(COLLECT_JS)]

    def _find_again(self, item: Clickable) -> Clickable | None:
        """The page was redrawn since we listed it, so find the same card again."""
        fresh = self.clickables()
        same_text = [c for c in fresh if c.text == item.text]
        for candidate in same_text:
            if candidate.key == item.key:
                return candidate
        return same_text[0] if same_text else None

    def click(self, item: Clickable) -> ClickOutcome:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import TimeoutError as PwTimeout
        page = self._page
        fresh = self._find_again(item)
        if fresh is None:
            return ClickOutcome(CLICK_ERROR, "", "element not found again")
        before = page.url
        popups: list = []
        downloads: list = []
        # Real functions, not list.append: Playwright tags handlers with an attribute, which
        # built-in methods cannot hold. The same objects are needed for remove_listener().
        def on_popup(popup) -> None:
            popups.append(popup)

        def on_download(download) -> None:
            downloads.append(download)

        page.context.on("page", on_popup)
        page.on("download", on_download)
        try:
            page.locator(f'[{MARK}="{fresh.idx}"]').first.click(timeout=5000)
            page.wait_for_timeout(CLICK_WAIT_MS)
            return classify_outcome(before, page.url, self._close_popups(popups),
                                    self._cancel_downloads(downloads))
        except (PwError, PwTimeout) as exc:
            return ClickOutcome(CLICK_ERROR, "", str(exc)[:100])
        finally:
            page.context.remove_listener("page", on_popup)
            page.remove_listener("download", on_download)

    @staticmethod
    def _close_popups(popups: list) -> list[str]:
        urls = []
        for popup in popups:
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001 - a slow new tab still has its address
                pass
            urls.append(popup.url)
            popup.close()
        return urls

    @staticmethod
    def _cancel_downloads(downloads: list) -> list[str]:
        urls = []
        for download in downloads:
            urls.append(download.url)
            download.cancel()
        return urls

    def close_overlay(self) -> None:
        """A click that opened a dialog instead of a page: close it."""
        self._page.keyboard.press("Escape")

    def come_back(self, hub: str) -> None:
        """Go back; if that does not land on the hub, open the hub again."""
        try:
            self._page.go_back(wait_until="domcontentloaded", timeout=30000)
            self._page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001 - nothing to go back to is normal after a new tab
            pass
        if uh.normalize(self._page.url) != uh.normalize(hub):
            self.render(hub)


# ── Step 5: explore one hub ─────────────────────────────────────────────────

@dataclass
class LogRow:
    hub: str
    clicked: str
    address: str
    result: str

    def as_list(self) -> list[str]:
        return [self.hub, self.clicked, self.address, self.result]


@dataclass
class Budget:
    max_clicks: int = MAX_CLICKS
    used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_clicks

    def spend(self) -> None:
        self.used += 1


@dataclass
class HiddenResult:
    candidates: dict[str, tuple[str, str]] = field(default_factory=dict)  # address -> (hub, clicked)
    pdfs: dict[str, tuple[str, str]] = field(default_factory=dict)
    log: list[LogRow] = field(default_factory=list)
    counts: list[tuple[str, str, int]] = field(default_factory=list)      # hub, label, number
    failed_hubs: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hubs_done: int = 0


def find_counts(text: str) -> list[tuple[str, int]]:
    """Numbers the page prints about itself: '4 Official Events', '11 / Departments', 'Events (4)'."""
    found: list[tuple[str, int]] = []
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        pairs = []
        m = re.fullmatch(r"(\d{1,3})\+?\s+([A-Za-z][A-Za-z&' ]{2,40})", line)
        if m:
            pairs.append((m.group(2), int(m.group(1))))
        elif re.fullmatch(r"\d{1,3}\+?", line) and i + 1 < len(lines):
            nxt = lines[i + 1]
            if re.fullmatch(r"[A-Za-z][A-Za-z&' ]{2,40}", nxt):
                pairs.append((nxt, int(line.rstrip("+"))))
        for m in re.finditer(r"([A-Za-z][A-Za-z&' ]{2,30}?)\s*\((\d{1,3})\)", line):
            pairs.append((m.group(1), int(m.group(2))))
        for label, number in pairs:
            first_word = label.split()[0].lower()
            if first_word not in NOISE_LABELS and (label, number) not in found:
                found.append((label.strip(), number))
    return found


def record(result: HiddenResult, hub: str, clicked: str, outcome: ClickOutcome) -> None:
    """Every click becomes a log row; page and PDF addresses also become candidates."""
    label = {PAGE: "new page", PDF: "PDF candidate", EXTERNAL: "other website, ignored",
             NONE: "no change", CLICK_ERROR: f"click failed: {outcome.note}"}[outcome.kind]
    result.log.append(LogRow(hub, clicked, outcome.address, label))
    if outcome.kind == PAGE:
        result.candidates.setdefault(outcome.address, (hub, clicked))
    elif outcome.kind == PDF:
        result.pdfs.setdefault(outcome.address, (hub, clicked))


def restore_hub(browser, load_more_clicks: int, budget: Budget,
                sleep: Callable[[float], None]) -> None:
    """After coming back the list is short again: scroll and press 'Load more' as before."""
    browser.scroll_bottom()
    for _ in range(load_more_clicks):
        button = find_load_more(browser.clickables())
        if not button or budget.exhausted:
            break
        browser.click(button)
        budget.spend()
        sleep(DELAY_SECONDS)
        browser.scroll_bottom()


def expand_hub(browser, hub: str, result: HiddenResult, budget: Budget,
               sleep: Callable[[float], None]) -> int:
    """Press 'Load more' until it goes away (at most 5 times). Returns how many presses."""
    browser.scroll_bottom()
    presses = 0
    while presses < MAX_LOAD_MORE and not budget.exhausted:
        button = find_load_more(browser.clickables())
        if not button:
            break
        outcome = browser.click(button)
        budget.spend()
        presses += 1
        sleep(DELAY_SECONDS)
        if outcome.kind != NONE:  # it was a link to another page, not a loader
            record(result, hub, button.text, outcome)
            browser.come_back(hub)
            return presses - 1
        browser.scroll_bottom()
    return presses


def explore_hub(browser, hub: str, ctx: dict, result: HiddenResult, budget: Budget,
                sleep: Callable[[float], None]) -> None:
    view = fs.open_page(browser, hub, sleep)
    ctx["seen"].setdefault(fs.text_hash(view.text), uh.normalize(hub))
    result.counts += [(hub, label, number) for label, number in find_counts(view.text)]
    loads = expand_hub(browser, hub, result, budget, sleep)
    # Read the links after scrolling and 'Load more': the diagnostic on /research/innovation showed
    # real <a href> event links that exist only once the page has been scrolled.
    note = "link after Load more" if loads else "link after scrolling"
    for href, link_text in browser.page_links():
        cleaned = fs.clean_link(href, hub)
        if cleaned and cleaned[1] == "page":
            result.candidates.setdefault(cleaned[0], (hub, note))
        elif cleaned:
            result.pdfs.setdefault(cleaned[0], (hub, link_text))
    for item in pick_clickables(browser.clickables()):
        if budget.exhausted:
            break
        outcome = browser.click(item)
        budget.spend()
        record(result, hub, item.text, outcome)
        sleep(DELAY_SECONDS)
        if outcome.kind == NONE:
            browser.close_overlay()
        else:
            browser.come_back(hub)
            restore_hub(browser, loads, budget, sleep)


def _queue_found_events_pages(queue: list[str], opened: set[str], result: HiddenResult,
                              max_hubs: int) -> None:
    for address in list(result.candidates):
        if is_events_list(address) and uh.normalize(address) not in opened and len(queue) < max_hubs:
            queue.append(address)
            opened.add(uh.normalize(address))


def explore_all(browser, hubs: list[str], ctx: dict, sleep: Callable[[float], None] = time.sleep,
                budget: Budget | None = None, discover: bool = True,
                max_hubs: int = MAX_HUBS) -> HiddenResult:
    """Opens the hubs. With `discover`, an events list page found by a click is opened too,
    but only from the listed hubs (one level), so the crawl cannot wander."""
    result = HiddenResult()
    budget = budget or Budget()
    queue = list(hubs)
    opened = {uh.normalize(h) for h in queue}
    try:
        for position, hub in enumerate(queue):
            if budget.exhausted:
                result.warnings.append(f"Stopped at {budget.max_clicks} clicks; later hub pages were not opened.")
                break
            try:
                explore_hub(browser, hub, ctx, result, budget, sleep)
                result.hubs_done += 1
                print(f"[hub {result.hubs_done}/{len(queue)}] clicks used: {budget.used}  {hub}")
                if discover and position < len(hubs):
                    _queue_found_events_pages(queue, opened, result, max_hubs)
            except fs.Blocked as exc:
                result.failed_hubs.append((hub, f"blocked ({exc})"))
            except Exception as exc:  # noqa: BLE001 - one bad hub must not stop the crawl
                result.failed_hubs.append((hub, f"{type(exc).__name__}: {str(exc)[:100]}"))
            sleep(DELAY_SECONDS)
    except KeyboardInterrupt:
        result.warnings.append(f"Stopped early with Ctrl+C: {result.hubs_done} hub pages done.")
        print("Stopped. Saving what is done so far...")
    if budget.exhausted:
        result.warnings.append(f"Click limit of {budget.max_clicks} reached.")
    return result


# ── Step 6: check the new addresses ─────────────────────────────────────────

def suggest_category(address: str) -> tuple[str, str]:
    """(category, extra note). The category is only a suggestion for the human."""
    category = config.resolve_category(address)
    if category is None:
        return "general", "config.resolve_category skips this address"
    return category, ""


def _is_move(row: fs.PageRow) -> bool:
    # judge_new_page reports a moved address as DUPLICATE with this reason text.
    return row.result == fs.DUPLICATE and row.reason.startswith("address moves")


def verify_address(renderer, address: str, hub: str, known: set[str], ctx: dict,
                   sleep: Callable[[float], None]) -> tuple[str, fs.PageRow, str]:
    """Open one address. Returns (final address, its row, note). One hop for a moved address."""
    note = ""
    target = address
    for _ in range(2):
        row, _ = fs.visit(renderer, target, hub, False, ctx, sleep)
        sleep(DELAY_SECONDS)
        if not _is_move(row):
            return target, row, note
        cleaned = fs.clean_link(row.duplicate_of, fs.SITE)
        if not cleaned or cleaned[1] != "page" or config._url_path(cleaned[0]) in known:
            return target, row, note
        note = f"moved from {address}"
        target = cleaned[0]
    return target, row, note


def verify_candidates(renderer, result: HiddenResult, known: set[str], ctx: dict,
                      sleep: Callable[[float], None] = time.sleep) -> list[list[str]]:
    """Review rows [address, category, keep, note] for new addresses; the rest goes to the log."""
    review: list[list[str]] = []
    have = set(known)
    try:
        for address, (hub, clicked) in list(result.candidates.items()):
            if config._url_path(address) in have:
                result.log.append(LogRow(hub, clicked, address, "already in pages.csv"))
                continue
            final, row, note = verify_address(renderer, address, hub, have, ctx, sleep)
            if config._url_path(final) in have:
                result.log.append(LogRow(hub, clicked, final, f"already in pages.csv ({note or 'same address'})"))
                continue
            row_review = _review_row(final, row, note, hub, clicked, result)
            if row_review:
                have.add(config._url_path(final))
                review.append(row_review)
            print(f"[check] {row.result:<9} {final}")
    except KeyboardInterrupt:
        result.warnings.append("Stopped early with Ctrl+C while checking new addresses.")
    return review


def _review_row(final: str, row: fs.PageRow, note: str, hub: str, clicked: str,
                result: HiddenResult) -> list[str] | None:
    category, extra = suggest_category(final)
    parts = [p for p in (note, extra) if p]
    if row.result == fs.REAL:
        keep = "no" if extra else "yes"
        result.log.append(LogRow(hub, clicked, final, "REAL"))
        return [final, category, keep, "; ".join(parts)]
    if row.result == fs.THIN:
        result.log.append(LogRow(hub, clicked, final, f"THIN: {row.reason}"))
        return [final, category, "no", "; ".join(parts + [row.reason])]
    result.log.append(LogRow(hub, clicked, final, f"{row.result}: {row.reason}"))
    return None


# ── Step 7: reports ─────────────────────────────────────────────────────────

def count_check_rows(result: HiddenResult, known: set[str], review: list[list[str]]) -> list[list[str]]:
    """For each number the page prints: how many addresses we have under that hub."""
    paths = known | {config._url_path(r[0]) for r in review}
    rows = []
    for hub, label, number in result.counts:
        hub_path = config._url_path(hub)
        if hub_path == "/":
            have = "n/a (home page)"
        else:
            have = str(sum(1 for p in paths if p.startswith(hub_path + "/")))
        rows.append([hub, f"{number} {label}", have])
    return rows


def build_summary(result: HiddenResult, review: list[list[str]], known: set[str],
                  left_out: list[str], stamp: str) -> str:
    lines = [f"# Hidden pages — {stamp}", "",
             f"Hub pages opened: {result.hubs_done}. Clicks logged: {len(result.log)}. "
             f"New addresses for review: {len(review)} "
             f"({sum(1 for r in review if r[2] == 'yes')} keep=yes).", ""]
    lines += [f"> WARNING: {w}" for w in result.warnings] + [""] * bool(result.warnings)
    lines += ["## Count check (compare by hand on the site)", "",
              "| Hub page | Page says | Addresses we have under it |", "|---|---|---|"]
    lines += [f"| {h} | {t} | {n} |" for h, t, n in count_check_rows(result, known, review)]
    lines += ["", f"## PDF candidates ({len(result.pdfs)})", ""]
    lines += [f"- {a} (from {hub}: {clicked})" for a, (hub, clicked) in result.pdfs.items()]
    lines += ["", f"## Hub pages where opening failed ({len(result.failed_hubs)})", ""]
    lines += [f"- {h}: {why}" for h, why in result.failed_hubs]
    failed_clicks = [r for r in result.log if r.result.startswith("click failed")]
    lines += ["", f"## Clicks that failed ({len(failed_clicks)})", ""]
    lines += [f"- {r.hub}: '{r.clicked}' — {r.result}" for r in failed_clicks]
    lines += ["", f"## Hub pages left out by the {MAX_HUBS}-page cap ({len(left_out)})", ""]
    lines += [f"- {a}" for a in left_out]
    return "\n".join(lines) + "\n"


def write_reports(result: HiddenResult, review: list[list[str]], known: set[str], left_out: list[str],
                  out_dir: Path = fs.REPORT_DIR, tag: str = "") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    review_path = out_dir / f"hidden_pages_for_review{tag}.csv"
    log_path = out_dir / f"hidden_pages_log{tag}.csv"
    summary_path = out_dir / f"hidden_pages_summary{tag}.md"
    uh.write_csv(review_path, REVIEW_HEADER, review)
    uh.write_csv(log_path, LOG_HEADER, [r.as_list() for r in result.log])
    stamp = time.strftime("%Y-%m-%d")
    summary_path.write_text(build_summary(result, review, known, left_out, stamp), encoding="utf-8")
    return [review_path, log_path, summary_path]


# ── Step 8: run ─────────────────────────────────────────────────────────────

def run(limit: int | None, only: str | None = None) -> list[Path]:
    rows = bl.read_rows(bl.LIST_DIR / "pages.csv", bl.PAGE_COLUMNS)
    hubs, left_out = choose_hubs(rows, limit)
    if only:  # a small live test: this hub plus at most one events page found from it
        hubs, left_out = [uh.normalize(only)], []
        if not hubs[0].startswith(fs.SITE):
            raise SystemExit(f"--only must be an address on {fs.SITE}")
    known = {config._url_path(r["address"]) for r in rows}
    print(f"Hub pages to open: {len(hubs)} (left out by the cap: {len(left_out)})")
    with ClickRenderer() as renderer:
        signature, home_text, warn = uh.probe_not_found(renderer, fs.SITE)
        ctx = {"signature": signature, "home_text": home_text, "seen": {}}
        result = explore_all(renderer, hubs, ctx,
                             max_hubs=ONLY_MAX_HUBS if only else min(limit, MAX_HUBS) if limit else MAX_HUBS)
        if warn:
            result.warnings.append(warn)
        review = verify_candidates(renderer, result, known, ctx)
    # A limited test run gets its own file names so it never overwrites a full run.
    tag = "_only" if only else f"_limit{limit}" if limit else ""
    return write_reports(result, review, known, left_out, tag=tag)


def main() -> None:
    parser = argparse.ArgumentParser(description="Find pages that only open by clicking.")
    parser.add_argument("--limit", type=int, default=None, help="stop after N hub pages (small tests)")
    parser.add_argument("--only", metavar="ADDRESS", default=None,
                        help="open just this one hub page (small live test)")
    args = parser.parse_args()
    for path in run(args.limit, args.only):
        print("Wrote", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
