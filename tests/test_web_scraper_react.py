"""Offline tests for spec 05: the scraper on the new React site.

Page tests read the saved copies in tests/fixtures/site/. The semester-click tests
open a small local fake page with Chromium (a file:// address, never the live site).
"""
import glob
import re
import subprocess
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import config
import web_scraper

FIX = Path(__file__).parent / "fixtures" / "site"
ALL_PAGES = sorted(p.name for p in FIX.glob("*.html"))
EVENT = Path(glob.glob(str(FIX / "research__innovation__events__three-days-olive-*.html"))[0]).name
BSCS = "academics__programs__bscs.html"
CS = "academics__departments__computer-science.html"
FACULTY = "academics__faculty.html"


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    # No image is downloaded and data/ocr_cache.json is never touched.
    monkeypatch.setattr(web_scraper, "OCR_AVAILABLE", False)


def soup_of(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIX / name).read_text(encoding="utf-8"), "html.parser")


def text_of(name: str, url: str = "https://uoli.edu.pk/test") -> str:
    return web_scraper._extract_text(soup_of(name), base_url=url)


def lines_of(name: str) -> list[str]:
    return text_of(name).split("\n")


def title_of(name: str) -> str:
    """The page title: the first <h1> in main's own title header (read from the copy)."""
    main = soup_of(name).select_one("main#main-content")
    return main.select_one("header h1").get_text(" ", strip=True)


# ── chrome, titles, aside ───────────────────────────────────────────────────

def test_no_site_chrome_on_any_page():
    assert len(ALL_PAGES) == 25
    for name in ALL_PAGES:
        text = text_of(name)
        for junk in ("Home ›", "UOLI AI Assistant", "Developed by", "Skip to main content"):
            assert junk not in text, f"{junk!r} found in {name}"


TITLE_PAGES = [
    "about.html", "about__vc-message.html", "services__scholarships.html",
    "academics__programs.html", BSCS, FACULTY, "news.html", "news__news-0.html",
    EVENT, "services__student-resources.html",
]


@pytest.mark.parametrize("name", TITLE_PAGES)
def test_titles_are_kept(name):
    assert title_of(name) in lines_of(name)


def test_event_page_keeps_aside_details():
    # The browser shows these labels in capitals through CSS; the page text is not.
    text = text_of(EVENT).lower()
    assert "session lead & facilitator" in text
    assert "supervision / partner" in text


# ── empty pages ─────────────────────────────────────────────────────────────

def test_bs_economics_is_empty(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # fetch_page writes scratch/_dryrun_web_cache.json
    html = (FIX / "academics__programs__bs-economics.html").read_text(encoding="utf-8")
    monkeypatch.setattr(web_scraper, "_fetch_with_playwright", lambda url: html)
    assert web_scraper.fetch_page("https://uoli.edu.pk/academics/programs/bs-economics") == (None, "empty")


def test_only_bs_economics_is_empty():
    empty = [n for n in ALL_PAGES if web_scraper._is_empty_page(soup_of(n))]
    assert empty == ["academics__programs__bs-economics.html"]


def test_empty_counted_separately(monkeypatch):
    monkeypatch.setattr(web_scraper, "fetch_page", lambda url, session=None: (None, "empty"))
    monkeypatch.setattr(web_scraper.time, "sleep", lambda s: None)
    results = web_scraper.scrape_all_pages(
        None, urls=[web_scraper.GUARANTEED_PAGES[0]], include_guaranteed=False)
    assert results["empty"] == 1
    assert results["failed"] == 0


# ── people ──────────────────────────────────────────────────────────────────

def test_cs_people_lines():
    lines = lines_of(CS)
    people = [l for l in lines if "Email:" in l]
    assert len(people) == 9
    khawar = [l for l in people if l.startswith("M. Khawar Abbas")]
    assert len(khawar) == 1 and "m.khawar@uoli.edu.pk" in khawar[0]
    hod = [l for l in lines if l.startswith("M. Khawar Abbas is the Head of")]
    assert hod == ["M. Khawar Abbas is the Head of the Department of Computer Science "
                   f"at the {config.UNIVERSITY_NAME}."]
    assert any("ismaildotani@uoli.edu.pk" in l and l.startswith("Ismail Khan") for l in people)


def test_cs_welcome_message_kept():
    assert "Welcome to the Department of Computer Science at the University of Loralai" in text_of(CS)


def test_faculty_directory_90_people():
    people = [l for l in lines_of(FACULTY) if "Email" in l]
    assert len(people) == 90
    assert all(re.search(r"Department of|Directorate|Office|Cent(re|er)", l) for l in people)
    assert not [l for l in lines_of(FACULTY) if re.fullmatch(r"[A-Z]{2}", l)]


def test_visiting_badge_in_line():
    lines = lines_of(FACULTY)
    assert any("(Visiting)" in l for l in lines)
    assert "Visiting" not in lines


def test_people_line_without_email_or_department():
    html = ("<main id='main-content'><div><h3>Dr Test Person</h3><p>Lecturer</p>"
            "<button><span>Profile</span><span>→</span></button></div>"
            "<p>" + "Filler sentence about the page. " * 8 + "</p></main>")
    text = web_scraper._extract_text(BeautifulSoup(html, "html.parser"), "https://uoli.edu.pk/t")
    assert "Dr Test Person — Lecturer. Email not listed." in text
    assert "Department" not in text.split("Email not listed.")[0]


# ── filters and labels ──────────────────────────────────────────────────────

def test_faq_questions_all_kept():
    main = soup_of("admissions__faq.html").select_one("main#main-content")
    questions = [b.get_text(" ", strip=True) for b in main.find_all("button")
                 if b.get_text(" ", strip=True).endswith("?")]
    assert len(questions) == 10
    lines = lines_of("admissions__faq.html")
    for q in questions:
        assert q in lines
    assert "All Questions" not in lines


FILTER_PAGES = [FACULTY, "academics__departments.html", "campus-life__sports__events.html",
                "academics__programs.html", "news.html"]


@pytest.mark.parametrize("name", FILTER_PAGES)
def test_filter_bars_removed(name):
    lines = lines_of(name)
    for label in ("All Departments", "All Faculties", "All Events (12)",
                  "All Degree Programs", "All"):
        assert label not in lines


def test_allied_health_survives_filter_removal():
    assert any("Allied Health Sciences" in l for l in lines_of(FACULTY))


def test_no_button_labels():
    for name in ALL_PAGES:
        for line in lines_of(name):
            assert line not in web_scraper._BUTTON_LABELS, (name, line)
            assert not re.fullmatch(r"Sem \d+|Showing \d+ Courses", line), (name, line)


def test_table_rows_have_no_label_cells():
    rows = [l for l in lines_of(BSCS) if l.startswith("|")]
    assert rows and not any("Tap course for syllabus" in r for r in rows)


def test_undergraduate_not_cut(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    name = "academics__undergraduate.html"
    html = (FIX / name).read_text(encoding="utf-8")
    monkeypatch.setattr(web_scraper, "_fetch_with_playwright", lambda url: html)
    text, reason = web_scraper.fetch_page("https://uoli.edu.pk/academics/undergraduate")
    # The raw page is 17,681 chars; cleaning brings it to ~14,900, so the old limit
    # of 15,000 was only just missed. The point is that nothing is cut and the
    # limit now leaves room for pages that grow (8 semesters on program pages).
    assert reason == "ok"
    assert text == text_of(name)
    assert config.MAX_PAGE_CHARS >= 40000


# ── footer noise, errors, hash ──────────────────────────────────────────────

def test_footer_noise_only_on_fallback():
    body = "<p>Curriculum developed by HEC.</p><p>" + "Real sentence here. " * 10 + "</p>"
    with_main = BeautifulSoup(f"<main id='main-content'>{body}</main>", "html.parser")
    without = BeautifulSoup(f"<main>{body}</main>", "html.parser")
    assert "Curriculum developed by HEC." in web_scraper._extract_text(with_main, "https://uoli.edu.pk/t")
    assert "Curriculum developed by HEC." not in web_scraper._extract_text(without, "https://uoli.edu.pk/t")


def test_extract_error_gives_unreachable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def boom(soup, base_url=""):
        raise ValueError("broken page")

    monkeypatch.setattr(web_scraper, "_fetch_with_playwright",
                        lambda url: "<html><body><main id='main-content'>" + "word " * 100 + "</main></body></html>")
    monkeypatch.setattr(web_scraper, "_extract_text", boom)
    assert web_scraper.fetch_page("https://uoli.edu.pk/x") == (None, "unreachable")


def test_content_hash_is_text_only(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("content_hash must not touch the network")

    monkeypatch.setattr(web_scraper.requests, "get", no_network)
    digest = web_scraper.content_hash("Some page text", "https://uoli.edu.pk/")
    assert "_" not in digest and len(digest) == 8
    assert not hasattr(web_scraper, "_AD_IMAGE_URL")


# ── OCR is lazy ─────────────────────────────────────────────────────────────

def test_import_does_not_load_easyocr():
    root = Path(__file__).resolve().parent.parent
    code = "import sys, web_scraper; print('easyocr' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr[-500:]


def test_pdf_ingest_uses_ocr_getter(monkeypatch):
    import importlib
    import pdf_ingest
    # Another test file re-imports web_scraper, so use the copy pdf_ingest will find.
    current = importlib.import_module("web_scraper")
    calls = []
    monkeypatch.setattr(current, "_get_ocr_reader", lambda: calls.append(1))
    assert pdf_ingest._ocr_page(object()) == ("", False, 0.0)
    assert calls == [1]


# ── semester tabs (fake page, real Chromium, local file only) ───────────────

FAKE_TABS = """<!doctype html><html><body>
<header>Site menu</header>
<main id="main-content">
<nav aria-label="Breadcrumb">Home › Fake</nav>
<header><h1>Fake Program</h1></header>
<p>This fake program page has enough of its own text to count as a real page, so that nothing here is judged empty.</p>
<div id="area">
  <h2>Curriculum Breakdown</h2>
  <!-- like the live site: the short "Sem N" label is hidden, so innerText omits it -->
  <div id="tabs">
    <button><span style="display:none">Sem 1</span><span>Semester I</span></button>
    <button><span style="display:none">Sem 2</span><span>Semester II</span></button>
    <button><span style="display:none">Sem 3</span><span>Semester III</span></button>
    <button id="alldept" role="tab">All Departments</button>
  </div>
  <div id="holder"></div>
  <div class="phone"><div>FAKE-101</div><div>Credits: 3</div><div>FAKE-102</div></div>
</div>
</main>
<script>
const sems = [["FAKE-101","FAKE-102"],["FAKE-201","FAKE-202"],["FAKE-301","FAKE-302"]];
function show(i){ document.getElementById('holder').innerHTML =
  '<table><tbody>' + sems[i].map(c => '<tr><td>'+c+'</td><td>Course '+c+'</td></tr>').join('') + '</tbody></table>'; }
show(0);
document.querySelectorAll('#tabs button').forEach((b, i) => { if (i < 3) b.onclick = () => show(i); });
document.getElementById('alldept').onclick = () => document.body.setAttribute('data-clicked', '1');
</script></body></html>"""


def open_fake(html_source: str, tmp_path, monkeypatch):
    """Open a fake page with Chromium; return (html, extracted text, info messages)."""
    if not web_scraper.PLAYWRIGHT_AVAILABLE:
        pytest.skip("playwright is not installed")
    page = tmp_path / "tabs.html"
    page.write_text(html_source, encoding="utf-8")
    # A spy instead of caplog: other test files change logging settings.
    messages = []
    monkeypatch.setattr(web_scraper.logger, "info", lambda msg, *a, **k: messages.append(str(msg)))
    html = web_scraper._fetch_with_playwright(page.as_uri())
    if not html:
        pytest.skip("Chromium could not open the local file")
    text = web_scraper._extract_text(BeautifulSoup(html, "html.parser"), "https://uoli.edu.pk/fake")
    return html, text, messages


@pytest.fixture
def fake_tabs(tmp_path, monkeypatch):
    return open_fake(FAKE_TABS, tmp_path, monkeypatch)


# The live table fills in two steps: the rows appear on the click, the header and the
# "Total Courses" row a moment later. Reading at the first change copies half a table.
# Not timer-based, so the test is not flaky: after a click, the table is completed
# only when it is READ for the third time (the page counts the reads itself).
FAKE_TABS_SLOW = FAKE_TABS.replace(
    "function show(i){", """function show(i){
  window.__armed = true; window.__reads = 0; window.__sem = i;""").replace(
    "show(0);", """show(0);
window.__complete = () => {
  const t = document.querySelector('#holder table'); const i = window.__sem;
  t.insertAdjacentHTML('afterbegin', '<thead><tr><th>Course Code</th><th>Course Title</th></tr></thead>');
  t.insertAdjacentHTML('beforeend', '<tfoot><tr><td>Total Courses: <strong>' + (i + 1) + '</strong></td><td>' + (i + 1) * 10 + ' Cr. Hrs</td></tr></tfoot>');
  window.__armed = false;
};
window.__complete();""")


def counting_reader(monkeypatch):
    """Make the scraper's page-side table read count itself and finish the table on read 3."""
    original = web_scraper._TABLE_JS
    wrapped = ("() => { if (window.__armed) { window.__reads++; "
               "if (window.__reads >= 3) window.__complete(); } return (" + original + ")(); }")
    monkeypatch.setattr(web_scraper, "_TABLE_JS", wrapped)


def test_table_is_copied_only_after_it_has_settled(tmp_path, monkeypatch):
    # Each semester's total row is different, so the line dedup cannot hide a half-copy.
    counting_reader(monkeypatch)
    _, text, _ = open_fake(FAKE_TABS_SLOW, tmp_path, monkeypatch)
    assert "| Total Courses:2 | 20 Cr. Hrs |" in text
    assert "| Total Courses:3 | 30 Cr. Hrs |" in text


def test_equal_table_rows_in_different_semesters_are_kept():
    # Live bug: the header row and an equal "Total Courses: 6 | 18 Cr. Hrs" row of
    # Semesters III-V were dropped as repeats of Semester I's.
    table = ("<table><thead><tr><th>Course Code</th><th>Course Title</th></tr></thead><tbody>"
             "<tr><td>{c}</td><td>Course {c}</td></tr></tbody>"
             "<tfoot><tr><td>Total Courses: <strong>1</strong></td><td>18 Cr. Hrs</td></tr></tfoot></table>")
    body = "".join(f"<h3>Semester {n}</h3>" + table.format(c=f"X-{n}") for n in (1, 2, 3))
    html = "<main id='main-content'><p>" + "Own text of the page. " * 10 + f"</p>{body}</main>"
    text = web_scraper._extract_text(BeautifulSoup(html, "html.parser"), "https://uoli.edu.pk/t")
    assert text.count("| Course Code | Course Title |") == 3
    assert text.count("| Total Courses:1 | 18 Cr. Hrs |") == 3


def test_same_table_printed_twice_under_one_heading_is_collapsed():
    # A desktop copy and a mobile copy of one table sit under the same heading.
    table = "<table><tbody><tr><td>FEE-1</td><td>Tuition</td></tr></tbody></table>"
    html = ("<main id='main-content'><p>" + "Own text of the page. " * 10
            + f"</p><h3>Fees</h3>{table}{table}</main>")
    text = web_scraper._extract_text(BeautifulSoup(html, "html.parser"), "https://uoli.edu.pk/t")
    assert text.count("| FEE-1 | Tuition |") == 1


def test_semester_tabs_are_read(fake_tabs):
    _, text, _ = fake_tabs
    for name in ("Semester I", "Semester II", "Semester III"):
        assert name in text.split("\n")
    for code in ("FAKE-101", "FAKE-102", "FAKE-201", "FAKE-202", "FAKE-301", "FAKE-302"):
        assert code in text


def test_fake_tabs_no_repeated_courses(fake_tabs):
    _, text, _ = fake_tabs
    for code in ("FAKE-101", "FAKE-102", "FAKE-201", "FAKE-301"):
        assert text.count(code) == 2, code   # once in the code cell, once in "Course <code>"
    assert not re.search(r"^Sem \d$", text, re.M)
    assert "Credits: 3" not in text          # the phone copy went with the original area
    assert "Curriculum Breakdown" in text


def test_non_sem_tabs_not_clicked(fake_tabs):
    html, _, messages = fake_tabs
    assert "<body data-clicked" not in html   # the page's own script mentions the name
    assert "other tabs left unclicked: ['All Departments']" in messages
