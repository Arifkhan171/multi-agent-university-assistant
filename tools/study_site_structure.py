"""Study the structure of the new React website (spec 04).

Opens 25 sample pages, one browser, one page at a time, and records where the real
content is, which parts are furniture (header, footer, menu, chat widget ...), and
what the OLD web_scraper._extract_text would store today. It also saves each loaded
page as a small offline HTML file for the tests of later specs.

It changes no existing file, stores nothing in the database and runs no OCR.
Reports go to reports/site_structure/, fixtures to tests/fixtures/site/.
"""
import argparse
import csv
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

if __package__ in (None, ""):  # run as `python tools/study_site_structure.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import page_furniture  # noqa: E402
from tools import find_hidden_pages as fh  # noqa: E402
from tools import find_site_urls as fs  # noqa: E402
from tools import url_health_check as uh  # noqa: E402

logger = logging.getLogger(__name__)

SITE = fs.SITE
REPORT_DIR = uh.ROOT / "reports" / "site_structure"
FIXTURE_DIR = uh.ROOT / "tests" / "fixtures" / "site"
PAGES_CSV = uh.ROOT / "url_lists" / "pages.csv"

DELAY_SECONDS = max(fh.DELAY_SECONDS, 1.0)   # spec: never below 1 second between pages
STABLE_MAX_SECONDS = 30                      # spec: slow page -> use what is there
STABLE_POLLS = 3                             # same text length 3 times in a row = stable
POLL_MS = 500
MAX_FIXTURE_BYTES = 500 * 1024
MAX_FIXTURES_TOTAL = 6 * 1024 * 1024
MAX_SHRINK_ROUNDS = 5
REPEAT_FRACTION = 0.5                        # "lines on at least half of the pages"
MIN_LOST_LINE_CHARS = 15
SAME_WORDS = 0.8                             # a line counts as present if 80% of its words are
GARBAGE_WORDS = 0.5                          # an old-output line is garbage below 50% overlap
PARSER = "html.parser"                       # same parser web_scraper.fetch_page uses

SAMPLE_PAGES: list[tuple[str, str]] = [
    ("", "home"),
    ("/about", "about"),
    ("/about/vc-message", "message"),
    ("/about/leadership", "leaders"),
    ("/admissions/fees", "fee table"),
    ("/admissions/eligibility", "long text"),
    ("/admissions/how-to-apply", "steps"),
    ("/services/scholarships", "steps"),
    ("/admissions/faq", "questions and answers"),
    ("/academics/programs", "program list"),
    ("/academics/programs/bscs", "program page"),
    ("/academics/programs/bs-economics", "empty program page"),
    ("/academics/departments", "department list"),
    ("/academics/departments/computer-science", "department page"),
    ("/academics/faculty", "faculty list"),
    ("/academics/undergraduate", "credit-hour tables"),
    ("/offices/registrar", "office"),
    ("/contact", "contact"),
    ("/news", "news list"),
    ("/news/news-0", "news article"),
    ("/research/innovation/events/three-days-olive-value-addition-olive-soap-activity-completed-successfully",
     "event page"),
    ("/campus-life/sports/events", "events list"),
    ("/services/student-resources", "pdf list"),
    ("/services/library", "library"),
    ("/privacy-policy", "privacy"),
]

KNOWN_LINES = [  # (name, test on a normalised repeated line)
    ("Skip to main content", lambda k: "skip to main content" in k),
    ("phone number in the header", lambda k: bool(re.search(r"\+?\d[\d\s()\-]{7,}\d", k))),
    ("email in the header", lambda k: "@" in k),
    ("Online Admissions", lambda k: "online admissions" in k),
    ("UOLI AI Assistant", lambda k: "uoli ai assistant" in k),
    ("Developed by", lambda k: "developed by" in k),
]

FURNITURE_KINDS = ["header", "footer", "menu", "breadcrumb", "widget", "popup",
                   "back_to_top", "cookie", "skip_link"]
LANDMARKS = ["header", "nav", "main", "footer", "aside", "[role=main]"]
CSV_HEADER = ["address", "type", "status", "load seconds", "more text after scroll",
              "header", "nav", "main", "footer", "aside", "content selector",
              "chars in content", "chars outside content", "old output chars",
              "garbage %", "lost content", "lost lines", "tables", "cards",
              "hidden text chars", "counters", "carousel copies", "images with text",
              "chat widget", "own content", "fixture KB", "note"]

_BARE_NUMBER = re.compile(r"^[\d,]+\s*[%+]?$")
_BASE64 = re.compile(r"data:[^\"')\s,]*;base64,[A-Za-z0-9+/=]+")


# ── Step 1: the old extractor, loaded safely ────────────────────────────────

def load_old_extractor() -> Callable:
    """Return web_scraper._extract_text with OCR switched off.

    Importing web_scraper builds an EasyOCR reader (slow, may download a model) and
    _extract_text would OCR page images and write data/ocr_cache.json. Python cannot
    import one function alone, so easyocr is hidden for the import only.
    """
    had_it = "easyocr" in sys.modules
    previous = sys.modules.get("easyocr")
    sys.modules["easyocr"] = None   # makes `import easyocr` raise ImportError
    try:
        import web_scraper
    finally:
        if had_it:
            sys.modules["easyocr"] = previous
        else:
            sys.modules.pop("easyocr", None)
    if web_scraper.OCR_AVAILABLE:
        raise RuntimeError("web_scraper was already imported with OCR on; "
                           "start a fresh Python process for this tool.")
    return web_scraper._extract_text


# ── Step 2: the browser ─────────────────────────────────────────────────────

MEASURE_JS = r"""
(args) => {
  window.scrollTo(0, 0);
  const chatRe = new RegExp(args.chat, 'i');
  const cut = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const txt = el => (el.innerText || '').trim();
  const cls = el => (typeof el.className === 'string' ? el.className : (el.getAttribute('class') || ''));
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 4 && r.height > 4 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const sel = el => { let s = el.tagName.toLowerCase(); if (el.id) s += '#' + el.id;
    return s + cls(el).trim().split(/\s+/).filter(Boolean).slice(0, 2).map(x => '.' + x).join(''); };
  const hash = s => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return String(h); };
  const ident = el => (el.id || '') + ' ' + cls(el) + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '');
  const fixed = el => { for (let p = el; p && p !== document.body; p = p.parentElement)
    if (getComputedStyle(p).position === 'fixed') return true; return false; };
  const outermost = list => { const set = new Set(list); return list.filter(el => !set.has(el.parentElement)); };
  const bodyText = document.body.innerText || '';
  const all = [...document.querySelectorAll('body *')];

  const landmarks = {};
  for (const q of args.landmarks) { const els = [...document.querySelectorAll(q)];
    landmarks[q] = {n: els.length, selector: els.length ? sel(els[0]) : '', chars: els.length ? txt(els[0]).length : 0}; }

  let content = document.querySelector('main') || document.querySelector('[role=main]');
  const noMain = !content;
  if (!content) {   // biggest block: walk down while one child holds most of the text
    let node = document.body;
    for (let depth = 0; depth < 30; depth++) {
      let top = null, topLen = 0;
      for (const c of node.children) { const n = txt(c).length; if (n > topLen) { top = c; topLen = n; } }
      if (!top || topLen < txt(node).length * 0.6) break;
      node = top;
    }
    content = node;
  }
  content.setAttribute('data-ss-box', 'green');

  const furniture = {};
  const add = (kind, el, where) => { if (!el || furniture[kind]) return;
    const t = txt(el) || el.textContent || '';
    furniture[kind] = {selector: sel(el), chars: t.length, hash: hash(t), where: where || 'dom'};
    el.setAttribute('data-ss-box', 'red'); };
  add('header', document.querySelector('header'));
  add('menu', document.querySelector('nav'));
  add('breadcrumb', document.querySelector('[aria-label*="breadcrumb" i], [class*="breadcrumb" i]'));
  add('footer', document.querySelector('footer'));
  if (!furniture.footer && /developed by|all rights reserved|©/i.test(bodyText)) {   // this site has no <footer>
    const guess = outermost(all.filter(el => /developed by|all rights reserved|©/i.test(el.textContent)
      && txt(el).length < 3000 && !el.contains(content) && !content.contains(el)));
    add('footer', guess[0], 'dom (guessed from "developed by")');
  }

  const hits = all.filter(el => el.tagName !== 'SCRIPT' && chatRe.test(ident(el)));
  const outer = outermost(hits);
  const widget = outer.find(fixed) || outer[0];
  if (widget) add('widget', widget, widget.tagName === 'IFRAME' ? 'iframe' : (widget.shadowRoot ? 'shadow' : 'dom'));
  else { const host = all.find(el => (el.tagName === 'IFRAME' || el.shadowRoot) && fixed(el));
    if (host) add('widget', host, host.tagName === 'IFRAME' ? 'iframe' : 'shadow'); }
  add('popup', all.find(el => el.matches('[role=dialog],[aria-modal=true],[class*="modal" i],[class*="popup" i]')
    && vis(el) && (!widget || (el !== widget && !widget.contains(el)))));
  add('back_to_top', all.find(el => ['BUTTON', 'A', 'DIV'].includes(el.tagName) && vis(el) && fixed(el)
    && el.getBoundingClientRect().width < 120
    && /back.?to.?top|scroll.?(to.?)?top|\btop\b/i.test(txt(el) + ' ' + (el.getAttribute('aria-label') || '') + ' ' + cls(el))));
  if (/cookie/i.test(bodyText)) add('cookie', outermost(all.filter(el => /cookie/i.test(txt(el))
    && txt(el).length < 400 && vis(el)))[0]);
  add('skip_link', [...document.querySelectorAll('a')].find(a => /skip to/i.test(a.textContent)));

  const tables = [...content.querySelectorAll('table')];
  let tableExample = '';
  for (const t of tables) { const r = [...t.querySelectorAll('tr')].find(r => r.querySelector('td') && txt(r));
    if (r) { tableExample = cut([...r.children].map(c => c.textContent.trim()).join(' | '), 150); break; } }

  const cardAll = [...content.querySelectorAll('[class*="card" i]')];
  const cards = cardAll.filter(el => !el.parentElement.closest('[class*="card" i]'));
  const people = cards.filter(el => /faculty|hod|member|profile|staff/i.test(cls(el) + ' ' + ident(el)));
  const cardShown = people[0] || cards[0];

  const hiddenAll = [...content.querySelectorAll('*')].filter(el => !['SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT'].includes(el.tagName)
    && getComputedStyle(el).display === 'none' && el.textContent.trim().length > 20);
  const closedDetails = [...content.querySelectorAll('details:not([open])')].filter(el => el.textContent.trim().length > 20);
  const hidden = [...new Set([...outermost(hiddenAll), ...closedDetails])];
  const filterBtn = [...content.querySelectorAll('button,[role=tab]')].find(b => /^all\b|all departments|filter/i.test(txt(b)));

  const sibText = el => { let n = el;
    for (let i = 0; i < 2 && n && n !== content; i++, n = n.parentElement) {
      const b = n.previousElementSibling, a = n.nextElementSibling;
      const bt = b ? cut(txt(b), 60) : '', at = a ? cut(txt(a), 60) : '';
      if (bt || at) return {before: bt, after: at}; }
    return {before: '', after: ''}; };
  const counters = [...content.querySelectorAll('*')].filter(el => el.children.length === 0
    && /^[\d,]+\s*[%+]?$/.test(el.textContent.trim())).slice(0, 8)
    .map(el => Object.assign({n: el.textContent.trim()}, sibText(el)));

  const carousels = outermost([...document.querySelectorAll('[class*="swiper" i],[class*="slick" i],[class*="carousel" i],[class*="slider" i]')]);
  const slides = [...document.querySelectorAll('[class*="slide" i]')].map(el => cut(txt(el) || el.textContent, 200)).filter(t => t.length > 20);

  const pics = [...content.querySelectorAll('img')].filter(i => (i.alt || '').length >= 40
    || /notice|poster|fee|advert|schedule|circular|prospectus|result|date.?sheet|timetable/i.test((i.src || '') + ' ' + (i.alt || '')));

  const contentText = txt(content);
  return {
    landmarks: landmarks,
    content: {selector: sel(content), chars: contentText.length, no_main: noMain},
    content_text: contentText, body_text: bodyText, body_chars: bodyText.length,
    furniture: furniture,
    tables: {count: tables.length, example: tableExample},
    cards: {count: cards.length, people: people.length, selector: cardShown ? sel(cardShown) : '',
            example: cardShown ? cut(txt(cardShown), 200) : ''},
    hidden: {count: hidden.length, chars: hidden.reduce((s, e) => s + e.textContent.trim().length, 0),
             example: hidden[0] ? cut(hidden[0].textContent, 120) : '',
             controls: content.querySelectorAll('[aria-expanded],[role=tab],details,summary').length,
             filter: filterBtn ? cut(txt(filterBtn), 60) : ''},
    counters: counters,
    carousel: {containers: carousels.length, clones: document.querySelectorAll('.swiper-slide-duplicate,.slick-cloned,[class*="clone" i]').length,
               copies: slides.length - new Set(slides).size},
    images: {count: pics.length, example: pics[0] ? cut(pics[0].src, 150) + ' | alt: ' + cut(pics[0].alt, 80) : ''}
  };
}
"""

BOX_JS = r"""
() => {
  document.querySelectorAll('[data-ss-box]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const colour = el.getAttribute('data-ss-box') === 'green' ? '#00a000' : '#e00000';
    const b = document.createElement('div');
    b.setAttribute('data-ss-overlay', '1');
    b.style.cssText = 'position:absolute;pointer-events:none;z-index:2147483647;box-sizing:border-box;'
      + 'border:3px solid ' + colour + ';background:' + colour + '22;left:' + (r.left + window.scrollX)
      + 'px;top:' + (r.top + window.scrollY) + 'px;width:' + r.width + 'px;height:' + r.height + 'px';
    document.body.appendChild(b);
  });
}
"""

CLEAR_JS = r"""
() => {
  document.querySelectorAll('[data-ss-overlay]').forEach(el => el.remove());
  document.querySelectorAll('[data-ss-box]').forEach(el => el.removeAttribute('data-ss-box'));
}
"""


class StudyRenderer(fh.ClickRenderer):
    """ClickRenderer that can also measure a page and take a screenshot."""

    def body_length(self) -> int:
        return self._page.evaluate("() => (document.body.innerText || '').length")

    def pause(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)

    def html(self) -> str:
        return self._page.content()

    def measure(self) -> dict:
        return self._page.evaluate(
            MEASURE_JS, {"chat": fh.CHAT_WIDGET.pattern, "landmarks": LANDMARKS})

    def screenshot(self, path: Path) -> None:
        """Full page, with green/red boxes drawn for the picture only."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._page.evaluate(BOX_JS)
            self._page.screenshot(path=str(path), full_page=True)
        finally:
            self._page.evaluate(CLEAR_JS)


# ── Step 3: wait until the page stops changing ──────────────────────────────

def wait_until_stable(read_length: Callable[[], int], pause: Callable[[int], None],
                      max_seconds: float = STABLE_MAX_SECONDS) -> tuple[float, bool]:
    """Return (seconds waited, stable). Stable = same text length STABLE_POLLS times in a row."""
    waited, last, same = 0.0, -1, 0
    while waited < max_seconds:
        size = read_length()
        same = same + 1 if size == last else 0
        last = size
        if same >= STABLE_POLLS - 1:
            return waited, True
        pause(POLL_MS)
        waited += POLL_MS / 1000
    return waited, False


def settle(renderer) -> tuple[float, bool, bool]:
    """Wait, scroll to the bottom (spec 02b lesson), wait again. Return (seconds, stable, grew)."""
    first, stable_a = wait_until_stable(renderer.body_length, renderer.pause)
    before = renderer.body_length()
    renderer.scroll_bottom()
    second, stable_b = wait_until_stable(renderer.body_length, renderer.pause)
    return first + second, stable_a and stable_b, renderer.body_length() > before


# ── Step 4: compare with the old scraper ────────────────────────────────────

@dataclass
class OldTest:
    error: str = ""
    old_chars: int = 0
    garbage_pct: float = 0.0
    lost: bool = False
    lost_count: int = 0
    garbage_examples: list[str] = field(default_factory=list)
    lost_examples: list[str] = field(default_factory=list)


def _fold(text: str) -> str:
    """Comparison form: lower case, markdown markers and table bars dropped, spaces folded."""
    return " ".join(re.sub(r"[#•|]", " ", text.lower()).split())


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _word_share(line: str, vocabulary: set[str]) -> float:
    words = _words(line)
    return sum(w in vocabulary for w in words) / len(words) if words else 1.0


def compare_old(old_text: str, content_text: str) -> OldTest:
    """Compare what the old scraper kept with the text inside the content area.

    Deliberately forgiving about format: the old code rewrites tables ("| a | b |")
    and faculty cards ("Name — Title"), so lines are compared without markers and
    by shared words, not by exact characters.
    """
    result = OldTest(old_chars=len(old_text))
    content_fold, old_fold = _fold(content_text), _fold(old_text)
    content_vocab, old_vocab = set(_words(content_text)), set(_words(old_text))

    old_lines = [ln for ln in old_text.splitlines() if ln.strip()]
    garbage = [ln for ln in old_lines
               if _fold(ln) not in content_fold and _word_share(ln, content_vocab) < GARBAGE_WORDS]
    total = sum(len(ln) for ln in old_lines)
    result.garbage_pct = round(100 * sum(len(ln) for ln in garbage) / total, 1) if total else 0.0
    result.garbage_examples = [" ".join(ln.split())[:100] for ln in garbage[:5]]

    lost = [ln.strip() for ln in content_text.splitlines()
            if len(ln.strip()) >= MIN_LOST_LINE_CHARS
            and _fold(ln) not in old_fold and _word_share(ln, old_vocab) < SAME_WORDS]
    result.lost, result.lost_count = bool(lost), len(lost)
    result.lost_examples = [ln[:100] for ln in lost[:3]]
    return result


def run_old_extractor(extractor: Callable, html: str, address: str, content_text: str) -> OldTest:
    try:
        old_text = extractor(BeautifulSoup(html, PARSER), address)
    except Exception as exc:  # noqa: BLE001 - spec: record the error and keep going
        return OldTest(error=f"{type(exc).__name__}: {str(exc)[:100]}")
    return compare_old(old_text, content_text)


# ── Step 5: offline copies ──────────────────────────────────────────────────

def fixture_name(address: str) -> str:
    """File name for a page: the path with / turned into __, at most 80 characters."""
    path = urlsplit(address).path.strip("/")
    if not path:
        return "home.html"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", path.replace("/", "__"))[:80] + ".html"


def clean_html(html: str) -> str:
    """Remove scripts, styles, svg and base64 data. Keeps inline style, which shows what is hidden."""
    soup = BeautifulSoup(html, PARSER)
    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()
    for tag in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
        tag.decompose()
    return _BASE64.sub("data:removed", str(soup))


def _repeated_group(soup) -> list | None:
    """The group of sibling elements with identical text that wastes the most bytes."""
    best, best_gain = None, 0
    for parent in soup.find_all(True):
        groups: dict = {}
        for child in parent.find_all(True, recursive=False):
            text = " ".join(child.get_text().split())
            if text:
                groups.setdefault((child.name, text), []).append(child)
        for kids in groups.values():
            gain = sum(len(str(k)) for k in kids[1:])
            if len(kids) > 1 and gain > best_gain:
                best, best_gain = kids, gain
    return best


def shrink_to_limit(html: str, limit: int = MAX_FIXTURE_BYTES) -> tuple[str, str]:
    """Cut the largest repeated block until the file fits. Returns (html, note); note is '' if untouched."""
    if len(html.encode("utf-8")) <= limit:
        return html, ""
    soup = BeautifulSoup(html, PARSER)
    notes: list[str] = []
    for _ in range(MAX_SHRINK_ROUNDS):
        group = _repeated_group(soup)
        if not group:
            break
        for extra in group[1:]:
            extra.decompose()
        notes.append(f"removed {len(group) - 1} repeated copies of <{group[0].name}>")
        out = str(soup)
        if len(out.encode("utf-8")) <= limit:
            return out, "; ".join(notes)
    return str(soup), "; ".join(notes + ["STILL OVER the size limit"])


# ── Step 6: one page ────────────────────────────────────────────────────────

@dataclass
class PageStudy:
    address: str
    kind: str
    status: str = "ok"            # ok | slow | blocked | error
    note: str = ""
    load_seconds: float = 0.0
    grew_after_scroll: bool = False
    measure: dict = field(default_factory=dict)
    content_text: str = ""        # kept in memory only, never written to a report
    body_text: str = ""
    old: OldTest = field(default_factory=OldTest)
    own_content: bool | None = None
    fixture_html: str = ""
    fixture_kb: int = 0
    fixture_file: str = ""


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _count(measure: dict, key: str) -> int:
    return measure.get(key, {}).get("count", 0)


def page_row(study: PageStudy) -> list:
    m, old = study.measure, study.old
    lm = m.get("landmarks", {})
    chars_in = m.get("content", {}).get("chars", 0)
    return [
        study.address, study.kind, study.status, round(study.load_seconds, 1),
        _yes(study.grew_after_scroll),
        *[_yes(lm.get(q, {}).get("n", 0) > 0) for q in ("header", "nav", "main", "footer", "aside")],
        m.get("content", {}).get("selector", ""), chars_in, max(m.get("body_chars", 0) - chars_in, 0),
        old.old_chars, old.garbage_pct, _yes(old.lost), old.lost_count,
        _count(m, "tables"), _count(m, "cards"), m.get("hidden", {}).get("chars", 0),
        len(m.get("counters", [])), m.get("carousel", {}).get("copies", 0), _count(m, "images"),
        m.get("furniture", {}).get("widget", {}).get("where", "none"),
        "" if study.own_content is None else _yes(study.own_content),
        study.fixture_kb, study.note,
    ]


def _fill_from_measure(study: PageStudy, measure: dict) -> None:
    study.content_text = measure.pop("content_text")
    study.body_text = measure.pop("body_text")
    study.measure = measure
    if measure["content"]["no_main"]:
        study.note = "no main: used the biggest content block"
    if not measure["furniture"].get("widget"):
        study.note = (study.note + "; " if study.note else "") + "no chat widget found"


def study_page(renderer, address: str, kind: str, extractor: Callable, screens_dir: Path,
               sleep: Callable[[float], None] = time.sleep) -> PageStudy:
    study = PageStudy(address, kind)
    started = time.monotonic()
    try:
        view = fs.open_page(renderer, address, sleep)
    except fs.Blocked as exc:
        study.status, study.note = "blocked", f"site blocked us ({exc})"
        return study
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the study
        study.status, study.note = "error", f"{type(exc).__name__}: {str(exc)[:100]}"
        return study
    try:
        _, stable, study.grew_after_scroll = settle(renderer)
        study.load_seconds = time.monotonic() - started
        if not stable or study.load_seconds > STABLE_MAX_SECONDS:
            study.status = "slow"
        if uh.normalize(view.final) != uh.normalize(address):
            study.note = f"moved to {view.final}"
        html = renderer.html()   # taken before measuring, so it has none of our marker attributes
        _fill_from_measure(study, renderer.measure())
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the study
        study.status, study.note = "error", f"{type(exc).__name__}: {str(exc)[:100]}"
        return study
    study.old = run_old_extractor(extractor, html, address, study.content_text)
    _save_screenshot(renderer, study, screens_dir)
    study.fixture_html = clean_html(html)
    return study


def _save_screenshot(renderer, study: PageStudy, screens_dir: Path) -> None:
    try:
        renderer.screenshot(screens_dir / (fixture_name(study.address)[:-5] + ".png"))
    except Exception as exc:  # noqa: BLE001 - a missing picture is a note, not a crash
        study.note += f"; screenshot failed: {str(exc)[:60]}"


# ── Step 7: across all pages ────────────────────────────────────────────────

def repeated_lines(texts: dict[str, str], fraction: float = REPEAT_FRACTION) -> list[tuple[int, str, str]]:
    """(pages, line, length kind) for lines on at least `fraction` of the pages.

    The kind is only about length and contact tokens (page_furniture.is_navigation_label),
    so a short real heading such as a person's name also reads as "short".
    """
    if not texts:
        return []
    freq = page_furniture.document_frequency(texts)
    rows = [(n, key, "short (40 chars or less)" if page_furniture.is_navigation_label(key) else "long or contact")
            for key, n in freq.items() if n / len(texts) >= fraction]
    return sorted(rows, key=lambda r: (-r[0], r[1]))


def check_known_lines(repeated: list[tuple[int, str, str]]) -> list[tuple[str, bool, int]]:
    out = []
    for name, test in KNOWN_LINES:
        counts = [n for n, key, _ in repeated if test(key)]
        out.append((name, bool(counts), max(counts, default=0)))
    return out


def best_content_selector(selectors: list[str]) -> tuple[str, int, int]:
    """(selector, pages that agree, pages counted)."""
    found = [s for s in selectors if s]
    if not found:
        return "", 0, 0
    selector, n = Counter(found).most_common(1)[0]
    return selector, n, len(found)


def counter_order(counters: list[dict]) -> str:
    before = sum(bool(c.get("before")) and not c.get("after") for c in counters)
    after = sum(bool(c.get("after")) and not c.get("before") for c in counters)
    if not counters:
        return "no counters"
    if after and not before:
        return "label AFTER the number"
    if before and not after:
        return "label BEFORE the number"
    return "unclear (labels on both sides or none)"


def furniture_summary(studies: list[PageStudy]) -> list[list]:
    """One row per furniture kind: pages that have it, most common selector, sameness."""
    rows = []
    done = [s for s in studies if s.measure]
    for kind in FURNITURE_KINDS:
        items = [s.measure["furniture"][kind] for s in done if kind in s.measure["furniture"]]
        if not items:
            rows.append([kind, f"0 of {len(done)}", "-", "-", "-"])
            continue
        selector, n_sel = Counter(i["selector"] for i in items).most_common(1)[0]
        n_hash = Counter(i["hash"] for i in items).most_common(1)[0][1]
        where = Counter(i["where"] for i in items).most_common(1)[0][0]
        rows.append([kind, f"{len(items)} of {len(done)}", f"`{selector}` ({n_sel} pages)",
                     "yes" if n_hash == len(items) else f"no ({n_hash} of {len(items)} equal)", where])
    return rows


def apply_corpus_results(studies: list[PageStudy]) -> list[tuple[int, str, str]]:
    """Set own_content on every page, and return the repeated lines of the whole run."""
    texts = {s.address: s.body_text for s in studies if s.measure}
    freq = page_furniture.document_frequency(texts)
    for s in studies:
        if s.measure:
            s.own_content = page_furniture.page_has_own_content(s.content_text, freq)
    return repeated_lines(texts)


# ── Step 8: reports ─────────────────────────────────────────────────────────

def _table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |" for r in rows]
    return lines + [""]


def _first_example(studies: list[PageStudy], key: str, field_name: str, count_name: str = "count") -> str:
    for s in studies:
        item = s.measure.get(key, {})
        if item.get(count_name) and item.get(field_name):
            return f"{item[count_name]} on {s.address} — example: {item[field_name]}"
    return f"not found on any of the {len(studies)} pages"


def section_content(studies: list[PageStudy]) -> list[str]:
    selector, n, total = best_content_selector(
        [s.measure.get("content", {}).get("selector", "") for s in studies if s.measure])
    answer = f"`{selector}` — {n} of {total} pages agree" if selector else "none found"
    no_main = [s.address for s in studies if s.measure and s.measure["content"]["no_main"]]
    lines = ["## 1. Where the real content is", "", f"Best selector: {answer}.", ""]
    if no_main:
        lines += [f"Pages with no `main`: {len(no_main)} of {total}", ""] + [f"- {a}" for a in no_main] + [""]
    return lines


def section_furniture(studies, repeated, known) -> list[str]:
    lines = ["## 2. Furniture", "", "Selectors (most common one per part):", ""]
    lines += _table(["part", "pages that have it", "selector", "same text on all?", "where"],
                    furniture_summary(studies))
    lines += ["Known lines (spec acceptance 4):", ""]
    lines += _table(["line", "found", "pages"], [[n, _yes(f), c] for n, f, c in known])
    lines += [f"Lines on at least half of the pages ({len(repeated)}):", ""]
    return lines + _table(["pages", "line", "kind"], [list(r) for r in repeated[:80]])


def section_old(studies: list[PageStudy]) -> list[str]:
    rows = []
    for s in studies:
        if not s.measure:
            rows.append([s.address, "-", "-", "-", "-", s.status])
            continue
        o = s.old
        garbage = o.error or "; ".join(o.garbage_examples[:2])
        lost = "; ".join(o.lost_examples[:2])
        rows.append([s.address, f"{o.garbage_pct}%", f"{_yes(o.lost)} ({o.lost_count})",
                     garbage[:120], lost[:160], s.status])
    lines = ["## 3. Old scraper test", "",
             "Garbage % = share of the old output that is not in the content area. "
             "Lost = content lines the old output misses. OCR was off.", ""]
    return lines + _table(["page", "garbage %", "lost content", "example garbage", "example lost line", "status"], rows)


def section_special(studies: list[PageStudy]) -> list[str]:
    done = [s for s in studies if s.measure]
    order = counter_order([c for s in done for c in s.measure["counters"]])
    first_counter = next((c for s in done for c in s.measure["counters"]), None)
    counters = (f"{order} — example: {first_counter['before']!r} / {first_counter['n']} / {first_counter['after']!r}"
                if first_counter else "not found on any page")
    closed = next((s for s in done if s.measure["hidden"]["controls"]), None)
    hidden = _first_example(done, "hidden", "example")
    if hidden.startswith("not found") and closed:
        hidden = (f"{closed.measure['hidden']['controls']} accordion/tab controls on {closed.address}, "
                  f"but no closed text is in the page")
    carousel = next((s for s in done if s.measure["carousel"]["containers"]), None)
    return ["## 4. Special content", "",
            f"- **Tables:** {_first_example(done, 'tables', 'example')}",
            f"- **Faculty/HOD cards:** {_first_example(done, 'cards', 'example', 'people')}",
            f"- **Other cards:** {_first_example(done, 'cards', 'example')}",
            f"- **Hidden text:** {hidden}",
            f"- **Counters:** {counters}",
            "- **Carousel:** " + (f"{carousel.measure['carousel']['containers']} slider(s) on {carousel.address}, "
                                  f"{carousel.measure['carousel']['copies']} repeated slides, "
                                  f"{carousel.measure['carousel']['clones']} clones" if carousel else "not found on any page"),
            f"- **Images with text:** {_first_example(done, 'images', 'example')}", ""]


def section_problems(studies: list[PageStudy], warnings: list[str]) -> list[str]:
    lines = ["## 5. Problems and surprises", ""] + [f"- WARNING: {w}" for w in warnings]
    for s in studies:
        if s.status != "ok":
            lines.append(f"- {s.status.upper()}: {s.address} {s.note}")
        elif s.note:
            lines.append(f"- {s.address}: {s.note}")
        if s.measure and s.grew_after_scroll:
            lines.append(f"- {s.address}: more text appeared after scrolling")
        if s.measure and s.own_content is False:
            lines.append(f"- {s.address}: no own content (a valid result)")
        if s.measure and s.measure["furniture"].get("widget", {}).get("where") in ("iframe", "shadow"):
            lines.append(f"- {s.address}: chat widget is in an {s.measure['furniture']['widget']['where']}, "
                         f"normal text reading cannot see it")
    return lines + [""]


def build_report(studies: list[PageStudy], repeated, known, warnings: list[str], stamp: str) -> str:
    lines = [f"# Site structure study — {stamp}", "",
             f"Pages studied: {len(studies)}. OK: {sum(s.status == 'ok' for s in studies)}.", ""]
    lines += section_content(studies) + section_furniture(studies, repeated, known)
    lines += section_old(studies) + section_special(studies) + section_problems(studies, warnings)
    return "\n".join(lines) + "\n"


def write_fixtures(studies: list[PageStudy], warnings: list[str]) -> None:
    """Write the offline copies (each at most 500 KB, all at most 6 MB) and their index."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    rows, total = [], 0
    for s in studies:
        if not s.fixture_html:
            continue
        html, note = shrink_to_limit(s.fixture_html)
        s.fixture_file = fixture_name(s.address)
        (FIXTURE_DIR / s.fixture_file).write_text(html, encoding="utf-8")
        size = len(html.encode("utf-8"))
        s.fixture_kb, total = size // 1024, total + size
        if note:
            s.note = (s.note + "; " if s.note else "") + f"fixture: {note}"
        rows.append([s.address, s.fixture_file, s.kind])
    uh.write_csv(FIXTURE_DIR / "index.csv", ["address", "file", "page type"], rows)
    if total > MAX_FIXTURES_TOTAL:
        warnings.append(f"Fixtures total {total // 1024} KB, over the {MAX_FIXTURES_TOTAL // 1024} KB limit.")


def write_outputs(studies: list[PageStudy], warnings: list[str], limit: int | None) -> list[Path]:
    tag = f"_limit{limit}" if limit else ""   # a test run never overwrites a full run
    repeated = apply_corpus_results(studies)
    if not limit:
        write_fixtures(studies, warnings)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    pages_csv = REPORT_DIR / f"structure_pages{tag}.csv"
    uh.write_csv(pages_csv, CSV_HEADER, [page_row(s) for s in studies])
    report = REPORT_DIR / f"structure_report{tag}.md"
    report.write_text(build_report(studies, repeated, check_known_lines(repeated), warnings,
                                   time.strftime("%Y-%m-%d")), encoding="utf-8")
    return [report, pages_csv]


# ── Step 9: run ─────────────────────────────────────────────────────────────

def sample_pages(limit: int | None) -> list[tuple[str, str]]:
    pages = [(SITE + path, kind) for path, kind in SAMPLE_PAGES]
    return pages[:limit] if limit else pages


def check_against_pages_csv(pages: list[tuple[str, str]]) -> list[str]:
    """Warn when a sample page is not a kept row of url_lists/pages.csv. The study still runs."""
    with open(PAGES_CSV, encoding="utf-8-sig", newline="") as handle:   # file starts with a BOM
        kept = {uh.normalize(r["address"]) for r in csv.DictReader(handle) if r["keep"].strip() == "yes"}
    return [f"{a} is not a kept row in url_lists/pages.csv" for a, _ in pages if uh.normalize(a) not in kept]


def run(limit: int | None, sleep: Callable[[float], None] = time.sleep) -> list[Path]:
    extractor = load_old_extractor()
    pages = sample_pages(limit)
    warnings = check_against_pages_csv(pages)
    screens_dir = REPORT_DIR / (f"screens_limit{limit}" if limit else "screens")
    studies: list[PageStudy] = []
    with StudyRenderer() as renderer:
        try:
            for address, kind in pages:
                studies.append(study_page(renderer, address, kind, extractor, screens_dir, sleep))
                print(f"[{len(studies)}/{len(pages)}] {studies[-1].status:<8} {address}")
                sleep(DELAY_SECONDS)
        except KeyboardInterrupt:
            warnings.append(f"Stopped early with Ctrl+C: {len(studies)} of {len(pages)} pages done.")
            print("Stopped. Saving what is done so far...")
    return write_outputs(studies, warnings, limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Study the structure of the new uoli.edu.pk pages.")
    parser.add_argument("--limit", type=int, default=None, help="study only the first N pages (small tests)")
    args = parser.parse_args()
    for path in run(args.limit):
        print("Wrote", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
