"""Offline tests for spec 03: URL lists copied from CSV into config.py."""
import csv
import re
from pathlib import Path

import pytest

import config
from tools import build_url_lists as bl

ROOT = Path(__file__).resolve().parent.parent
HOME = "https://uoli.edu.pk"


def _write_csv(path: Path, header, rows, encoding="utf-8"):
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _page(address, category="general", keep="yes"):
    return {"address": address, "category": category, "keep": keep, "note": ""}


def _pdf(address, label="A label", category="policy", keep="yes"):
    return {"address": address, "category": category, "label": label, "keep": keep, "note": ""}


def _errors(rows, is_pdf=False):
    return bl.check_rows(rows, "x.csv", is_pdf)


# ── CSV checks ────────────────────────────────────────────────────────────────

def test_good_rows_have_no_errors():
    assert _errors([_page(HOME), _page(HOME + "/about", "overview")]) == []


@pytest.mark.parametrize("address", [
    "http://uoli.edu.pk/about",
    "https://other.example/about",
    HOME + "/about?x=1",
    HOME + "/about#top",
    HOME + "/about/",
])
def test_bad_address_is_reported(address):
    assert len(_errors([_page(address)])) == 1


def test_duplicate_with_and_without_slash_is_reported():
    rows = [_page(HOME + "/about"), _page(HOME + "/about/")]
    errors = _errors(rows)
    assert any("same address as row 2" in e for e in errors)


def test_bad_category_and_keep_are_reported():
    assert len(_errors([_page(HOME + "/a", category="students")])) == 1
    assert len(_errors([_page(HOME + "/a", keep="maybe")])) == 1


def test_kept_pdf_needs_label_but_skipped_pdf_does_not():
    assert len(_errors([_pdf(HOME + "/Docs/a.pdf", label="")], True)) == 1
    assert _errors([_pdf(HOME + "/Docs/a.pdf", label="", keep="no")], True) == []


def test_missing_column_is_named(tmp_path):
    _write_csv(tmp_path / "pages.csv", ["address", "category", "note"], [])
    with pytest.raises(bl.UrlListError, match="keep"):
        bl.read_rows(tmp_path / "pages.csv", bl.PAGE_COLUMNS)


def test_bom_and_spaces_are_handled(tmp_path):
    path = tmp_path / "pages.csv"
    _write_csv(path, bl.PAGE_COLUMNS, [[f"  {HOME}/about ", " overview ", " yes ", ""]],
               encoding="utf-8-sig")
    rows = bl.read_rows(path, bl.PAGE_COLUMNS)
    assert rows[0]["address"] == HOME + "/about"
    assert rows[0]["category"] == "overview"
    assert rows[0]["keep"] == "yes"


# ── --write ───────────────────────────────────────────────────────────────────

CONFIG_TEXT = f"before = 1\n{bl.BEGIN_MARKER}\nOLD = []\n{bl.END_MARKER}\nafter = 2\n"


def test_write_changes_only_the_block_and_is_repeatable(tmp_path):
    path = tmp_path / "config.py"
    path.write_text(CONFIG_TEXT, encoding="utf-8", newline="")
    pages = [_page(HOME, "general"), _page(HOME + "/skip", keep="no")]
    pdfs = [_pdf(HOME + "/Docs/a.pdf")]

    assert bl.write_config(pages, pdfs, path) is True
    first = path.read_text(encoding="utf-8")
    assert first.startswith("before = 1\n") and first.endswith("after = 2\n")
    assert "OLD" not in first and "/skip" not in first
    assert "a.pdf" in first

    assert bl.write_config(pages, pdfs, path) is False
    assert path.read_text(encoding="utf-8") == first


@pytest.mark.parametrize("text", [
    "no markers here\n",
    f"{bl.BEGIN_MARKER}\n{bl.END_MARKER}\n{bl.BEGIN_MARKER}\n{bl.END_MARKER}\n",
    f"{bl.END_MARKER}\n{bl.BEGIN_MARKER}\n",
])
def test_write_stops_when_markers_are_wrong(tmp_path, text):
    path = tmp_path / "config.py"
    path.write_text(text, encoding="utf-8", newline="")
    with pytest.raises(bl.UrlListError):
        bl.write_config([], [], path)
    assert path.read_text(encoding="utf-8") == text


# ── Project files agree with the CSVs ─────────────────────────────────────────

def _kept(name, columns):
    rows = bl.read_rows(ROOT / "url_lists" / name, columns)
    return [r for r in rows if r["keep"] == "yes"]


def test_csv_files_pass_the_check():
    bl.load_lists()  # raises with every problem if there is one


def test_config_pages_are_the_kept_csv_rows():
    expected = [(r["address"], r["category"]) for r in _kept("pages.csv", bl.PAGE_COLUMNS)]
    actual = [(p["url"], p["category"]) for p in config.UNIVERSITY_PAGES]
    assert actual == expected


def test_config_pdfs_are_the_kept_csv_rows():
    expected = [(r["address"], r["category"], r["label"]) for r in _kept("pdfs.csv", bl.PDF_COLUMNS)]
    actual = [(p["url"], p["category"], p["label"]) for p in config.PDF_SOURCES]
    assert actual == expected


def test_proforma_pdfs_and_spreadsheet_are_not_in_config():
    urls = [p["url"] for p in config.UNIVERSITY_PAGES + config.PDF_SOURCES]
    assert not any("Proforma" in u or u.endswith(".xlsx") for u in urls)


def test_guaranteed_pages_match_config():
    import web_scraper
    assert web_scraper.GUARANTEED_PAGES == [p["url"] for p in config.UNIVERSITY_PAGES]


def _agents_tables():
    from graph import Agents
    tables = [config.OFFICE_URLS.values(), config.DEPT_URLS.values(),
              config.LEADERSHIP_PAGES, config.CONTACT_PAGES, config.FEE_FALLBACK_PAGES,
              [config.FACULTY_LIST_PAGE]]
    # Agents must use the config tables, not private copies.
    assert Agents._OFFICE_URL is config.OFFICE_URLS
    return [url for table in tables for url in table]


def test_every_agents_link_is_in_the_page_list():
    known = {config._url_path(p["url"]) for p in config.UNIVERSITY_PAGES}
    missing = [u for u in _agents_tables() if config._url_path(u) not in known]
    assert missing == []


def test_no_old_address_shape_in_code_lines():
    old = re.compile(r"/wp-content/|/faculty/[a-z]|(?<!/about)/(vc|pro-vc|dean|registrar)-message")
    for name in ("config.py", "web_scraper.py", "graph/Agents.py"):
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            # Image handling and the old ad image are spec 05, not the page lists.
            if name == "web_scraper.py" and "/wp-content/" in code:
                continue
            # Only lines that hold an address; prose in docstrings may name old paths.
            if "http" not in code and "_site(" not in code:
                continue
            assert not old.search(code), f"{name}:{number}: {line.strip()}"


# ── New address rules ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("/academics/departments/physics", "faculty"),
    ("/academics/faculty", "faculty"),
    ("/academics/programs/bs-physics", "admissions"),
    ("/academics/undergraduate", "admissions"),
    ("/research/oric", "offices"),
    ("/about/new-page", "overview"),
    ("/offices/new-office", "offices"),
])
def test_rules_for_pages_not_in_the_list(path, expected):
    assert config.resolve_category(HOME + path) == expected


def test_csv_category_beats_a_rule():
    # /academics/programs/* is "admissions" by rule, /campus-life/... is "general"
    # by fallback; a declared page must keep exactly what the CSV says.
    for page in config.UNIVERSITY_PAGES:
        assert config.resolve_category(page["url"]) == page["category"]


# ── Faculty source filter ─────────────────────────────────────────────────────

def test_faculty_source_accepts_department_and_list_pages():
    from graph.Agents import _is_faculty_source
    for url in list(config.DEPT_URLS.values()) + [config.FACULTY_LIST_PAGE]:
        assert _is_faculty_source(url)
        assert _is_faculty_source(url + "/")


def test_faculty_source_rejects_other_pages():
    from graph.Agents import _is_faculty_source
    for url in (HOME + "/news/news-1", HOME + "/about/vc-message",
                HOME + "/faculty/commerce", HOME + "/academics/departments", ""):
        assert not _is_faculty_source(url)


# ── --append-from (spec 02b) ──────────────────────────────────────────────────

def _setup_lists(tmp_path, newline="\n"):
    header = ["address", "category", "keep", "note"]
    path = tmp_path / "pages.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator=newline).writerows(
            [header, [HOME + "/about", "overview", "yes", ""]])
    return path


def _review(tmp_path, rows):
    path = tmp_path / "review.csv"
    _write_csv(path, ["address", "category", "keep", "note"], rows)
    return path


def test_append_adds_new_rows_and_second_run_changes_nothing(tmp_path):
    pages = _setup_lists(tmp_path)
    review = _review(tmp_path, [[HOME + "/a", "general", "yes", "n"], [HOME + "/ABOUT", "overview", "yes", ""]])
    added, skipped = bl.append_pages(review, tmp_path)
    assert [r["address"] for r in added] == [HOME + "/a"] and len(skipped) == 1
    first = pages.read_bytes()
    added, _ = bl.append_pages(review, tmp_path)
    assert added == [] and pages.read_bytes() == first


def test_append_keeps_crlf_and_adds_missing_final_newline(tmp_path):
    pages = _setup_lists(tmp_path, newline="\r\n")
    pages.write_bytes(pages.read_bytes().rstrip(b"\r\n"))
    bl.append_pages(_review(tmp_path, [[HOME + "/a", "general", "yes", ""]]), tmp_path)
    raw = pages.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") == 3  # header + 2 rows, no bare LF


@pytest.mark.parametrize("row", [
    [HOME + "/a", "students", "yes", ""],
    [HOME + "/a/", "general", "yes", ""],
    ["https://other.example/a", "general", "yes", ""],
    [HOME + "/a", "general", "maybe", ""],
])
def test_append_stops_on_a_bad_row_and_changes_nothing(tmp_path, row):
    pages = _setup_lists(tmp_path)
    before = pages.read_bytes()
    review = _review(tmp_path, [[HOME + "/ok", "general", "yes", ""], row])
    with pytest.raises(bl.UrlListError, match="review.csv row 3"):
        bl.append_pages(review, tmp_path)
    assert pages.read_bytes() == before


def test_append_stops_on_duplicate_inside_the_review_file(tmp_path):
    pages = _setup_lists(tmp_path)
    before = pages.read_bytes()
    review = _review(tmp_path, [[HOME + "/a", "general", "yes", ""], [HOME + "/a", "general", "no", ""]])
    with pytest.raises(bl.UrlListError, match="same address as row 2"):
        bl.append_pages(review, tmp_path)
    assert pages.read_bytes() == before
