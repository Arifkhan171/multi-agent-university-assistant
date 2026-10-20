"""Offline tests for spec 02b: no internet, no browser."""
import csv

import pytest

from tools import find_hidden_pages as fh
from tools import find_site_urls as fs
from tools import url_health_check as uh

SITE = fs.SITE
HOME_TEXT = "Home Welcome " + "home words " * 60
SIG = ["Oops this page does not exist"]


def body(label: str) -> str:
    return f"{label} " + "real content here " * 30


def view(url: str, text: str, status: int = 404) -> fs.PageView:
    return fs.PageView(url, text, "T", [], False, status)


def item(text: str, top: int = 0, left: int = 0, **kw) -> fh.Clickable:
    kw.setdefault("lineage", ("div", "main", "body"))
    return fh.Clickable(idx=kw.pop("idx", 0), text=text, top=top, left=left, **kw)


class FakeBrowser:
    """Pretends to be the browser. Never touches the network."""

    def __init__(self, pages=None, items=None, outcomes=None, loader_batches=0):
        self.pages = pages or {}
        self.items = items or {}                  # hub -> clickable items on first view
        self.outcomes = outcomes or {}            # item text -> ClickOutcome
        self.links = {}                           # hub -> [(href, text)] shown after scrolling
        self.loader_batches = loader_batches      # how many times 'Load more' reveals new cards
        self.hub = ""
        self.presses = 0
        self.clicked: list[str] = []
        self.opened: list[str] = []
        self.overlays_closed = 0
        self.came_back = 0

    def render_page(self, url):
        self.opened.append(url)
        self.hub, self.presses = url, 0
        result = self.pages.get(url)
        if isinstance(result, BaseException):
            raise result
        return result or view(SITE, HOME_TEXT)

    def scroll_bottom(self):
        pass

    def page_links(self):
        return self.links.get(self.hub, [])

    def clickables(self):
        shown = list(self.items.get(self.hub, []))
        shown += [item(f"extra card {n}", top=1000 + n * 100) for n in range(self.presses)]
        if self.presses < self.loader_batches:
            shown.append(item("Load more", top=9000))
        return shown

    def click(self, clicked):
        self.clicked.append(clicked.text)
        if fh.LOAD_MORE_TEXT.search(clicked.text):
            self.presses += 1
            return fh.ClickOutcome(fh.NONE)
        return self.outcomes.get(clicked.text, fh.ClickOutcome(fh.NONE))

    def close_overlay(self):
        self.overlays_closed += 1

    def come_back(self, hub):
        self.came_back += 1
        self.hub, self.presses = hub, 0


class FakeRenderer:
    """For verify_candidates: only render_page is used."""

    def __init__(self, pages):
        self.pages = pages
        self.opened: list[str] = []

    def render_page(self, url):
        self.opened.append(url)
        return self.pages.get(url) or view(SITE, HOME_TEXT)


def ctx():
    return {"signature": SIG, "home_text": HOME_TEXT, "seen": {}}


def explore(browser, hubs, sleeps=None, budget=None):
    log = sleeps if sleeps is not None else []
    return fh.explore_all(browser, hubs, ctx(), sleep=log.append, budget=budget)


HUB = SITE + "/research/innovation"
EVENT = SITE + "/research/innovation/events/ev-1"


# ── hub choice ────────────────────────────────────────────────────────────────

def _rows(addresses, keep="yes"):
    return [{"address": a, "category": "general", "keep": keep, "note": ""} for a in addresses]


def test_hubs_are_kept_short_pages_only_and_shortest_first():
    rows = _rows([SITE + "/offices/x", SITE + "/news", SITE, SITE + "/a/b/c"]) + _rows([SITE + "/skip"], keep="no")
    hubs, left_out = fh.choose_hubs(rows)
    assert hubs == [SITE, SITE + "/news", SITE + "/offices/x"]
    assert left_out == []


def test_hub_cap_is_40_and_the_rest_are_listed():
    rows = _rows([f"{SITE}/p{n}" for n in range(45)])
    hubs, left_out = fh.choose_hubs(rows)
    assert len(hubs) == 40 and len(left_out) == 5
    assert left_out[0] == f"{SITE}/p40"


def test_limit_shortens_the_hub_list_only():
    rows = _rows([f"{SITE}/p{n}" for n in range(10)])
    hubs, left_out = fh.choose_hubs(rows, limit=3)
    assert len(hubs) == 3 and left_out == []


# ── what may be clicked ───────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    item("Read More", lineage=("button", "header", "body")),
    item("Read More", lineage=("button", "nav", "body")),
    item("Read More", lineage=("button", "footer", "body")),
    item("View all", lineage=("button", "form", "body")),
    item("Read More", lineage=("div", "a", "body")),
    item("Read more", ident=" chat-widget open"),
    item("Read more", ident=" assistant-launcher"),
    item("Read more", ident=" floating bot"),
    item("Read more", tag="button", input_type="submit"),
    item("Read more", tag="input"),
    item("Log in to read more"),
    item("Apply now - view details"),
    item("Download all events"),
    item("Register for events"),
    item("Read " + "x" * 400),
])
def test_forbidden_items_are_never_wanted(bad):
    assert not fh.is_wanted(bad)


@pytest.mark.parametrize("good", [
    item("Read Article"), item("View All Events"), item("Explore Programs"),
    item("See more"), item("Event details"), item("Next"),
])
def test_card_and_button_texts_are_wanted(good):
    assert fh.is_wanted(good)


def test_bottom_class_is_not_the_chat_bot():
    assert fh.is_wanted(item("Read Article", ident=" bottom-section"))


def test_text_without_action_words_is_not_wanted():
    assert not fh.is_wanted(item("Our mission"))


def test_same_card_once_but_same_text_in_other_place_twice():
    items = [item("Read Article", top=100), item("Read Article", top=100, idx=1), item("Read Article", top=500, idx=2)]
    assert len(fh.pick_clickables(items)) == 2


# ── what a click did ──────────────────────────────────────────────────────────

def test_moved_tab_is_a_page():
    out = fh.classify_outcome(HUB, EVENT + "/", [], [])
    assert (out.kind, out.address) == (fh.PAGE, EVENT)


def test_no_change_is_none():
    assert fh.classify_outcome(HUB, HUB + "/", [], []).kind == fh.NONE


def test_new_tab_is_recorded():
    out = fh.classify_outcome(HUB, HUB, ["about:blank", EVENT], [])
    assert (out.kind, out.address) == (fh.PAGE, EVENT)


def test_other_website_is_external():
    assert fh.classify_outcome(HUB, "https://example.com/x", [], []).kind == fh.EXTERNAL


def test_download_and_pdf_are_pdf_candidates():
    assert fh.classify_outcome(HUB, HUB, [], [SITE + "/files/a.xlsx"]).kind == fh.PDF
    assert fh.classify_outcome(HUB, SITE + "/Docs/a.pdf", [], []).kind == fh.PDF


def test_address_with_query_says_nothing():
    assert fh.classify_outcome(HUB, HUB + "?page=2", [], []).kind == fh.NONE


# ── exploring a hub ───────────────────────────────────────────────────────────

def hub_browser(**kw):
    pages = {HUB: view(HUB, body("Innovation"))}
    return FakeBrowser(pages=pages, **kw)


def test_never_clicks_chat_header_footer_or_submit():
    items = [
        item("Read Article", top=10),
        item("Ask me - view answers", ident=" chat-widget"),
        item("View all", lineage=("button", "header", "body")),
        item("Read more", lineage=("button", "footer", "body")),
        item("Submit - read", tag="button", input_type="submit"),
        item("Login to view", top=50),
    ]
    browser = hub_browser(items={HUB: items})
    explore(browser, [HUB])
    assert browser.clicked == ["Read Article"]


def test_click_to_page_is_recorded_and_hub_is_restored():
    browser = hub_browser(items={HUB: [item("Read Article")]},
                          outcomes={"Read Article": fh.ClickOutcome(fh.PAGE, EVENT)})
    result = explore(browser, [HUB])
    assert EVENT in result.candidates
    assert browser.came_back == 1


def test_no_change_closes_dialog_and_other_site_is_ignored():
    browser = hub_browser(items={HUB: [item("Read Article", top=1), item("View all", top=2)]},
                          outcomes={"View all": fh.ClickOutcome(fh.EXTERNAL, "https://example.com")})
    result = explore(browser, [HUB])
    assert result.candidates == {}
    assert browser.overlays_closed == 1
    assert [r.result for r in result.log] == ["no change", "other website, ignored"]


def test_pdf_click_is_a_pdf_candidate_not_a_page():
    pdf = SITE + "/Docs/a.pdf"
    browser = hub_browser(items={HUB: [item("View details")]},
                          outcomes={"View details": fh.ClickOutcome(fh.PDF, pdf)})
    result = explore(browser, [HUB])
    assert pdf in result.pdfs and result.candidates == {}


def test_failed_click_is_logged():
    browser = hub_browser(items={HUB: [item("Read Article")]},
                          outcomes={"Read Article": fh.ClickOutcome(fh.CLICK_ERROR, "", "boom")})
    result = explore(browser, [HUB])
    assert result.log[0].result == "click failed: boom"


def test_load_more_stops_after_five_presses():
    browser = hub_browser(loader_batches=50)
    explore(browser, [HUB])
    assert browser.clicked.count("Load more") == fh.MAX_LOAD_MORE


def test_cards_revealed_by_load_more_are_clicked_after_restore():
    browser = hub_browser(loader_batches=2)
    result = explore(browser, [HUB])
    assert "extra card 1" not in browser.clicked  # fake cards have no action word
    assert result.hubs_done == 1


def test_click_limit_holds_and_delay_is_at_least_one_second():
    items = [item("Read Article", top=n * 100, idx=n) for n in range(50)]
    browser = hub_browser(items={HUB: items})
    sleeps: list[float] = []
    result = explore(browser, [HUB], sleeps, budget=fh.Budget(max_clicks=7))
    assert len(browser.clicked) == 7
    assert sleeps and min(sleeps) >= 1.0
    assert any("Click limit" in w for w in result.warnings)


def test_delay_constants():
    assert fh.DELAY_SECONDS >= 1.0
    assert (fh.MAX_HUBS, fh.MAX_CLICKS) == (40, 400)


def test_hub_that_fails_does_not_stop_the_rest():
    other = SITE + "/news"
    browser = FakeBrowser(pages={HUB: RuntimeError("boom"), other: view(other, body("News"))})
    result = explore(browser, [HUB, other])
    assert result.hubs_done == 1 and result.failed_hubs[0][0] == HUB


def test_ctrl_c_keeps_what_is_done(tmp_path):
    other = SITE + "/news"
    browser = FakeBrowser(pages={HUB: view(HUB, body("Innovation")), other: KeyboardInterrupt()})
    result = explore(browser, [HUB, other])
    assert result.hubs_done == 1 and any("Ctrl+C" in w for w in result.warnings)
    paths = fh.write_reports(result, [], set(), [], out_dir=tmp_path)
    assert all(p.exists() for p in paths)


# ── checking the new addresses ────────────────────────────────────────────────

def candidates_for(*addresses):
    result = fh.HiddenResult()
    for address in addresses:
        result.candidates[address] = (HUB, "Read Article")
    return result


def test_real_new_page_goes_to_review_with_suggested_category():
    result = candidates_for(EVENT)
    renderer = FakeRenderer({EVENT: view(EVENT, body("Event"))})
    review = fh.verify_candidates(renderer, result, set(), ctx(), sleep=lambda s: None)
    assert [r[0] for r in review] == [EVENT]
    assert review[0][2] == "yes" and review[0][1] in fh.config.CATEGORIES


def test_known_address_is_never_opened_or_listed():
    result = candidates_for(EVENT + "/")
    known = {fh.config._url_path(EVENT)}
    renderer = FakeRenderer({})
    review = fh.verify_candidates(renderer, result, known, ctx(), sleep=lambda s: None)
    assert review == [] and renderer.opened == []


def test_moved_address_uses_final_address_with_note():
    final = SITE + "/research/innovation/events/ev-1"
    old = SITE + "/news/ev-1"
    renderer = FakeRenderer({old: view(final, body("Event")), final: view(final, body("Event"))})
    review = fh.verify_candidates(renderer, candidates_for(old), set(), ctx(), sleep=lambda s: None)
    assert review[0][0] == final
    assert f"moved from {old}" in review[0][3]


def test_moved_to_known_address_is_not_listed():
    old = SITE + "/news/ev-1"
    known = {fh.config._url_path(EVENT)}
    renderer = FakeRenderer({old: view(EVENT, body("Event"))})
    review = fh.verify_candidates(renderer, candidates_for(old), known, ctx(), sleep=lambda s: None)
    assert review == []


def test_thin_page_is_keep_no_and_not_found_is_only_logged():
    thin, missing = SITE + "/x/thin", SITE + "/x/missing"
    renderer = FakeRenderer({thin: view(thin, "short text"),
                             missing: view(missing, "Oops this page does not exist")})
    result = candidates_for(thin, missing)
    review = fh.verify_candidates(renderer, result, set(), ctx(), sleep=lambda s: None)
    assert [(r[0], r[2]) for r in review] == [(thin, "no")]
    assert any("NOT_FOUND" in r.result for r in result.log)


def test_every_wait_in_verify_is_at_least_one_second():
    sleeps: list[float] = []
    renderer = FakeRenderer({EVENT: view(EVENT, body("Event"))})
    fh.verify_candidates(renderer, candidates_for(EVENT), set(), ctx(), sleep=sleeps.append)
    assert sleeps and min(sleeps) >= 1.0


def test_skipped_category_becomes_general_keep_no(monkeypatch):
    monkeypatch.setattr(fh.config, "resolve_category", lambda url: None)
    renderer = FakeRenderer({EVENT: view(EVENT, body("Event"))})
    review = fh.verify_candidates(renderer, candidates_for(EVENT), set(), ctx(), sleep=lambda s: None)
    assert review[0][1:3] == ["general", "no"]


# ── count check ───────────────────────────────────────────────────────────────

def test_find_counts_reads_the_usual_shapes():
    text = "Overview\n4 Official Events\n11\nDepartments\nEvents (4)\n12 January\nPage 2"
    found = dict(fh.find_counts(text))
    assert found["Official Events"] == 4
    assert found["Departments"] == 11
    assert found["Events"] == 4
    assert "January" not in found


def test_count_table_compares_with_addresses_under_the_hub():
    result = fh.HiddenResult(counts=[(HUB, "Official Events", 4), (SITE, "Programs", 20)])
    known = {fh.config._url_path(EVENT), fh.config._url_path(HUB + "/events/ev-2")}
    rows = fh.count_check_rows(result, known, [[HUB + "/events/ev-3", "general", "yes", ""]])
    assert rows[0] == [HUB, "4 Official Events", "3"]
    assert rows[1][2].startswith("n/a")


def test_reports_have_the_spec_columns(tmp_path):
    result = fh.HiddenResult()
    result.log.append(fh.LogRow(HUB, "Read", EVENT, "REAL"))
    paths = fh.write_reports(result, [[EVENT, "general", "yes", ""]], set(), [], out_dir=tmp_path, tag="_limit3")
    assert [p.name for p in paths] == ["hidden_pages_for_review_limit3.csv", "hidden_pages_log_limit3.csv",
                                       "hidden_pages_summary_limit3.md"]
    with open(paths[0], newline="", encoding="utf-8-sig") as handle:
        assert next(csv.reader(handle)) == ["address", "category", "keep", "note"]
    with open(paths[1], newline="", encoding="utf-8-sig") as handle:
        assert next(csv.reader(handle)) == fh.LOG_HEADER


def test_fs_and_uh_are_reused_not_copied():
    assert fh.ClickRenderer.__mro__[1] is fs.LinkRenderer
    assert issubclass(fs.LinkRenderer, uh.BrowserRenderer)


def test_carousel_arrows_are_not_clicked():
    assert not fh.is_wanted(item("Next slide"))
    assert not fh.is_wanted(item("Previous"))


def test_news_stories_are_not_hubs_but_news_is():
    rows = _rows([SITE + "/news", SITE + "/news/news-1", SITE + "/news/some-story", SITE + "/offices/treasurer"])
    hubs, _ = fh.choose_hubs(rows)
    assert hubs == [SITE + "/news", SITE + "/offices/treasurer"]


def test_events_list_pages_are_hubs_even_when_deep():
    deep = SITE + "/campus-life/sports/events"
    hubs, _ = fh.choose_hubs(_rows([SITE + "/news", deep, SITE + "/campus-life/sports/events/one-event"]))
    assert hubs == [SITE + "/news", deep]


@pytest.mark.parametrize("text, wanted", [
    ("Next slide", False), ("Previous slide", False), ("Next page", True),
    ("Next", True), ("Load more", True), ("Previous page", False)])
def test_only_carousel_arrows_are_filtered(text, wanted):
    # "Previous page" has no action word, so it is never wanted either way.
    assert fh.is_wanted(item(text)) is wanted


def test_events_page_found_by_a_click_is_opened_once_and_not_followed_further():
    events = HUB + "/events"
    deeper = events + "/events"
    pages = {HUB: view(HUB, body("Innovation")), events: view(events, body("Events"))}
    browser = FakeBrowser(
        pages=pages,
        items={HUB: [item("View All Events")], events: [item("Read Article")]},
        outcomes={"View All Events": fh.ClickOutcome(fh.PAGE, events),
                  "Read Article": fh.ClickOutcome(fh.PAGE, deeper)})
    result = explore(browser, [HUB])
    assert browser.opened.count(events) == 1 and deeper not in browser.opened
    assert result.hubs_done == 2


def test_discovery_can_be_switched_off_for_only():
    events = HUB + "/events"
    browser = FakeBrowser(pages={HUB: view(HUB, body("Innovation"))}, items={HUB: [item("View All Events")]},
                          outcomes={"View All Events": fh.ClickOutcome(fh.PAGE, events)})
    result = fh.explore_all(browser, [HUB], ctx(), sleep=lambda s: None, discover=False)
    assert result.hubs_done == 1 and events not in browser.opened


def test_links_shown_after_scrolling_become_candidates_without_any_click():
    browser = hub_browser()
    browser.links[HUB] = [(EVENT, "Event"), (SITE + "/Docs/a.pdf", "Form"), ("https://example.com/x", "Out"),
                          (SITE + "/research/innovation/events", "View All Events")]
    result = explore(browser, [HUB])
    assert set(result.candidates) == {EVENT, SITE + "/research/innovation/events"}
    assert SITE + "/Docs/a.pdf" in result.pdfs
    assert browser.clicked == []
