"""Offline tests: no internet, no browser."""
import csv

import pytest

from tools import find_site_urls as fs
from tools import url_health_check as uh

HOME_TEXT = "Home Welcome " + "home words " * 60
SIG = ["Oops this page does not exist"]
SITE = fs.SITE


def body(label: str) -> str:
    return f"{label} " + "real content here " * 30


class FakeRenderer:
    """Pretends to be the browser. `pages` maps address -> PageView (or an exception)."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.opened: list[str] = []

    def render_page(self, url: str):
        self.opened.append(url)
        result = self.pages.get(url)
        if isinstance(result, BaseException):
            raise result
        if result is None:  # made-up address: the site shows the home page
            return fs.PageView(SITE, HOME_TEXT, "Home", [], False, 404)
        return result


def view(url: str, text: str, links=(), title="T", login=False, status=404) -> fs.PageView:
    return fs.PageView(url, text, title, list(links), login, status)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(uh.time, "sleep", lambda s: None)


def run_crawl(pages, sitemap=(), limit=None, sleeps=None):
    log = sleeps if sleeps is not None else []
    return fs.crawl(FakeRenderer(pages), list(sitemap), SIG, HOME_TEXT, limit, sleep=log.append)


# ── link filter ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("href", [
    "mailto:a@uoli.edu.pk", "tel:+92300", "https://example.com/x", "https://uoli.edu.pk/logo.png",
    "https://uoli.edu.pk/news?page=2", "javascript:void(0)", "", "https://sub.uoli.edu.pk/x",
    "https://uoli.edu.pk/Sports%20Week%20Draw.xlsx", "/files/form.DOCX"])
def test_skipped_links(href):
    assert fs.clean_link(href, SITE) is None


def test_fragment_slash_www_are_one_link():
    keys = {fs.clean_link(h, SITE)[0] for h in (
        "/about", "/about/", "https://www.uoli.edu.pk/about#team", "http://uoli.edu.pk/about")}
    assert keys == {"https://uoli.edu.pk/about"}


def test_pdf_and_page_kinds():
    assert fs.clean_link("/files/a.PDF", SITE)[1] == "pdf"
    assert fs.clean_link("/about", SITE)[1] == "page"


def test_section_is_first_path_part_only():
    assert fs.section_of("https://uoli.edu.pk/academics/departments/x") == "academics"
    assert fs.section_of("https://uoli.edu.pk") == "(home)"


# ── sitemap ─────────────────────────────────────────────────────────────────

URLSET = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          '<url><loc>https://uoli.edu.pk/a</loc></url><url><loc> https://uoli.edu.pk/b </loc></url></urlset>')
INDEX = ('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
         '<sitemap><loc>https://uoli.edu.pk/pages.xml</loc></sitemap></sitemapindex>')


def test_parse_sitemap_urlset():
    assert fs.parse_sitemap(URLSET) == (["https://uoli.edu.pk/a", "https://uoli.edu.pk/b"], False)


def test_read_sitemap_follows_index():
    files = {f"{SITE}/sitemap.xml": INDEX, "https://uoli.edu.pk/pages.xml": URLSET}
    urls, warnings = fs.read_sitemap(files.__getitem__)
    assert urls == ["https://uoli.edu.pk/a", "https://uoli.edu.pk/b"] and warnings == []


def test_missing_sitemap_is_a_warning_not_a_crash():
    def boom(url):
        raise uh.FetchTimeout("down")
    urls, warnings = fs.read_sitemap(boom)
    assert urls == [] and "home page only" in warnings[0]


# ── judging a page ──────────────────────────────────────────────────────────

def judge(requested, final, text, seen=None, login=False):
    return fs.judge_new_page(requested, final, text, SIG, HOME_TEXT, seen or {}, login)


def test_page_with_own_content_is_real():
    assert judge(f"{SITE}/about", f"{SITE}/about", body("About"))[0] == fs.REAL


def test_not_found_text_is_never_real():  # acceptance 4
    text = "Oops this page does not exist " + body("x")
    assert judge(f"{SITE}/gone", f"{SITE}/gone", text)[0] == fs.NOT_FOUND


def test_made_up_address_that_jumps_elsewhere_is_never_real():  # acceptance 3
    result, dup, _ = judge(f"{SITE}/zz-fake", f"{SITE}/academics", body("Academics"))
    assert (result, dup) == (fs.DUPLICATE, f"{SITE}/academics")


def test_made_up_address_showing_home_text_is_duplicate():
    result, dup, _ = judge(f"{SITE}/zz-fake", f"{SITE}/zz-fake", HOME_TEXT)
    assert (result, dup) == (fs.DUPLICATE, SITE)


def test_home_page_itself_is_real():
    assert judge(SITE, SITE, HOME_TEXT)[0] == fs.REAL


def test_same_text_as_earlier_page_is_duplicate():
    seen = {fs.text_hash(body("Same")): f"{SITE}/first"}
    result, dup, _ = judge(f"{SITE}/second", f"{SITE}/second", body("Same"), seen)
    assert (result, dup) == (fs.DUPLICATE, f"{SITE}/first")


def test_little_text_is_thin():
    assert judge(f"{SITE}/x", f"{SITE}/x", "short page text here")[0] == fs.THIN


def test_login_page_is_skipped():
    assert judge(f"{SITE}/portal", f"{SITE}/portal", body("Login"), login=True)[:3:2] == (fs.SKIPPED, "login")


# ── crawler ─────────────────────────────────────────────────────────────────

def site(*extra):
    pages = {
        SITE: view(SITE, HOME_TEXT, [("/about", "About"), ("/files/rules.pdf", "Rules"), ("/about/", "again")]),
        f"{SITE}/about": view(f"{SITE}/about", body("About"), [("/about/team", "Team")]),
        f"{SITE}/about/team": view(f"{SITE}/about/team", body("Team")),
    }
    pages.update(extra)
    return pages


def test_crawl_follows_links_one_row_per_address():
    result = run_crawl(site())
    addresses = [r.address for r in result.rows]
    assert sorted(addresses) == sorted({SITE, f"{SITE}/about", f"{SITE}/about/team"})
    assert all(r.result == fs.REAL for r in result.rows)
    about = next(r for r in result.rows if r.address.endswith("/about"))
    assert about.found_on == SITE and about.in_sitemap is False


def test_crawl_collects_pdf_links():
    assert list(run_crawl(site()).pdfs) == [f"{SITE}/files/rules.pdf"]


def test_sitemap_pages_are_all_in_report_and_marked():  # acceptance 1
    result = run_crawl(site(), sitemap=[f"{SITE}/about/team", f"{SITE}/only-in-sitemap"])
    by_addr = {r.address: r for r in result.rows}
    assert by_addr[f"{SITE}/only-in-sitemap"].in_sitemap is True
    assert by_addr[f"{SITE}/about/team"].in_sitemap is True
    assert by_addr[f"{SITE}/about"].in_sitemap is False


def test_made_up_address_is_never_real_in_a_crawl():  # acceptance 3
    result = run_crawl(site(), sitemap=[f"{SITE}/zz-made-up"])
    fake = next(r for r in result.rows if r.address.endswith("zz-made-up"))
    assert fake.result == fs.DUPLICATE


def test_redirect_target_gets_its_own_row():
    pages = site()
    pages[f"{SITE}/old"] = view(f"{SITE}/old", body("New"), status=404)
    pages[f"{SITE}/old"].final = f"{SITE}/newer"
    pages[f"{SITE}/newer"] = view(f"{SITE}/newer", body("New"))
    result = run_crawl(pages, sitemap=[f"{SITE}/old"])
    by_addr = {r.address: r for r in result.rows}
    assert by_addr[f"{SITE}/old"].result == fs.DUPLICATE
    assert by_addr[f"{SITE}/newer"].result == fs.REAL


def test_depth_limit_stops_the_crawl():
    pages = {SITE: view(SITE, HOME_TEXT, [("/d1", "")])}
    for i in range(1, 6):
        pages[f"{SITE}/d{i}"] = view(f"{SITE}/d{i}", body(f"level {i}"), [(f"/d{i + 1}", "")])
    addresses = {r.address for r in run_crawl(pages).rows}
    assert f"{SITE}/d3" in addresses and f"{SITE}/d4" not in addresses


def test_page_cap(monkeypatch):
    monkeypatch.setattr(fs, "MAX_PAGES", 5)
    pages = {SITE: view(SITE, HOME_TEXT, [(f"/p{i}", "") for i in range(20)])}
    for i in range(20):
        pages[f"{SITE}/p{i}"] = view(f"{SITE}/p{i}", body(f"page {i}"))
    result = run_crawl(pages)
    assert len(result.rows) == 5 and "Stopped at 5 pages" in result.warnings[0]


def test_limit_option_stops_early():
    assert len(run_crawl(site(), limit=2).rows) == 2


def test_delay_is_never_below_one_second():  # acceptance 7
    sleeps: list[float] = []
    run_crawl(site(), sleeps=sleeps)
    assert uh.DELAY_SECONDS >= 1 and sleeps and min(sleeps) >= 1


def test_login_page_row_says_login_and_links_not_followed():
    pages = {SITE: view(SITE, HOME_TEXT, [("/portal", "")]),
             f"{SITE}/portal": view(f"{SITE}/portal", body("Sign in"), [("/secret", "")], login=True)}
    result = run_crawl(pages)
    portal = next(r for r in result.rows if r.address.endswith("/portal"))
    assert (portal.result, portal.reason) == (fs.SKIPPED, "login")
    assert not any(r.address.endswith("/secret") for r in result.rows)


def test_timeout_becomes_error_row():
    pages = {SITE: view(SITE, HOME_TEXT, [("/slow", "")]), f"{SITE}/slow": uh.FetchTimeout("slow")}
    slow = next(r for r in run_crawl(pages).rows if r.address.endswith("/slow"))
    assert slow.result == fs.ERROR and "timeout" in slow.reason


def test_blocked_page_waits_ten_seconds_then_errors():
    blocked = view(f"{SITE}/b", "Forbidden", status=403)
    pages = {SITE: view(SITE, HOME_TEXT, [("/b", "")]), f"{SITE}/b": blocked}
    sleeps: list[float] = []
    result = run_crawl(pages, sleeps=sleeps)
    row = next(r for r in result.rows if r.address.endswith("/b"))
    assert row.result == fs.ERROR and "blocked" in row.reason and fs.BLOCK_WAIT_SECONDS in sleeps


def test_ctrl_c_keeps_rows_done_so_far():
    pages = {SITE: view(SITE, HOME_TEXT, [("/x", "")]), f"{SITE}/x": KeyboardInterrupt()}
    result = run_crawl(pages)
    assert [r.address for r in result.rows] == [SITE]
    assert "Ctrl+C" in result.warnings[0]


def test_row_keeps_title_and_length_but_not_text():
    pages = {SITE: view(SITE, HOME_TEXT, title="  Home page ")}
    row = run_crawl(pages).rows[0]
    assert row.title == "Home page" and row.text_length == len(" ".join(HOME_TEXT.split()))
    assert HOME_TEXT not in str(row.as_list())


# ── PDFs ────────────────────────────────────────────────────────────────────

class PdfRenderer:
    def __init__(self, status=200):
        self.status = status

    def fetch(self, url):
        return uh.Fetched(self.status, url, "application/pdf", b"%PDF-1.4")


def fake_fetch(status, ctype="application/pdf", head=b"%PDF-1.4"):
    return lambda url: uh.Fetched(status, url, ctype, head)


def test_real_pdf(monkeypatch):
    monkeypatch.setattr(uh, "fetch_unblocked", fake_fetch(200))
    monkeypatch.setattr(fs, "pdf_size", lambda url: "10 KB")
    row = fs.check_pdf(f"{SITE}/a.pdf", SITE, "A", PdfRenderer())
    assert (row.result, row.size) == (fs.REAL_PDF, "10 KB")


def test_blocked_pdf_retried_in_browser_then_blocked(monkeypatch):
    monkeypatch.setattr(uh, "fetch_unblocked", fake_fetch(403))
    row = fs.check_pdf(f"{SITE}/a.pdf", SITE, "A", PdfRenderer(status=403))
    assert row.result == fs.BLOCKED and "403" in row.reason


def test_blocked_pdf_works_in_browser(monkeypatch):
    monkeypatch.setattr(uh, "fetch_unblocked", fake_fetch(403))
    monkeypatch.setattr(fs, "pdf_size", lambda url: "unknown")
    assert fs.check_pdf(f"{SITE}/a.pdf", SITE, "A", PdfRenderer(status=200)).result == fs.REAL_PDF


def test_web_page_instead_of_pdf(monkeypatch):
    monkeypatch.setattr(uh, "fetch_unblocked", fake_fetch(200, "text/html", b"<html>"))
    assert fs.check_pdf(f"{SITE}/a.pdf", SITE, "A", PdfRenderer()).result == fs.NOT_A_PDF


def test_every_pdf_gets_a_result_and_ctrl_c_keeps_done(monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return uh.Fetched(200, url, "application/pdf", b"%PDF")

    monkeypatch.setattr(uh, "fetch_unblocked", fetch)
    monkeypatch.setattr(fs, "pdf_size", lambda url: "1 KB")
    rows: list = []
    pdfs = {f"{SITE}/{n}.pdf": (SITE, n) for n in "abc"}
    with pytest.raises(KeyboardInterrupt):
        fs.check_pdfs(pdfs, PdfRenderer(), rows, sleep=lambda s: None)
    assert [r.result for r in rows] == [fs.REAL_PDF]


# ── old -> new ──────────────────────────────────────────────────────────────

REAL_PAGES = [f"{SITE}/about/vc-message", f"{SITE}/academics/departments/computer-science",
              f"{SITE}/academics/faculty", f"{SITE}/research/oric", f"{SITE}/services/library"]


@pytest.mark.parametrize("old,expected", [
    ("https://uoli.edu.pk/vc-message/", f"{SITE}/about/vc-message"),
    ("https://uoli.edu.pk/faculty/computer-science/", f"{SITE}/academics/departments/computer-science"),
    ("https://uoli.edu.pk/faculty/", f"{SITE}/academics/faculty"),
    ("https://uoli.edu.pk/oric/", f"{SITE}/research/oric"),
])
def test_clear_matches(old, expected):
    assert fs.match_old_link(old, REAL_PAGES)[0] == expected


@pytest.mark.parametrize("old", [
    "https://uoli.edu.pk/bus-routes/", "https://uoli.edu.pk/fee-structure/",
    "https://uoli.edu.pk/uol-signs-mou-with-pakistan-bait-ul-mal"])
def test_unclear_means_no_match(old):
    assert fs.match_old_link(old, REAL_PAGES) == ("", "no match found")


def test_every_old_link_has_a_row():  # acceptance 5
    old = [{"url": "https://uoli.edu.pk/vc-message/", "type": "page"},
           {"url": "https://uoli.edu.pk/x.pdf", "type": "pdf"},
           {"url": "https://uoli.edu.pk/logo.jpeg", "type": "image"}]
    pages = [fs.PageRow(a, "", "", 300, "", False, fs.REAL, "", "") for a in REAL_PAGES]
    pages.append(fs.PageRow(f"{SITE}/dup", "", "", 300, "", False, fs.DUPLICATE, SITE, ""))
    rows = fs.match_old_links(old, pages, [])
    assert [r[0] for r in rows] == [o["url"] for o in old]
    assert rows[0][1] == f"{SITE}/about/vc-message" and rows[1][1] == rows[2][1] == "no match found"


def test_only_real_pages_are_suggested():
    pages = [fs.PageRow(f"{SITE}/about/vc-message", "", "", 300, "", False, fs.DUPLICATE, SITE, "")]
    assert fs.match_old_links([{"url": "https://uoli.edu.pk/vc-message/", "type": "page"}],
                              pages, [])[0][1] == "no match found"


# ── reports ─────────────────────────────────────────────────────────────────

def test_reports_have_the_spec_columns(tmp_path):
    pages = [fs.PageRow(f"{SITE}/a", "a", "A", 300, SITE, False, fs.REAL, "", "ok")]
    pdfs = [fs.PdfRow(f"{SITE}/a.pdf", SITE, "A", "1 KB", fs.REAL_PDF, "ok")]
    old = [["https://uoli.edu.pk/zzz", "no match found", "no match found"]]
    paths = fs.write_reports(pages, pdfs, old, ["a warning"], out_dir=tmp_path)
    assert len(paths) == 4
    read = lambda p: list(csv.reader(p.open(encoding="utf-8-sig")))
    assert read(paths[0])[0] == ["address", "section", "title", "text length", "found on",
                                 "in sitemap", "result", "duplicate of", "reason"]
    assert read(paths[0])[1][5] == "no"
    assert read(paths[1])[0] == ["address", "found on", "link text", "size", "result", "reason"]
    assert read(paths[2])[0][0] == "old address"
    summary = paths[3].read_text(encoding="utf-8")
    assert "a warning" in summary and "Real pages not in the sitemap (1)" in summary
    assert "Old links with no match (1 of 1)" in summary and "Do the human check" in summary


def test_limited_run_does_not_overwrite_full_run(tmp_path):
    full = fs.write_reports([], [], [], [], out_dir=tmp_path)
    limited = fs.write_reports([], [], [], [], out_dir=tmp_path, tag="_limit3")
    assert not set(full) & set(limited)
