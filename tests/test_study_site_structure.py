"""Offline tests for spec 04: no internet, no browser."""
import csv
import re
import sys

import pytest
from bs4 import BeautifulSoup

from tools import study_site_structure as ss

ROOT_PAGES_CSV = ss.PAGES_CSV
INDEX = ss.FIXTURE_DIR / "index.csv"
needs_fixtures = pytest.mark.skipif(
    not INDEX.exists(), reason="fixtures are written when you run the full study")


def make_study(address="https://uoli.edu.pk/x", selector="main", body="", content="", **measure):
    base = {"content": {"selector": selector, "chars": len(content), "no_main": False},
            "landmarks": {}, "body_chars": len(body), "furniture": {}, "counters": [],
            "tables": {}, "cards": {}, "hidden": {}, "carousel": {}, "images": {}}
    base.update(measure)
    return ss.PageStudy(address, "t", measure=base, body_text=body, content_text=content)


# ── sample list ─────────────────────────────────────────────────────────────

def test_sample_list_has_25_unique_pages():
    pages = ss.sample_pages(None)
    assert len(pages) == 25
    assert len({a for a, _ in pages}) == 25


def test_sample_pages_are_kept_rows_of_pages_csv():
    assert ss.check_against_pages_csv(ss.sample_pages(None)) == []


def test_limit_cuts_the_list():
    assert len(ss.sample_pages(3)) == 3


def test_delay_is_at_least_one_second():
    assert ss.DELAY_SECONDS >= 1.0


# ── stable wait ─────────────────────────────────────────────────────────────

def test_wait_until_stable_stops_after_three_equal_readings():
    sizes = iter([10, 20, 20, 20, 99])
    seconds, stable = ss.wait_until_stable(lambda: next(sizes), lambda ms: None)
    assert stable and seconds == pytest.approx(1.5)


def test_wait_until_stable_gives_up_on_a_page_that_keeps_changing():
    counter = iter(range(1000))
    seconds, stable = ss.wait_until_stable(lambda: next(counter), lambda ms: None, max_seconds=2)
    assert not stable and seconds >= 2


# ── file names and fixtures ─────────────────────────────────────────────────

def test_fixture_name():
    assert ss.fixture_name("https://uoli.edu.pk") == "home.html"
    assert ss.fixture_name("https://uoli.edu.pk/about/vc-message") == "about__vc-message.html"
    assert len(ss.fixture_name("https://uoli.edu.pk/" + "a" * 300)) <= 85


def test_clean_html_removes_noise_but_keeps_text_and_inline_style():
    html = ('<html><head><style>p{}</style><link rel="stylesheet" href="a.css"></head><body>'
            '<script>alert(1)</script><svg><path/></svg>'
            '<img src="data:image/png;base64,AAAA1234=="><p style="display:none">Hidden fact</p>'
            '<p>Real text</p></body></html>')
    out = ss.clean_html(html)
    for gone in ("<script", "<style", "<svg", "stylesheet", "base64"):
        assert gone not in out
    assert "Real text" in out and "display:none" in out and "data:removed" in out


def test_shrink_leaves_a_small_file_alone():
    assert ss.shrink_to_limit("<p>hi</p>", 1000) == ("<p>hi</p>", "")


def test_shrink_cuts_the_largest_repeated_block():
    slides = "".join('<div class="slide">' + "same words " * 50 + "</div>" for _ in range(40))
    html = f"<html><body><p>Keep me</p><section>{slides}</section></body></html>"
    out, note = ss.shrink_to_limit(html, 3000)
    assert len(out.encode()) <= 3000
    assert "Keep me" in out and out.count("same words") == 50
    assert "removed 39 repeated copies" in note


def test_shrink_says_when_it_cannot_fit():
    html = "<html><body><p>" + "unique " * 2000 + "</p></body></html>"
    _, note = ss.shrink_to_limit(html, 100)
    assert "STILL OVER" in note


# ── old scraper comparison ──────────────────────────────────────────────────

CONTENT = "Fee Structure\nTuition fee per semester is thirty thousand rupees\nHostel fee is ten thousand rupees"


def test_compare_old_counts_menu_garbage():
    old = ("# Fee Structure\nTuition fee per semester is thirty thousand rupees\n"
           "Hostel fee is ten thousand rupees\nScholarships\nStudent Affairs\nOnline Admissions")
    result = ss.compare_old(old, CONTENT)
    assert 0 < result.garbage_pct < 50
    assert not result.lost
    assert "Scholarships" in result.garbage_examples


def test_compare_old_finds_lost_content():
    result = ss.compare_old("# Fee Structure", CONTENT)
    assert result.lost and result.lost_count == 2
    assert any("Tuition fee" in line for line in result.lost_examples)


def test_compare_old_ignores_table_and_marker_format():
    content = "Program\tSeats\nBS Computer Science program\t60 seats available"
    old = "| Program | Seats |\n| BS Computer Science program | 60 seats available |"
    result = ss.compare_old(old, content)
    assert not result.lost and result.garbage_pct == 0


def test_run_old_extractor_records_a_crash_and_keeps_going():
    def broken(soup, url):
        raise ValueError("boom")
    result = ss.run_old_extractor(broken, "<p>x</p>", "https://uoli.edu.pk", "x")
    assert "ValueError" in result.error


def test_run_old_extractor_passes_text_through():
    result = ss.run_old_extractor(lambda soup, url: soup.get_text(), "<p>Fee Structure here today</p>",
                                  "https://uoli.edu.pk", "Fee Structure here today")
    assert result.error == "" and not result.lost


# ── across pages ────────────────────────────────────────────────────────────

def test_repeated_lines_uses_half_of_the_pages():
    texts = {"a": "Skip to main content\nOnly a", "b": "Skip to main content\nOnly b",
             "c": "Skip to main content", "d": "Other"}
    rows = ss.repeated_lines(texts)
    assert rows[0][:2] == (3, "skip to main content") and len(rows) == 1


def test_check_known_lines():
    repeated = [(20, "skip to main content", "label"), (18, "+92 (824) 410051", "long/contact"),
                (12, "developed by someone", "label")]
    found = {name: ok for name, ok, _ in ss.check_known_lines(repeated)}
    assert found["Skip to main content"] and found["phone number in the header"]
    assert found["Developed by"]
    assert not found["UOLI AI Assistant"] and not found["email in the header"]


def test_best_content_selector_says_n_of_m():
    assert ss.best_content_selector(["main"] * 24 + ["div.page"]) == ("main", 24, 25)
    assert ss.best_content_selector(["", ""]) == ("", 0, 0)


def test_counter_order():
    assert ss.counter_order([{"n": "100+", "before": "", "after": "Faculty"}]) == "label AFTER the number"
    assert ss.counter_order([{"n": "100+", "before": "Faculty", "after": ""}]) == "label BEFORE the number"
    assert ss.counter_order([]) == "no counters"


def test_furniture_summary_reports_same_text():
    item = {"selector": "header.top", "hash": "1", "where": "dom"}
    other = dict(item, hash="2")
    studies = [make_study(furniture={"header": item}), make_study(furniture={"header": other})]
    row = next(r for r in ss.furniture_summary(studies) if r[0] == "header")
    assert row[1] == "2 of 2" and row[3].startswith("no")
    assert next(r for r in ss.furniture_summary(studies) if r[0] == "footer")[1] == "0 of 2"


def test_apply_corpus_results_marks_a_page_with_no_own_content():
    shared = "\n".join(f"Menu item {i}" for i in range(6))
    studies = [make_study(f"https://uoli.edu.pk/{n}", body=shared, content=shared) for n in "abcde"]
    studies.append(make_study("https://uoli.edu.pk/real", body=shared + "\nThe university offers many programs in science.",
                              content="The university offers many programs in science."))
    ss.apply_corpus_results(studies)
    assert studies[0].own_content is False and studies[-1].own_content is True


# ── rows and report ─────────────────────────────────────────────────────────

def test_page_row_matches_header_length():
    assert len(ss.page_row(make_study())) == len(ss.CSV_HEADER)
    assert len(ss.page_row(ss.PageStudy("https://uoli.edu.pk", "home", status="blocked"))) == len(ss.CSV_HEADER)


def test_report_has_all_five_sections():
    studies = [make_study(body="a\nb", content="a") for _ in range(2)]
    for s in studies:
        s.old = ss.OldTest(garbage_pct=12.5, lost=True, lost_count=1)
        s.measure["hidden"] = {"controls": 0}
        s.measure["carousel"] = {"containers": 0}
    studies.append(ss.PageStudy("https://uoli.edu.pk/bad", "t", status="blocked", note="site blocked us"))
    text = ss.build_report(studies, [(2, "menu", "label")], ss.check_known_lines([]), ["a warning"], "2026-10-20")
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5."):
        assert heading in text
    assert "`main` — 2 of 2 pages agree" in text and "BLOCKED" in text and "a warning" in text


def test_report_shows_lost_lines_and_separates_faculty_cards():
    study = make_study(body="a", content="a",
                       cards={"count": 4, "people": 2, "example": "Dr Ali — HOD", "selector": "div.faculty-card"})
    study.old = ss.OldTest(garbage_pct=0.0, lost=True, lost_count=1, lost_examples=["Admission closes on 5 May"])
    study.measure["hidden"] = {"controls": 0}
    study.measure["carousel"] = {"containers": 0}
    text = ss.build_report([study], [], ss.check_known_lines([]), [], "2026-10-20")
    assert "Admission closes on 5 May" in text
    assert "**Faculty/HOD cards:** 2 on" in text and "Dr Ali" in text


def test_repeated_line_kind_is_about_length_not_meaning():
    rows = ss.repeated_lines({"a": "Hostels\nAn address line that is clearly longer than forty characters", "b": "Hostels\nAn address line that is clearly longer than forty characters"})
    kinds = {key: kind for _, key, kind in rows}
    assert kinds["hostels"].startswith("short")
    assert kinds[next(k for k in kinds if k.startswith("an address"))] == "long or contact"


# ── safe import of the old extractor ────────────────────────────────────────

def test_load_old_extractor_keeps_ocr_off_and_restores_sys_modules():
    sentinel = object()
    sys.modules.pop("web_scraper", None)
    sys.modules["easyocr"] = sentinel
    try:
        extractor = ss.load_old_extractor()
        assert sys.modules["easyocr"] is sentinel
        assert sys.modules["web_scraper"].OCR_AVAILABLE is False
        assert callable(extractor)
    finally:
        sys.modules.pop("easyocr", None)
    sys.modules.pop("web_scraper", None)
    ss.load_old_extractor()
    assert "easyocr" not in sys.modules


# ── fixtures (skipped until the full study has been run) ────────────────────

@needs_fixtures
def test_fixture_index_and_files():
    rows = list(csv.DictReader(open(INDEX, encoding="utf-8", newline="")))
    assert len(rows) == 25
    total = 0
    for row in rows:
        path = ss.FIXTURE_DIR / row["file"]
        assert path.exists()
        size = path.stat().st_size
        assert size <= ss.MAX_FIXTURE_BYTES, row["file"]
        total += size
    assert total <= ss.MAX_FIXTURES_TOTAL


@needs_fixtures
def test_fixtures_have_no_scripts_or_base64_and_old_extractor_runs():
    extractor = ss.load_old_extractor()
    for path in ss.FIXTURE_DIR.glob("*.html"):
        html = path.read_text(encoding="utf-8")
        assert "<script" not in html and not re.search(r"base64,[A-Za-z0-9+/=]{20}", html), path.name
        extractor(BeautifulSoup(html, ss.PARSER), "https://uoli.edu.pk/" + path.stem)
