"""Offline tests for spec 07: the dry-run tool.

The live site is never touched: the browser fetch is replaced by the saved pages in
tests/fixtures/site/, the network is blocked, and sleeping only records the delay.
"""
import csv
import subprocess
import sys
from pathlib import Path

import pytest
import requests

import pdf_ingest
import web_scraper
from tools import dry_run_scrape as dr

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "site"
BASE = "https://uoli.edu.pk"

SAVED = {  # address -> saved page
    BASE: "home.html",
    BASE + "/academics/programs/bscs": "academics__programs__bscs.html",
    BASE + "/academics/departments/computer-science": "academics__departments__computer-science.html",
    BASE + "/offices/registrar": "offices__registrar.html",
    BASE + "/news/news-0": "news__news-0.html",
    BASE + "/services/library": "services__library.html",
}
ECONOMICS = BASE + "/academics/programs/bs-economics"

PATH_ATTRS = [(web_scraper, "FURNITURE_CACHE"), (web_scraper, "CONTACT_LINES_CACHE"),
              (web_scraper, "SITE_NOTE_CACHE"), (web_scraper, "_OCR_CACHE_PATH"),
              (web_scraper, "_OCR_CACHE"), (pdf_ingest, "_CACHE_PATH"),
              (pdf_ingest, "_CACHE"), (pdf_ingest, "_SKIP_LOG_PATH")]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No network, no OCR, no real sleeping, and every cache setting put back."""
    monkeypatch.chdir(tmp_path)   # relative files (scratch/...) land in tmp, not the project
    monkeypatch.setattr(web_scraper, "OCR_AVAILABLE", False)
    monkeypatch.setattr(web_scraper, "PLAYWRIGHT_AVAILABLE", True)
    # Safety net: a test that forgets to set up its pages must fail, never open the live site.
    monkeypatch.setattr(web_scraper, "_fetch_with_playwright", lambda url: None)
    for module, name in PATH_ATTRS:   # lets pytest restore what redirect_caches changes
        monkeypatch.setattr(module, name, getattr(module, name))
    sleeps = []
    monkeypatch.setattr(web_scraper.time, "sleep", sleeps.append)

    def no_network(*args, **kwargs):
        raise requests.ConnectionError("network blocked in tests")
    monkeypatch.setattr(requests.Session, "get", no_network)
    return sleeps


@pytest.fixture
def sleeps(offline):
    return offline


def saved_site(monkeypatch, pages=None):
    """The browser fetch answers from the saved pages."""
    files = {BASE + "/academics/programs/bs-economics": "academics__programs__bs-economics.html",
             **(pages or SAVED)}
    monkeypatch.setattr(
        web_scraper, "_fetch_with_playwright",
        lambda url: (FIX / files[url]).read_text(encoding="utf-8") if url in files else None)


def stub_texts(monkeypatch, texts):
    """web_scraper.fetch_page answers with the given page texts."""
    monkeypatch.setattr(web_scraper, "fetch_page",
                        lambda url, session=None: (texts[url], "ok") if url in texts else (None, "unreachable"))


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def small_lists(tmp_path, pages):
    """A tiny pages.csv and an empty samples file: pages is [(address, keep)]."""
    pages_csv = write_csv(tmp_path / "p.csv", ["address", "category", "keep", "note"],
                          [(a, "general", k, "") for a, k in pages])
    samples_csv = write_csv(tmp_path / "s.csv", ["address", "file", "page type"], [])
    return pages_csv, samples_csv


def saved_selection(pdfs=()):
    return list(SAVED), [], list(pdfs)


# ── selection ───────────────────────────────────────────────────────────────

def test_selection_counts():
    pages, _notes = dr.select_pages()
    pdfs = dr.select_pdfs()
    assert len(pages) == 16 and len(pdfs) == 3
    samples = {dr._path_of(r["address"]) for r in dr._read_csv(dr.SAMPLES_CSV)}
    assert sum(dr._path_of(p) not in samples for p in pages) >= 12
    for pick in dr.PICKS:
        assert sum(pick.match(dr._path_of(p)) for p in pages) >= pick.count


def test_selection_about_and_campus_rules():
    pages, _ = dr.select_pages()
    about = [dr._path_of(p) for p in pages if dr._path_of(p).startswith("/about/")]
    assert len(about) == 2 and "/about/vision-mission" in about
    assert any(a.endswith("-message") for a in about)
    campus = [dr._path_of(p) for p in pages if dr._path_of(p).startswith("/campus-life/")]
    assert len(campus) == 2
    assert sum(c.startswith("/campus-life/sports/") for c in campus) == 1


def test_selection_copies_csv_text():
    from_csv = {r["address"] for r in dr._read_csv(dr.PAGES_CSV)}
    pdfs_csv = {r["address"] for r in dr._read_csv(dr.PDFS_CSV)}
    pages, _ = dr.select_pages()
    assert set(pages) <= from_csv and set(dr.select_pdfs()) <= pdfs_csv


def test_selection_only_kept_rows(tmp_path):
    pages_csv, samples_csv = small_lists(
        tmp_path, [(BASE + "/news/a", "no"), (BASE + "/news/b", "yes")])
    pages, _ = dr.select_pages(pages_csv, samples_csv)
    assert pages == [BASE + "/news/b"]


def test_selection_fills_from_samples(tmp_path):
    pages_csv = write_csv(tmp_path / "p.csv", ["address", "category", "keep", "note"],
                          [(BASE + "/news/a", "general", "yes", ""),
                           (BASE + "/news/b", "general", "yes", "")])
    samples_csv = write_csv(tmp_path / "s.csv", ["address", "file", "page type"],
                            [(BASE + "/news/a", "a.html", "news"),
                             (BASE + "/news/b", "b.html", "news")])
    pages, notes = dr.select_pages(pages_csv, samples_csv)
    assert pages == [BASE + "/news/a", BASE + "/news/b"]
    assert any("news" in n and "sample" in n for n in notes)


def test_selection_short_group(tmp_path):
    pages_csv, samples_csv = small_lists(tmp_path, [(BASE + "/news/only", "yes")])
    pages, notes = dr.select_pages(pages_csv, samples_csv)
    assert pages == [BASE + "/news/only"]
    assert any("news: asked for 2, only 1 exist" in n for n in notes)


# ── cache redirect, guard, stand-in ─────────────────────────────────────────

def test_redirect_moves_every_cache(tmp_path):
    paths = dr.redirect_caches(tmp_path / "cache")
    assert len(paths) == 6
    for path in dr.current_cache_paths().values():
        assert Path(path).resolve().parent == (tmp_path / "cache").resolve()
    assert web_scraper._OCR_CACHE is None and pdf_ingest._CACHE is None
    dr.check_no_data_paths(dr.current_cache_paths())   # does not raise


def test_guard_stops_on_data_path(monkeypatch):
    calls = []
    monkeypatch.setattr(web_scraper, "fetch_page", lambda *a, **k: calls.append(a) or (None, "dead"))
    monkeypatch.setattr(dr, "redirect_caches", lambda cache_dir: None)   # leaves the real data/ paths
    monkeypatch.setattr(web_scraper, "FURNITURE_CACHE", str(dr.DATA_DIR / "page_furniture.json"))
    with pytest.raises(dr.DataPathError):
        dr.run(selection=saved_selection())
    assert calls == []


def test_main_exit_1_on_data_path(monkeypatch):
    monkeypatch.setattr(dr, "redirect_caches", lambda cache_dir: None)
    monkeypatch.setattr(web_scraper, "FURNITURE_CACHE", str(dr.DATA_DIR / "page_furniture.json"))
    assert dr.main([]) == 1


def test_recording_kb():
    kb = dr.RecordingKB()
    result = kb.load_scraped_page("text", BASE + "/x", "general", page_hash="h")
    assert result.status == "added"
    assert kb.stored[BASE + "/x"] == {"text": "text", "category": "general",
                                      "source_type": "web_scrape"}


# ── runs on the saved pages ─────────────────────────────────────────────────

@pytest.fixture
def saved_run(monkeypatch, tmp_path):
    saved_site(monkeypatch)
    out = tmp_path / "out"
    res = dr.run(selection=saved_selection(), out_dir=out)
    dr.write_outputs(res, out)
    return res, out


def test_run_on_saved_pages(saved_run):
    res, out = saved_run
    assert res.data_unchanged and not res.interrupted
    assert set(res.fetch.records) == set(SAVED)
    for url in SAVED:
        assert (out / "pages" / f"{dr.address_to_filename(url)}.txt").exists()
        assert url in (out / "report.md").read_text(encoding="utf-8")
    assert (out / "results.json").exists()


def test_site_note_written(saved_run):
    _res, out = saved_run
    note = (out / "site_note.txt").read_text(encoding="utf-8")
    for title in dr.SITE_NOTE_TITLES:
        assert title in note


def test_report_has_four_sections(saved_run):
    _res, out = saved_run
    report = (out / "report.md").read_text(encoding="utf-8")
    for heading in ("## 1. Problems", "## 2. New links",
                    "## 3. Lines that repeat on 3 or more pages",
                    "## 4. Comparison with spec 05/06 expectations"):
        assert heading in report


def test_program_and_department_counts(saved_run):
    res, _ = saved_run
    rows = {r["url"]: r for r in dr.build_rows(res)}
    bscs = rows[BASE + "/academics/programs/bscs"]
    assert bscs["semesters"] >= 1 and bscs["course_rows"] >= 1
    cs = rows[BASE + "/academics/departments/computer-science"]
    assert cs["people"] == 9 and cs["head_line"] is True


def test_empty_page_row(monkeypatch, tmp_path):
    saved_site(monkeypatch)
    res = dr.run(selection=([ECONOMICS], [], []), out_dir=tmp_path / "out")
    rows = dr.build_rows(res)
    assert rows[0]["result"] == "empty"
    assert any("result is empty" in p for p in dr.problems(rows, []))


# ── cleaner report and junk (synthetic page texts) ──────────────────────────

SYN = [BASE + "/offices/registrar", BASE + "/offices/directorate-of-it",
       BASE + "/services/library", BASE + "/news/news-0"]


def synthetic(extra=""):
    return {url: "\n".join([f"Title of page {n}", f"This is the own content of page {n}, "
                            f"long enough to count as real text for this page alone.",
                            "Scholarships", extra, f"More own facts number {n} for the students."])
            for n, url in enumerate(SYN)}


def test_would_remove_lists_lines_only(monkeypatch, tmp_path):
    texts = synthetic()
    stub_texts(monkeypatch, texts)
    res = dr.run(selection=(SYN, [], []), out_dir=tmp_path / "out")
    rows = dr.build_rows(res)
    assert all("Scholarships" in r["would_remove"] for r in rows)
    assert all("Scholarships" in res.kb.stored[u]["text"] for u in SYN)   # nothing removed
    assert "Scholarships" in dr.build_report(res)


def test_junk_found(monkeypatch, tmp_path):
    # The real cleaner already removes a bare "###", so only "Share" can reach the report here.
    stub_texts(monkeypatch, synthetic(extra="Share\nBack to all programs"))
    res = dr.run(selection=(SYN, [], []), out_dir=tmp_path / "out")
    found = dr.problems(dr.build_rows(res), [])
    assert any("junk line 'Share'" in p for p in found)
    assert any("junk line 'Back to all programs'" in p for p in found)


def test_junk_rules():
    assert dr.find_junk("Share\nShare of seats\nBack to all\n####\nHome › About") == [
        "Share", "Back to all", "####", "Home › About"]


# ── safety: retry, delay, PDFs, Ctrl+C, imports ─────────────────────────────

def test_retry_then_stop(monkeypatch, tmp_path, sleeps):
    calls = []

    def fake(url, session=None):
        calls.append(url)
        return None, "unreachable"
    monkeypatch.setattr(web_scraper, "fetch_page", fake)
    res = dr.run(selection=(SYN, [], []), out_dir=tmp_path / "out")
    assert res.fetch.stopped and len(calls) == 2            # first try + one retry
    assert sleeps.count(dr.FETCH_RETRY_WAIT) == 1
    assert res.fetch.records[SYN[1]]["reason"] == "not_fetched"


def test_delay_never_below_one_second(monkeypatch, tmp_path, sleeps):
    saved_site(monkeypatch)
    dr.run(selection=saved_selection(), out_dir=tmp_path / "out")
    assert sleeps and min(sleeps) >= 1.0


def test_limit_reads_no_pdfs(monkeypatch, tmp_path):
    saved_site(monkeypatch)
    called = []
    monkeypatch.setattr(pdf_ingest, "read_pdf", lambda *a, **k: called.append(a) or None)
    res = dr.run(limit=3, selection=saved_selection(dr.select_pdfs()), out_dir=tmp_path / "out")
    assert len(res.pages) == 3 and res.pdfs == [] and called == []


def test_pdf_filter_only_chosen(monkeypatch, tmp_path):
    saved_site(monkeypatch)
    chosen = dr.select_pdfs()
    called = []

    def fake_read(url, session=None, use_cache=True):
        called.append(url)
        return {"text": "Policy text.", "doc_hash": "h", "kind": "text", "pages": 1,
                "ocr_pages": 0, "tables": 0}
    monkeypatch.setattr(pdf_ingest, "read_pdf", fake_read)
    res = dr.run(selection=saved_selection(chosen), out_dir=tmp_path / "out")
    assert set(called) == set(chosen)
    assert all(res.pdf.records[u]["result"] == "ok" for u in chosen)


def test_ctrl_c_still_writes_report(monkeypatch, tmp_path):
    def fake(url, session=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(web_scraper, "fetch_page", fake)
    out = tmp_path / "out"
    res = dr.run(selection=(SYN, [], []), out_dir=out)
    report = dr.write_outputs(res, out)
    assert res.interrupted and "STOPPED EARLY: Ctrl+C" in report.read_text(encoding="utf-8")
    assert web_scraper.fetch_page is fake   # the real function was put back, not the watcher


def test_no_openai_import(tmp_path):
    script = (
        "import sys, requests\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "import web_scraper\n"
        "from tools import dry_run_scrape as dr\n"
        "web_scraper.OCR_AVAILABLE = False\n"
        "web_scraper.PLAYWRIGHT_AVAILABLE = True\n"
        "web_scraper.time.sleep = lambda s: None\n"
        f"html = Path({str(FIX / 'home.html')!r}).read_text(encoding='utf-8')\n"
        "web_scraper._fetch_with_playwright = lambda url: html\n"
        f"dr.run(selection=(['{BASE}'], [], []), out_dir=Path({str(tmp_path / 'sub')!r}))\n"
        "print('knowledge_base' in sys.modules, 'openai' in sys.modules)\n")
    done = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.stdout.strip().endswith("False False"), done.stderr[-800:]


def test_no_playwright_exit_1(monkeypatch):
    monkeypatch.setattr(web_scraper, "PLAYWRIGHT_AVAILABLE", False)
    assert dr.main([]) == 1
