"""Offline tests: no internet, no browser."""
import pytest

from tools import url_health_check as uh

HOME = "Home Welcome " + "x " * 200
NOT_FOUND_SIG = ["Oops this page does not exist"]


# ── finding links ───────────────────────────────────────────────────────────

def test_email_is_ignored_not_checked():
    links, ignored = uh.find_mentions('mail = "info@uoli.edu.pk"', "a.py")
    assert links == []
    assert ignored[0][1] == "email address"
    assert (ignored[0][0].file, ignored[0][0].line) == ("a.py", 1)


def test_bare_domain_is_ignored():
    links, ignored = uh.find_mentions('FALLBACK = "uoli.edu.pk"', "a.py")
    assert links == [] and "domain name only" in ignored[0][1]


def test_placeholder_is_ignored():
    links, ignored = uh.find_mentions("# https://uoli.edu.pk/faculty/{name}/", "a.py")
    assert links == [] and "placeholder" in ignored[0][1]


def test_real_link_found_with_line_number():
    links, _ = uh.find_mentions('x = 1\nu = "https://uoli.edu.pk/about-us/"', "c.py")
    assert (links[0].text, links[0].line) == ("https://uoli.edu.pk/about-us/", 2)


def test_trailing_newline_escape_not_part_of_link():
    links, _ = uh.find_mentions(r'u = "https://uoli.edu.pk/a/\n"', "c.py")
    assert links[0].text == "https://uoli.edu.pk/a/"


# ── same-link rules and types ───────────────────────────────────────────────

def test_small_differences_are_same_link():
    keys = {uh.normalize(u) for u in (
        "https://uoli.edu.pk/faculty/", "http://uoli.edu.pk/faculty",
        "https://www.uoli.edu.pk/faculty", "uoli.edu.pk/faculty/")}
    assert len(keys) == 1


def test_types_are_separated():
    assert uh.classify_type("https://uoli.edu.pk/a/b.PDF") == "pdf"
    assert uh.classify_type("https://uoli.edu.pk/logo.png") == "image"
    assert uh.classify_type("https://uoli.edu.pk/about-us/") == "page"


def test_collect_links_merges_duplicates(tmp_path):
    (tmp_path / "a.py").write_text('u = "https://uoli.edu.pk/x/"\n')
    (tmp_path / "b.py").write_text('u = "http://www.uoli.edu.pk/x"\ne = "a@uoli.edu.pk"\n')
    (tmp_path / "test_c.py").write_text('u = "https://uoli.edu.pk/skipped"\n')
    entries, ignored = uh.collect_links(tmp_path)
    assert len(entries) == 1
    assert entries[0]["found"] == ["a.py:1", "b.py:1"]
    assert len(ignored) == 1


# ── judging results ─────────────────────────────────────────────────────────

def test_fake_page_is_dead():
    res = uh.judge_page("https://uoli.edu.pk/fake", 200, "https://uoli.edu.pk/fake",
                        "Menu\nOops this page does not exist", NOT_FOUND_SIG, HOME)
    assert res[0] == uh.DEAD


def test_http_404_with_no_content_is_dead():
    res = uh.judge_page("https://uoli.edu.pk/x", 404, "https://uoli.edu.pk/x", "", [], HOME)
    assert res[0] == uh.DEAD


def test_home_page_is_working():
    res = uh.judge_page("https://uoli.edu.pk/", 200, "https://uoli.edu.pk/", HOME, [], HOME)
    assert res[0] == uh.WORKING


def test_redirect_to_home_needs_review():
    res = uh.judge_page("https://uoli.edu.pk/old", 200, "https://uoli.edu.pk/", HOME, [], "other")
    assert res[0] == uh.REVIEW


def test_little_text_needs_review():
    res = uh.judge_page("https://uoli.edu.pk/a", 200, "https://uoli.edu.pk/a", "Hello there friend", [], HOME)
    assert res[0] == uh.REVIEW


def test_moved_page_is_redirected_with_new_address():
    text = "Real content " * 40
    res = uh.judge_page("https://uoli.edu.pk/old", 200, "https://uoli.edu.pk/new", text, [], HOME)
    assert res[0] == uh.REDIRECTED and res[1].endswith("/new")


def test_http_404_but_browser_moves_to_new_address_is_redirected():
    text = "Real content " * 40
    res = uh.judge_page("https://uoli.edu.pk/about-us/", 404, "https://uoli.edu.pk/about", text, [], HOME)
    assert res[0] == uh.REDIRECTED and res[1].endswith("/about")


def test_http_404_with_content_at_same_address_needs_review():
    text = "Real content " * 40
    res = uh.judge_page("https://uoli.edu.pk/a", 404, "https://uoli.edu.pk/a", text, [], HOME)
    assert res[0] == uh.REVIEW


def test_pdf_that_opens_web_page_is_dead():
    f = uh.Fetched(200, "https://uoli.edu.pk/a.pdf", "text/html", b"<html>")
    assert uh.judge_binary("https://uoli.edu.pk/a.pdf", "pdf", f)[0] == uh.DEAD


def test_real_pdf_is_working():
    f = uh.Fetched(200, "https://uoli.edu.pk/a.pdf", "application/pdf", b"%PDF-1.7")
    assert uh.judge_binary("https://uoli.edu.pk/a.pdf", "pdf", f)[0] == uh.WORKING


def test_learn_not_found_ignores_menu_lines():
    home = "Menu\nHome"
    fake1 = "Menu\nOops this page does not exist"
    fake2 = "Menu\nOops this page does not exist"
    assert uh.learn_not_found(fake1, fake2, home) == ["Oops this page does not exist"]


# ── whole-link behaviour with fake network ──────────────────────────────────

class FakeRenderer:
    def __init__(self, exc=None, file=None):
        self.exc, self.file = exc, file

    def fetch(self, url):
        return self.file

    def render(self, url):
        if self.exc:
            raise self.exc
        return url, HOME


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(uh.time, "sleep", lambda s: None)


def test_timeout_is_error_not_dead(monkeypatch):
    def boom(url):
        raise uh.FetchTimeout("slow")
    monkeypatch.setattr(uh, "fetch", boom)
    entry = {"url": "https://uoli.edu.pk/a", "type": "page", "found": ["x.py:1"]}
    row = uh.check_link(entry, FakeRenderer(), [], HOME)
    assert row.result == uh.ERROR


def test_timeout_is_retried_twice_more(monkeypatch):
    calls = []

    def boom(url):
        calls.append(url)
        raise uh.FetchTimeout("slow")
    monkeypatch.setattr(uh, "fetch", boom)
    uh.check_link({"url": "https://uoli.edu.pk/a", "type": "pdf", "found": []}, FakeRenderer(), [], HOME)
    assert len(calls) == 3


def test_blocked_page_is_judged_by_the_browser(monkeypatch):
    monkeypatch.setattr(uh, "fetch", lambda url: uh.Fetched(403, url, "text/html", b""))
    row = uh.check_link({"url": "https://uoli.edu.pk/a", "type": "page", "found": []}, FakeRenderer(), [], "other home")
    assert row.result == uh.WORKING


def test_blocked_pdf_is_downloaded_by_the_browser(monkeypatch):
    monkeypatch.setattr(uh, "fetch", lambda url: uh.Fetched(403, url, "text/html", b""))
    good = uh.Fetched(200, "https://uoli.edu.pk/a.pdf", "application/pdf", b"%PDF-1.7")
    row = uh.check_link({"url": "https://uoli.edu.pk/a.pdf", "type": "pdf", "found": []}, FakeRenderer(file=good), [], HOME)
    assert row.result == uh.WORKING


def test_pdf_blocked_in_browser_too_needs_review(monkeypatch):
    blocked = uh.Fetched(403, "https://uoli.edu.pk/a.pdf", "text/html", b"")
    monkeypatch.setattr(uh, "fetch", lambda url: blocked)
    row = uh.check_link({"url": "https://uoli.edu.pk/a.pdf", "type": "pdf", "found": []}, FakeRenderer(file=blocked), [], HOME)
    assert row.result == uh.REVIEW


def test_redirect_loop_is_dead(monkeypatch):
    def loop(url):
        raise uh.RedirectLoop("loop")
    monkeypatch.setattr(uh, "fetch", loop)
    row = uh.check_link({"url": "https://uoli.edu.pk/a", "type": "page", "found": []}, FakeRenderer(), [], HOME)
    assert row.result == uh.DEAD


# ── reports ─────────────────────────────────────────────────────────────────

def test_reports_are_split_by_type(tmp_path):
    rows = [
        uh.Row("https://uoli.edu.pk/a", "x.py:1", "page", "https://uoli.edu.pk/a", uh.WORKING, "ok"),
        uh.Row("https://uoli.edu.pk/b.pdf", "x.py:2", "pdf", "", uh.DEAD, "gone"),
    ]
    ignored = [(uh.Mention("a@uoli.edu.pk", "x.py", 3), "email address")]
    paths = {p.name.split("_", 3)[-1]: p for p in uh.write_reports(rows, ignored, [], tmp_path)}
    assert {"pages.csv", "pdfs.csv", "ignored.csv", "summary.md"} <= set(paths)
    pages = paths["pages.csv"].read_text(encoding="utf-8-sig")
    assert "b.pdf" not in pages and pages.splitlines()[0].startswith("link,where found,type")
    assert "a@uoli.edu.pk" in paths["ignored.csv"].read_text(encoding="utf-8-sig")
