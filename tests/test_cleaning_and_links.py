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
             "View All Events", "Return to All Academic Departments",
             "Explore All Campus Facilities")


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
                                  "academics__departments__commerce.html",
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


# ── spec 07b: placeholder, navigation words, related block, staff cards ─────

COMMERCE = "academics__departments__commerce.html"
CS_DEPT = "academics__departments__computer-science.html"
IT_OFFICE = "offices__directorate-of-it.html"
NEWS_ARTICLE = "news__admissions-open-fall-2026-phase-ii-university-of-loralai.html"


def test_no_image_unavailable():
    # Images are blocked while the page loads, so every photo shows this text.
    assert "Image Unavailable" in html_of(COMMERCE)     # the copy really has it
    for row in INDEX:
        for line in text_of(row["file"]).splitlines():
            assert line.strip(" •#") != "Image Unavailable", row["file"]
            assert "Status: Image Unavailable" not in line, row["file"]


def test_image_unavailable_in_sentence_kept():
    text = small("<p>Photo: Image Unavailable for this event.</p>"
                 "<div><span>Image Unavailable</span></div><p>" + "Real text. " * 10 + "</p>")
    assert "Photo: Image Unavailable for this event." in text
    assert "Image Unavailable" not in text.replace("Photo: Image Unavailable for this event.", "")


def test_placeholder_is_never_a_person_badge():
    cards = ('<div><h3>Dr. Ali Khan</h3><p>Lecturer</p><span>Image Unavailable</span>'
             '<a href="mailto:ali@uoli.edu.pk">ali@uoli.edu.pk</a><button>Profile</button></div>'
             '<div><h3>Dr. Sara Khan</h3><p>Lecturer</p><a href="mailto:sara@uoli.edu.pk">s</a>'
             '<button>Profile</button></div>')
    text = small(cards)
    assert "Image Unavailable" not in text and "Dr. Ali Khan — Lecturer." in text


def test_return_explore_removed():
    assert "Return to" in html_of(COMMERCE) and "Return to" in html_of(CS_DEPT)
    for name in (COMMERCE, CS_DEPT):
        lines = {l.strip(" •#") for l in text_of(name).splitlines()}
        assert "Return to All Academic Departments" not in lines
    text = small('<p>Campus text.</p><a href="/campus-life"><span>Explore All Campus Facilities</span></a>')
    assert "Explore All" not in text and "Campus text." in text
    # Only a whole link or button is removed, never a sentence.
    assert "Explore all the options in the prospectus." in small(
        "<p>Explore all the options in the prospectus.</p>")


def article(path: str, body: str) -> str:
    html = f'<html><body><main id="main-content">{body}</main></body></html>'
    return ws._extract_text(BeautifulSoup(html, "html.parser"), base_url="https://uoli.edu.pk" + path)


RELATED = ('<p>' + 'Own article text. ' * 10 + '</p><div><h3>{heading}</h3>'
           '<div><h4>Another Article Title</h4><span>2026-10-08</span></div></div>')


def test_news_related_block_removed():
    text = text_of(NEWS_ARTICLE)
    assert "Admissions Open: Fall 2026 (Phase-II)" in text and "2026-10-03" in text
    assert "Related Official Notices" not in text
    for title in ("Global Food Security Forum", "Board of Studies (BoS), Department of Economics",
                  "Strengthening Academic Partnerships"):
        assert title not in text


def test_related_block_without_heading_kept():
    assert "Another Article Title" in article("/news/some-article", RELATED.format(heading="More Notices"))
    # The list page is not an article, so even the exact heading is left alone there.
    assert "Another Article Title" in article("/news", RELATED.format(heading="Related Official Notices"))
    assert "Another Article Title" not in article(
        "/research/innovation/events/an-event", RELATED.format(heading="Related Official Notices"))


def test_news_list_page_unchanged():
    assert "Related Official Notices" not in html_of("news.html")
    assert "Return to" not in html_of("news.html")


def test_it_office_staff_lines():
    office = "Directorate of Information Technology (IT)"
    lines = text_of(IT_OFFICE).splitlines()
    staff = [l for l in lines if l.startswith(("Mr. ", "Engr. ")) and " — " in l]
    assert staff == [
        f"Mr. Muhammad Ismail — Director IT / Lecturer CS, {office}. "
        "Email: director.it@uoli.edu.pk. Phone: 0320 1467001.",
        f"Mr. Khawar Abbas — Deputy Director IT / HOD CS, {office}. "
        "Email: deputydirector.it@uoli.edu.pk.",
        f"Engr. Noor Afzal — Network & Systems Administrator, {office}. Email not listed.",
        f"Engr. Faisal Khan — System Administrator, {office}. Email not listed.",
        f"Mr. Meer Habib Ullah — Clerk, {office}. Email not listed.",
        f"Mr. Muhammad Shafique — Clerk, {office}. Email not listed.",
    ]
    # The top "Officer In-Charge" box is not a card: no second line for the Director.
    assert sum(1 for l in lines if l.startswith("Mr. Muhammad Ismail — ")) == 1
    assert not [l for l in lines if "[EMAIL_ADDRESS]" in l]


def test_staff_phone_in_plain_text_kept():
    line = next(l for l in text_of(IT_OFFICE).splitlines() if l.startswith("Mr. Muhammad Ismail — "))
    assert "Phone: 0320 1467001." in line


def test_registrar_staff_keep_their_extension():
    lines = text_of("offices__registrar.html").splitlines()
    zain = next(l for l in lines if l.startswith("Mr. Zain Ul Abdeen — "))
    assert "Email: deputyregistrar@uoli.edu.pk." in zain and "(Ext. 104)" in zain


def card(name: str, job: str, mail: str = "", extra: str = "") -> str:
    link = f'<a href="mailto:{mail}">{mail}</a>' if mail else ""
    return f"<div><h4>{name}</h4><p>{job}</p>{link}{extra}</div>"


def office_page(body: str, path: str = "/offices/test-office") -> str:
    html = ('<html><body><main id="main-content"><header><h1>Test Office</h1></header><p>'
            + "Own text of the office. " * 10 + f"</p>{body}</main></body></html>")
    return ws._extract_text(BeautifulSoup(html, "html.parser"), base_url="https://uoli.edu.pk" + path)


def test_staff_card_name_from_top_box_same_job():
    top = "<div><h4>Mr. Top Person</h4><p>Director</p></div>"
    same = office_page(top + "<div>" + card("", "Director", "d@uoli.edu.pk")
                       + card("Mr. Other One", "Clerk", "c@uoli.edu.pk") + "</div>")
    assert "Mr. Top Person — Director, Test Office. Email: d@uoli.edu.pk." in same
    other = office_page(top + "<div>" + card("", "Registrar", "r@uoli.edu.pk")
                        + card("Mr. Other One", "Clerk", "c@uoli.edu.pk") + "</div>")
    assert "Name not listed — Registrar, Test Office. Email: r@uoli.edu.pk." in other
    assert "Top Person — Registrar" not in other and "Other One — Registrar" not in other


def test_staff_cards_only_on_offices():
    body = ("<div>" + card("Mr. A One", "Clerk", "a@uoli.edu.pk")
            + card("Mr. B Two", "Clerk", "b@uoli.edu.pk") + "</div>")
    assert "Mr. A One — Clerk, Test Office. Email: a@uoli.edu.pk." in office_page(body)
    elsewhere = office_page(body, "/services/test")
    assert "Mr. A One — " not in elsewhere
    assert "Mr. A One" in elsewhere          # the text is still there, just not a person line


def test_staff_card_two_emails_uses_first(caplog):
    body = ("<div>" + card("Mr. A One", "Clerk", "first@uoli.edu.pk",
                           '<a href="mailto:second@uoli.edu.pk">second@uoli.edu.pk</a>')
            + card("Mr. B Two", "Clerk", "b@uoli.edu.pk") + "</div>")
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        text = office_page(body)
    assert "Mr. A One — Clerk, Test Office. Email: first@uoli.edu.pk." in text
    assert "second@uoli.edu.pk" not in text
    assert any("2 emails" in r.getMessage() for r in caplog.records)


def test_staff_same_name_same_email_is_one_line():
    body = ("<div>" + card("Mr. A One", "Clerk", "same@uoli.edu.pk")
            + card("Mr. A One", "Clerk", "same@uoli.edu.pk")
            + card("Mr. C Three", "Clerk", "c@uoli.edu.pk") + "</div>")
    text = office_page(body)
    assert text.count("same@uoli.edu.pk") == 1 and text.count("Mr. A One — ") == 1
    assert "Mr. C Three — Clerk, Test Office. Email: c@uoli.edu.pk." in text


def test_staff_different_names_same_email_second_has_no_email(caplog):
    body = ("<div>" + card("Mr. A One", "Clerk", "same@uoli.edu.pk")
            + card("Mr. B Two", "Clerk", "same@uoli.edu.pk")
            + card("Mr. C Three", "Clerk", "c@uoli.edu.pk") + "</div>")
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        text = office_page(body)
    assert text.count("same@uoli.edu.pk") == 1
    assert "Mr. A One — Clerk, Test Office. Email: same@uoli.edu.pk." in text
    assert "Mr. B Two — Clerk, Test Office. Email not listed." in text
    warning = next(r.getMessage() for r in caplog.records if "share the email" in r.getMessage())
    assert "Mr. A One" in warning and "Mr. B Two" in warning


def test_registrar_inayat_ullah_has_a_line(caplog):
    # His email on the site is Mr. Nasrullah's: a website data error. He keeps a line,
    # without that email, and the warning names both people.
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        lines = text_of("offices__registrar.html").splitlines()
    inayat = [l for l in lines if l.startswith("Mr. Inayat Ullah — ")]
    assert len(inayat) == 1
    assert "Clerk" in inayat[0] and "Email not listed." in inayat[0] and "(Ext. 115)" in inayat[0]
    assert sum(l.count("nasrullah@uoli.edu.pk") for l in lines) == 1
    assert next(l for l in lines if l.startswith("Mr. Nasrullah — ")).count("Email: nasrullah@uoli.edu.pk.") == 1
    assert any("Inayat Ullah" in r.getMessage() and "Nasrullah" in r.getMessage()
               for r in caplog.records)


def test_staff_without_title_in_a_roster_is_kept():
    body = ('<div class="roster">'
            + card("Mr. A One", "Clerk", "a@uoli.edu.pk").replace("<div>", '<div class="c">')
            + card("Zara Khan", "Accountant", "zara@uoli.edu.pk").replace("<div>", '<div class="c">')
            + card("Dr. C Three", "Officer", "c@uoli.edu.pk").replace("<div>", '<div class="c">')
            + "</div>")
    text = office_page(body)
    assert "Zara Khan — Accountant, Test Office. Email: zara@uoli.edu.pk." in text
    assert "Mr. A One — Clerk, Test Office. Email: a@uoli.edu.pk." in text


def test_untitled_boxes_outside_a_roster_are_not_staff():
    # Forms, no titled card beside them: they stay ordinary text.
    forms = "".join(f'<div class="f"><h4>Leave Form {n}</h4><p>PDF</p></div>' for n in (1, 2, 3))
    other = ('<div class="roster">' + card("Mr. A One", "Clerk", "a@uoli.edu.pk")
             + card("Mr. B Two", "Clerk", "b@uoli.edu.pk") + "</div>")
    text = office_page("<div>" + forms + "</div>" + other)
    assert "Leave Form 1" in text and "Leave Form 1 —" not in text


def test_person_cards_same_name_same_email_one_line():
    cards = ('<div><h3>Dr. Ali Khan</h3><p>Lecturer</p><a href="mailto:ali@uoli.edu.pk">a</a>'
             '<button>Profile</button></div>') * 2
    text = small(cards)
    assert text.count("Dr. Ali Khan — Lecturer.") == 1 and text.count("ali@uoli.edu.pk") == 1


def test_person_cards_different_names_same_email(caplog):
    cards = ('<div><h3>Dr. Ali Khan</h3><p>Lecturer</p><a href="mailto:x@uoli.edu.pk">a</a>'
             '<button>Profile</button></div>'
             '<div><h3>Dr. Sara Khan</h3><p>Lecturer</p><a href="mailto:x@uoli.edu.pk">a</a>'
             '<button>Profile</button></div>')
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        text = small(cards)
    assert "Dr. Ali Khan — Lecturer. Email: x@uoli.edu.pk." in text
    assert "Dr. Sara Khan — Lecturer. Email not listed." in text
    assert any("Ali Khan" in r.getMessage() and "Sara Khan" in r.getMessage() for r in caplog.records)


def test_staff_same_name_keeps_line_with_email():
    body = ("<div>" + card("Mr. A One", "Clerk") + card("Mr. A One", "Clerk", "a@uoli.edu.pk")
            + card("Mr. B Two", "Clerk", "b@uoli.edu.pk") + "</div>")
    text = office_page(body)
    assert text.count("Mr. A One — ") == 1
    assert "Mr. A One — Clerk, Test Office. Email: a@uoli.edu.pk." in text


def test_page_title_and_contact_boxes_are_not_staff_cards():
    body = ('<div><h2>Office Contact &amp; Location</h2>'
            '<a href="mailto:office@uoli.edu.pk">office@uoli.edu.pk</a></div>')
    text = office_page(body)
    assert "Office Contact & Location" in text and "Email:" not in text


def test_profile_cards_unchanged():
    people = lambda name: [l for l in text_of(name).splitlines() if "Email" in l]
    assert len(people(CS_DEPT)) == 9
    assert len(people("academics__faculty.html")) == 90


# ── spec 07b: the page body must survive the cleaning rules ─────────────────

BBA = "academics__programs__bba.html"
LOTS = "Own text of the page. " * 40


def test_every_page_keeps_its_own_title():
    # A rule that finds too big a box deletes the page body, the title with it.
    for row in INDEX:
        main = BeautifulSoup(html_of(row["file"]), "html.parser").select_one("main#main-content")
        title = main.find("h1").get_text(" ", strip=True)
        lines = [l.strip(" •#") for l in text_of(row["file"]).splitlines() if l.strip()]
        if row["file"] == "home.html":          # the hero heading is split over several lines
            assert len(" ".join(lines)) > 1000
            continue
        assert any(title in l for l in lines[:3]), row["file"]


def bba_after_browser_step() -> str:
    """The saved BBA page plus what the browser step adds to it: a table, the semester
    section and the FAQ section with an answer for EVERY question."""
    soup = BeautifulSoup(html_of(BBA), "html.parser")
    main = soup.select_one("main#main-content")
    sem = [b for b in main.find_all("button") if ws._SEM_BUTTON_RE.match(b.get_text(" ", strip=True))]
    questions = [b for b in main.find_all("button") if b.get_text(" ", strip=True).endswith("?")]
    sem[0].find_parent("div").insert_after(BeautifulSoup("<table><tr><td>Y-1</td></tr></table>", "html.parser"))
    main.append(BeautifulSoup('<section data-uoli-semesters=""><h3>Semester II</h3>'
                              "<table><tr><td>X-1</td></tr></table></section>", "html.parser"))
    pairs = "".join(f"<h3>{ws._faq_question_text(q)}</h3><p>Answer number {i}.</p>"
                    for i, q in enumerate(questions))
    main.append(BeautifulSoup(f'<section data-uoli-faq="">{pairs}</section>', "html.parser"))
    return ws._extract_text(soup, ADDRESS[BBA])


def test_faq_with_every_question_answered_keeps_the_page_body():
    # Live bug: once the other questions were deleted, the last one was alone in its
    # list and its "box" grew to the whole page, which was deleted with it.
    text = bba_after_browser_step()
    assert "Program Overview" in text and "Eligibility Criteria" in text
    assert "Undergraduate Rules PDF (https://uoli.edu.pk/Docs/" in text
    lines = text.splitlines()
    for i in range(8):
        assert f"Answer number {i}." in lines
    assert len(text) > 4000
    assert sum(1 for l in lines if l.endswith("?")) == 8      # each question once


def main_of(body: str):
    return BeautifulSoup(f'<html><body><main id="main-content">{body}</main></body></html>',
                         "html.parser").select_one("main#main-content")


def warned(caplog, what: str) -> bool:
    return any(what in r.getMessage() and "not removed" in r.getMessage() for r in caplog.records)


def test_related_block_over_half_the_page_is_kept(caplog):
    main = main_of(f"<p>Short article.</p><div><h3>Related Official Notices</h3><p>{LOTS}</p></div>")
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        ws._remove_related_block(main, "https://uoli.edu.pk/news/a")
    assert "Related Official Notices" in main.get_text() and warned(caplog, "related articles block")


def test_faq_box_over_half_the_page_is_kept(caplog):
    main = main_of(f"<div><p>{LOTS}</p><div><button>Is it open?</button></div></div>"
                   '<section data-uoli-faq=""><h3>Is it open?</h3><p>Yes.</p></section>')
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        ws._remove_faq_originals(main, "https://uoli.edu.pk/faq")
    assert "Own text of the page." in main.get_text() and warned(caplog, "FAQ question box")


def test_course_area_over_half_the_page_is_kept(caplog):
    main = main_of(f"<div><button>Sem 1</button><table><tr><td>{LOTS}</td></tr></table></div>"
                   "<p>Short.</p>"
                   '<section data-uoli-semesters=""><h3>Semester I</h3>'
                   "<table><tr><td>T-1</td></tr></table></section>")
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        ws._remove_course_area(main, "https://uoli.edu.pk/academics/programs/x")
    assert "Own text of the page." in main.get_text() and warned(caplog, "course area")


def test_filter_bar_button_over_half_the_page_is_kept(caplog):
    main = main_of(f"<div><button>All {LOTS}</button><button>B</button></div><p>Short.</p>")
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        ws._remove_filter_bars(main, "https://uoli.edu.pk/x")
    assert "Own text of the page." in main.get_text() and warned(caplog, "filter bar button")


def test_placeholder_that_is_the_whole_page_is_kept(caplog):
    main = main_of("<div><span>Image Unavailable</span></div>")
    with caplog.at_level(logging.WARNING, logger="web_scraper"):
        ws._remove_placeholders(main, "https://uoli.edu.pk/x")
    assert "Image Unavailable" in main.get_text() and warned(caplog, "image placeholder")


def test_small_blocks_are_still_removed():
    main = main_of(f"<p>{LOTS}</p><div><span>Image Unavailable</span></div>")
    ws._remove_placeholders(main, "https://uoli.edu.pk/x")
    assert "Image Unavailable" not in main.get_text()


def test_view_image_download_and_viewer_hint_dropped():
    text = small("<p>Gallery text of the page.</p><button>View Image</button><a href='/f.pdf'>Download</a>"
                 "<p>Click any photograph to open full-screen view.</p>"
                 "<p>Download the prospectus from the admissions portal.</p>")
    lines = text.splitlines()
    assert "View Image" not in lines and "Download" not in lines
    assert not [l for l in lines if l.startswith("Click any photograph")]
    # Only whole lines are labels: a sentence that starts with the word is kept.
    assert "Download the prospectus from the admissions portal." in lines
    assert "Gallery text of the page." in lines


def test_no_download_label_lines_on_the_registrar_page():
    assert "Download" in html_of("offices__registrar.html")
    assert "Download" not in text_of("offices__registrar.html").splitlines()
