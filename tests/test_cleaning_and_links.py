"""Offline tests for spec 06: cleaning rules, site-wide notes and links."""
import config


def test_note_addresses_resolve_to_their_category():
    # The "#" part is dropped by _url_path(), so without the exact lookup every
    # note would be filed as the home page ("general").
    assert len(config.SITE_INFO_URLS) + len(config.PAGE_DIRECTORY_URLS) == 12
    for category in config.CATEGORIES:
        assert config.resolve_category(config.SITE_INFO_URLS[category]) == category
        assert config.resolve_category(config.PAGE_DIRECTORY_URLS[category]) == category


def test_note_addresses_are_distinct_and_keep_the_hash():
    addresses = list(config.SITE_INFO_URLS.values()) + list(config.PAGE_DIRECTORY_URLS.values())
    assert len(set(addresses)) == 12
    assert all("#" in a for a in addresses)


def test_home_page_keeps_its_own_category():
    assert config.resolve_category(config.HOME_URL) == "general"


# ── helpers ─────────────────────────────────────────────────────────────────
import csv
import logging
import re
import types
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import page_furniture
import web_scraper as ws

FIX = Path(__file__).parent / "fixtures" / "site"
INDEX = list(csv.DictReader(open(FIX / "index.csv", encoding="utf-8-sig")))
ADDRESS = {row["file"]: row["address"] for row in INDEX}
LEFTOVERS = ("Back to About UOLI", "Back to All Academic Departments", "Read Article",
             "View All Events")


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    monkeypatch.setattr(ws, "OCR_AVAILABLE", False)


def html_of(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def text_of(name: str) -> str:
    return ws._extract_text(BeautifulSoup(html_of(name), "html.parser"),
                            base_url=ADDRESS[name])


def small(body: str) -> str:
    html = f'<html><body><main id="main-content">{body}</main></body></html>'
    return ws._extract_text(BeautifulSoup(html, "html.parser"), base_url="https://uoli.edu.pk/x")


def home_note() -> str:
    soup = BeautifulSoup(html_of("home.html"), "html.parser")
    return ws.build_site_note(ws.extract_site_info(soup, config.HOME_URL))


class StubKb:
    """Records what would be stored: {address: category}."""
    def __init__(self):
        self.stored = {}
        self.texts = {}

    def load_scraped_page(self, text, url, category="general", page_hash=None,
                          legacy_hash=None, source_type="web_scrape"):
        self.stored[url] = category
        self.texts[url] = text
        return types.SimpleNamespace(status="added")


def run_scrape(monkeypatch, tmp_path, pages: dict, include_guaranteed=True):
    """scrape_all_pages on saved pages: {address: html}. No network, no database."""
    def fake_fetch(url, session=None):
        soup = BeautifulSoup(pages[url], "html.parser")
        ws._SITE_INFO[url] = ws.extract_site_info(soup, url)
        if ws._is_empty_page(soup):
            return None, "empty"
        return ws._extract_text(soup, base_url=url), "ok"

    monkeypatch.setattr(ws, "fetch_page", fake_fetch)
    monkeypatch.setattr(ws, "GUARANTEED_PAGES", list(pages))
    monkeypatch.setattr(ws, "time", types.SimpleNamespace(sleep=lambda _s: None))
    monkeypatch.setattr(ws, "FURNITURE_CACHE", str(tmp_path / "furniture.json"))
    monkeypatch.setattr(ws, "CONTACT_LINES_CACHE", str(tmp_path / "contact.json"))
    monkeypatch.setattr(ws, "SITE_NOTE_CACHE", str(tmp_path / "note.json"))
    ws._PDF_REFERRERS.clear()
    kb = StubKb()
    results = ws.scrape_all_pages(kb, include_guaranteed=include_guaranteed)
    return results, kb


# ── link addresses in page text ─────────────────────────────────────────────

def test_how_to_apply_has_portal_address():
    assert "Access Online Admission Portal (https://uoli.ac.pk/admissions/)" in text_of(
        "admissions__how-to-apply.html")


def test_contact_has_maps_address():
    maps = BeautifulSoup(html_of("contact.html"), "html.parser").find(
        "a", href=re.compile("google.com/maps"))["href"]
    assert f"Get Directions on Google Maps ({maps})" in text_of("contact.html")


def test_mailto_added_only_when_hidden():
    assert "Contact Secretariat (registrar@uoli.edu.pk)" in text_of("offices__registrar.html")
    line = next(l for l in text_of("about__vc-message.html").splitlines()
                if "Contact VC Secretariat" in l)
    assert line.count("vc@uoli.edu.pk") == 1


def test_internal_links_not_inline():
    assert not re.search(r"\(https://uoli\.edu\.pk/(?!Docs)", text_of("about.html"))


def test_document_link_is_full_address():
    assert ("Undergraduate Rules PDF (https://uoli.edu.pk/Docs/"
            "UOL-Notification-of-Under-Graduate-Rules.pdf)") in text_of(
                "academics__programs__bscs.html")


def test_table_cell_keeps_address():
    text = small('<table><tr><td>Fee slip</td><td><a href="https://bank.example.pk/slip">'
                 'Download</a></td></tr></table>')
    assert "| Fee slip | Download (https://bank.example.pk/slip) |" in text


def test_link_without_href_skipped():
    text = small("<p>Contact</p><button>Apply now</button><a>Nothing here</a>")
    assert "Apply now" in text and "(" not in text


def test_same_words_and_address_written_once():
    text = small('<p><a href="https://example.org/a">https://example.org/a</a></p>')
    assert text.count("https://example.org/a") == 1


# ── the site-wide note ──────────────────────────────────────────────────────

def test_site_note_contents():
    soup = BeautifulSoup(html_of("home.html"), "html.parser")
    info = ws.extract_site_info(soup, config.HOME_URL)
    note = ws.build_site_note(info)
    assert [n for n, _ in info["social"]] == ["Facebook", "Instagram", "LinkedIn", "YouTube"]
    assert [w for w, _ in info["portals"]] == [
        "Online Admissions", "Fee Challan", "E-Office", "DIMS"]
    assert len(info["pdfs"]) == 12      # 11 rules documents + the admission form
    for fact in ("Phone: +92 (824) 410051", "Email: info@uoli.edu.pk",
                 "Address: University of Loralai, Quetta Road", "Website: uoli.edu.pk",
                 "ACCESS. QUALITY. RELEVANCE", "Chartered 2012",
                 "Admission Form: https://uoli.edu.pk/Docs/Admission-Form"):
        assert fact in note, fact


def test_site_note_leaves_out_credit():
    note = home_note()
    for junk in ("Developed by", "myasir", "Muhammad Yasir", "©", "Skip to main content",
                 "UOLI AI Assistant"):
        assert junk not in note, junk
    lines = note.splitlines()
    for heading in ("Navigation", "Contact Information", "Rules & Legal Regulations"):
        assert heading not in lines


def test_site_note_items_once():
    lines = [l for l in home_note().splitlines() if l and not l.endswith(":")]
    assert len(lines) == len(set(l.lower() for l in lines))
    assert "Online Admissions Admissions" not in home_note()


def test_note_stored_for_every_category(monkeypatch, tmp_path):
    results, kb = run_scrape(monkeypatch, tmp_path, {config.HOME_URL: html_of("home.html")})
    for category in config.CATEGORIES:
        assert kb.stored[config.SITE_INFO_URLS[category]] == category
    assert results["notes"] >= len(config.CATEGORIES)
    assert results["scraped"] == 1          # notes are counted apart from pages


def test_extra_footer_line_warns(monkeypatch, tmp_path, caplog):
    other = html_of("about.html").replace(
        "</footer>", "<p>Admission deadline extended to Friday</p></footer>", 1)
    pages = {config.HOME_URL: html_of("home.html"), ADDRESS["about.html"]: other}
    with caplog.at_level(logging.WARNING):
        results, kb = run_scrape(monkeypatch, tmp_path, pages)
    assert "Admission deadline extended to Friday" in caplog.text
    assert any("Admission deadline extended" in item for item in results["new_links"])
    assert "Admission deadline extended to Friday" in kb.texts[
        config.SITE_INFO_URLS["general"]]


def test_unknown_menu_link_reported(monkeypatch, tmp_path):
    home = html_of("home.html").replace(
        "</footer>", '<a href="/brand-new-page">Brand new</a></footer>', 1)
    results, _ = run_scrape(monkeypatch, tmp_path, {config.HOME_URL: home})
    assert f"new page: {config.HOME_URL}/brand-new-page" in results["new_links"]


# ── the page directory ──────────────────────────────────────────────────────

def _all_pages() -> dict:
    return {row["address"]: html_of(row["file"]) for row in INDEX}


def test_page_directory_per_category(monkeypatch, tmp_path):
    results, kb = run_scrape(monkeypatch, tmp_path, _all_pages())
    economics = ADDRESS["academics__programs__bs-economics.html"]
    pages = {u: c for u, c in kb.stored.items() if "#" not in u}
    assert economics not in pages
    for category in config.CATEGORIES:
        mine = [u for u, c in pages.items() if c == category]
        address = config.PAGE_DIRECTORY_URLS[category]
        if not mine:
            assert address not in kb.stored
            continue
        directory = kb.texts[address]
        for url in mine:
            assert url in directory, (category, url)
        assert economics not in directory
    assert ADDRESS["admissions__fees.html"] in kb.texts[config.PAGE_DIRECTORY_URLS["admissions"]]
    for address in config.PAGE_DIRECTORY_URLS.values():
        for line in kb.texts.get(address, "").splitlines()[1:]:
            assert not line.startswith(" "), f"entry without a title: {line}"
    assert f"How to Apply — {ADDRESS['admissions__how-to-apply.html']}" in kb.texts[
        config.PAGE_DIRECTORY_URLS["admissions"]]


def test_partial_run_keeps_directories(monkeypatch, tmp_path):
    results, kb = run_scrape(monkeypatch, tmp_path, {config.HOME_URL: html_of("home.html")},
                             include_guaranteed=False)
    assert not any("#pages-" in u for u in kb.stored)


# ── cleaning ────────────────────────────────────────────────────────────────

def test_furniture_keeps_undergraduate():
    pages = {ADDRESS[row["file"]]: text_of(row["file"]) for row in INDEX
             if "bs-economics" not in row["file"]}
    assert len(pages) >= page_furniture.MIN_PAGES_FOR_FREQUENCY
    assert page_furniture.find_furniture(pages) == set()


@pytest.mark.parametrize("name", ["about__vc-message.html",
                                  "academics__departments__computer-science.html",
                                  "home.html", "news__news-0.html", "news.html"])
def test_link_leftovers_removed(name):
    lines = {l.strip(" •#") for l in text_of(name).splitlines()}
    assert not lines & set(LEFTOVERS)


def test_see_more_paragraph_kept():
    assert "See more details about this program on the next page." in small(
        "<p>See more details about this program on the next page.</p>")


def test_link_leftover_button_removed():
    text = small('<p>Hello world.</p><a href="/about">Back to About UOLI</a>')
    assert "Back to" not in text and "Hello world." in text


# ── tags ────────────────────────────────────────────────────────────────────

def test_tags():
    from knowledge_base import _detect_content_type as ctype, _extract_department as dept
    base = "https://uoli.edu.pk"
    news = f"{base}/news/news-mou-district-education-office-loralai"
    assert dept(news) == "general"
    assert dept(f"{base}/academics/departments/computer-science") == "computer-science"
    assert dept(f"{base}/admissions/fees") == "general"
    assert dept("UOL-Notification-of-Conduct-Rules.pdf") == "all"
    assert ctype(f"{base}/research/oric", "offices", "web_scrape") == "office_info"
    assert ctype(f"{base}/offices/registrar", "offices", "web_scrape") == "office_info"
    assert ctype(f"{base}/about/vc-message", "overview", "web_scrape") == "leadership_message"
    assert ctype(f"{base}/about/leadership", "overview", "web_scrape") == "leadership_message"
    assert ctype(f"{base}/academics/departments/computer-science", "faculty",
                 "web_scrape") == "faculty_page"
    assert ctype(f"{base}/academics/faculty", "faculty", "web_scrape") == "faculty_page"
    for category in config.CATEGORIES:
        assert ctype(config.SITE_INFO_URLS[category], category, "web_scrape") == "site_info"
        assert ctype(config.PAGE_DIRECTORY_URLS[category], category,
                     "web_scrape") == "page_directory"


def test_dept_slugs_removed():
    from knowledge_base import KnowledgeBase
    assert not hasattr(KnowledgeBase, "_DEPT_SLUGS")


# ── spaces in link addresses ────────────────────────────────────────────────

def test_spaces_in_addresses_become_percent_20():
    text = small('<p><a href="/Docs/Notification of Service Rules.pdf">Service Rules</a> '
                 '<a href="/Docs/Already%20Encoded.pdf">Encoded</a></p>')
    assert "Service Rules (https://uoli.edu.pk/Docs/Notification%20of%20Service%20Rules.pdf)" in text
    assert "Encoded (https://uoli.edu.pk/Docs/Already%20Encoded.pdf)" in text
    assert "%2520" not in text


def test_site_note_addresses_have_no_spaces():
    note = home_note()
    assert "Service Rules Notification: https://uoli.edu.pk/Docs/Notification%20of%20Service%20Rules.pdf" in note
    for line in note.splitlines():
        if "http" in line:
            assert " " not in line[line.index("http"):], line
