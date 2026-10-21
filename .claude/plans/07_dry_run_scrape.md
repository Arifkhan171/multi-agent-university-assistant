# Plan 07 — Dry Run: Small Live Scrape Without the Database

## 1. Goal

Run the real scraper and cleaning code on about 16 live pages and 3 PDFs, with no database and no API calls, and write a report that shows exactly what the chatbot would learn from each page. Nothing in `data/` changes.

## 2. Files to change

Only two new files. No existing file is changed.

| File | What |
|---|---|
| `tools/dry_run_scrape.py` (new) | The dry-run tool. |
| `tests/test_dry_run_scrape.py` (new) | Offline tests for the tool. |

Existing code the tool **uses but does not edit** (found by grep):

| File : line | Name | How the tool uses it |
|---|---|---|
| `web_scraper.py:1966` | `scrape_all_pages(kb, urls, include_guaranteed)` | Called once with `include_guaranteed=False`. |
| `web_scraper.py:1594` | `fetch_page(url, session)` | Wrapped (in memory only) to time each page, keep the raw text, and retry once. |
| `web_scraper.py:2055` | `time.sleep(1.0)` inside `_fetch` | Gives the 1-second gap. Worker count is fixed at 1 (`:2080`). |
| `web_scraper.py:1796-1797` | `FURNITURE_CACHE`, `CONTACT_LINES_CACHE` | Pointed to `reports/dry_run_07/cache/`. |
| `web_scraper.py:1388` | `SITE_NOTE_CACHE` | Pointed to the cache folder. |
| `web_scraper.py:290-295` | `_OCR_CACHE_PATH`, `_OCR_CACHE`, `_IMAGE_DIGEST_BY_URL` | Path pointed to the cache folder, `_OCR_CACHE` set to `None`, digest map cleared. Image owner info lives inside this OCR cache, so this also covers "image ownership state". |
| `web_scraper.py:79` | `OCR_AVAILABLE` | Read to write the "no OCR" warning. |
| `web_scraper.py:61` | `PLAYWRIGHT_AVAILABLE` | Read to decide exit code 1. |
| `web_scraper.py:1928` / `config.py:626` | `_store_notes`, `config.SITE_INFO_URLS` | The site note reaches the stand-in through `load_scraped_page` at these addresses; the tool saves it as `site_note.txt`. |
| `pdf_ingest.py:106-107` | `_CACHE_PATH`, `_CACHE` | Pointed to the cache folder, `_CACHE` set to `None`. |
| `pdf_ingest.py:711` | `_SKIP_LOG_PATH` (`pdf_skipped.md`) | Pointed to the cache folder, so no new file appears in the project root. |
| `pdf_ingest.py:797` | `read_pdf(url, session, use_cache)` | Wrapped so only the 3 chosen PDFs are read. Pass 3 of `scrape_all_pages` (`web_scraper.py:2299`) would otherwise read all 12 PDFs the home footer links. |
| `pdf_ingest.py:720` | `was_refused(url)` | Gives the PDF row result "refused". |
| `page_furniture.py:119` | `MIN_PAGES_FOR_FREQUENCY = 20` | Below 20 pages the cleaner removes nothing during the run. |
| `page_furniture.py:202` | `find_furniture(pages, threshold, min_pages)` | Called once more after the run, on the 16 raw page texts with `min_pages=1`, for the report only. |
| `page_furniture.py:165` | `normalise(line)` | Used to match the "would remove" keys back to real lines on each page. |
| `knowledge_base.py:47` | `IngestionResult` | **Not imported.** The stand-in returns a tiny local object with `.status = "added"`, which is all `scrape_all_pages` reads. This keeps `knowledge_base` (Chroma, OpenAI) out of the process. |

## 3. Steps (in order)

1. **Selection** (item 1). Write `select_pages(pages_csv, samples_csv)` and `select_pdfs(pdfs_csv)`.
   - Read `url_lists/pages.csv` rows with `keep = yes`; read `tests/fixtures/site/index.csv` for the 25 samples (compare without the end `/`).
   - One fixed group table in the tool: home `/` 1, `/academics/programs/` 3, `/academics/departments/` 2, `/about/` 2, `/offices/` 2, `/campus-life/` 2, `/research/` 1, `/news/` 2, `/services/` 1.
   - In each group: sort by address, take non-sample pages first; if short, fill with sample pages and add a note; if still short, take what exists and add a note.
   - Two groups have their own rule (from the updated spec):
     - `/about/`: the first page whose address ends in `-message`, plus `/about/vision-mission`.
     - `/campus-life/`: the first page under `/campus-life/sports/`, plus the first other campus-life page.
     - Both picks still prefer non-sample pages first. `/about/vision-mission` is found by matching the CSV row, not by typing the address.
   - PDFs: first 3 `keep = yes` rows of `url_lists/pdfs.csv`, in file order.
   - Addresses are copied from the CSV text, never built by hand.
   - Today's lists give: home (a sample, noted), `bba`, `bba-2-5`, `bed-honors`, `allied-health-sciences`, `commerce`, `dean-message`, `vision-mission`, `controller-examination`, `directorate-of-it`, `campus-life/sports/events/annual-sports-week-2018`, `campus-life/clinic`, `research/innovation`, two news pages, `career-counselling`. That is 15 non-sample pages.
2. **Cache redirect and data guard** (items 2, 3). Write `redirect_caches(cache_dir) -> dict` that sets every path in the table above and returns them. Write `check_no_data_paths(paths)` that stops with a clear message if any path resolves inside `data/`. Called before any fetch.
3. **data/ fingerprint** (item 2). Write `data_fingerprint()` that records name, size and modified time of every file in `data/` (metadata only, no file content read). Taken before and after the run; the report says "data/ unchanged: yes/no". Needed because most `data/` files are in `.gitignore`, so `git status` alone cannot see a change.
4. **Recording stand-in** (items 3, 4). Class `RecordingKB` with `load_scraped_page(text, url, category, page_hash=None, legacy_hash=None, source_type="web_scrape")`. It stores each call in a dict and returns an object with `status = "added"`.
5. **Fetch watcher** (items 4, 7, edge cases). Class `FetchWatch` that replaces `web_scraper.fetch_page` for the run and calls the original:
   - records start time, seconds taken ("slow" if over 30 s), reason, and the raw text;
   - on "unreachable": wait 10 s, try once more; if it fails again, set `stopped = True` and answer every later page with `(None, "unreachable")` without fetching;
   - records the gap between page starts, so the report can show the smallest gap.
   - The original is put back in a `finally` block.
6. **PDF filter** (items 1, 4). Replace `pdf_ingest.read_pdf` for the run with a wrapper that reads only the chosen PDFs (none with `--limit`) and returns `None` for the rest. The tool keeps the PDF rows from its own record, not from the `pdfs_failed` count. Put back in `finally`.
7. **Run** (items 2, 3, 7). `run(limit) -> dict`: select → redirect → guard → fingerprint → patch → `scrape_all_pages(kb, urls=pages, include_guaranteed=False)` → unpatch → fingerprint. Catch `KeyboardInterrupt` and still write the outputs for what is done (pages fetched but not cleaned are written from the raw text and marked "not cleaned").
8. **Page facts** (item 4). Small helpers, each one job:
   - `count_links(text)`: lines containing `http://`, `https://`, `mailto`-style emails kept by `_link_value`.
   - `count_people(text)`: lines with `Email:` or `is the Head of the Department` (department and faculty pages only).
   - `count_semesters(text)` and `count_course_rows(text)`: `Semester ...` heading lines and `| ... |` rows (program pages only).
   - `find_junk(text)`: lines matching the spec list ("UOLI AI Assistant", "Developed by", "Skip to main content", "Home ›", "Share", "Print", "Profile", "Back to", "Read Article", a bare `###`).
   - `removed_lines(raw, final)`: lines in the raw text that are not in the stored text (what the run itself removed).
   - `has_title(text)`: first line starts with `#`.
   - `would_remove(raw_texts) -> dict`: after the run, call `page_furniture.find_furniture(raw_texts, min_pages=1)` on the raw texts of the fetched pages (the same input pass 2 uses). For each page, list its lines whose `normalise(line)` is in that set. Report only: the stored texts and page files are not changed.
9. **Cross-page sections** (item 5). `repeated_lines(texts, min_pages=3)`, `problems(rows)`, a "Cleaner would remove (minimum lifted, nothing removed)" list from `would_remove` inside the repeated-lines section, `expectations(rows, site_note)` (semester table on every program page, head-of-department line on every department page, site note has the four sections "Official social media pages", "Portals and services", "Contact", "Rules, policies and forms").
10. **Outputs** (items 4, 5, 6). `address_to_filename(url)` using the fixture naming style (`/` → `__`, home → `home`). Write `pages/<name>.txt`, `pdfs/<name>.txt`, `site_note.txt`, `results.json`, `report.md` into `reports/dry_run_07/`. Report top: run time, OCR on/off, "stopped early" if so, "data/ unchanged", smallest gap between pages, selection list with notes. Then one table row per page and PDF, then the four sections.
11. **Command line** (API contract). `main()` with `argparse` and `--limit N`. Exit 1 if `web_scraper.PLAYWRIGHT_AVAILABLE` is false or the data guard fails; exit 0 otherwise, even with failed pages. Log with `logging`, no `print` except the final "report written to ..." line.
12. **Tests** (item 8). Write the tests below, run `python -m pytest tests/ -q`.

## 4. Tests (`tests/test_dry_run_scrape.py`, all offline)

Every test uses `monkeypatch` so `web_scraper._fetch_with_playwright` returns saved HTML from `tests/fixtures/site/`, `web_scraper.time.sleep` records the delay instead of sleeping, `OCR_AVAILABLE` is `False`, and output goes to `tmp_path`.

| Test | What it checks | Fixture |
|---|---|---|
| `test_selection_counts` | 16 pages, 3 PDFs, at least 12 not samples, group sizes as in the table | `url_lists/pages.csv`, `url_lists/pdfs.csv`, `tests/fixtures/site/index.csv` |
| `test_selection_about_and_campus_rules` | `/about/` gives one `-message` page and `/about/vision-mission`; `/campus-life/` gives one `/campus-life/sports/` page and one other campus-life page | same CSV files |
| `test_selection_copies_csv_text` | every chosen address is exactly a string from the CSV | same |
| `test_selection_fills_from_samples` | a group with only sample pages takes samples and adds a note | small CSV written in `tmp_path` |
| `test_selection_short_group` | a group with too few pages takes what exists and adds a note | small CSV in `tmp_path` |
| `test_redirect_moves_every_cache` | all 8 paths point inside the cache folder; `_OCR_CACHE` and `pdf_ingest._CACHE` are `None` | none |
| `test_guard_stops_on_data_path` | a path inside `data/` stops the tool before any fetch (fetch spy not called) | none |
| `test_recording_kb` | stores text, address, category; returns status "added" | none |
| `test_run_on_saved_pages` | full `run()` on saved pages: one text file and one report row per page, `results.json` written, `data/` fingerprint unchanged | `home.html`, `academics__programs__bscs.html`, `academics__departments__computer-science.html`, `offices__registrar.html`, `news__news-0.html`, `services__library.html` |
| `test_site_note_written` | `site_note.txt` has the four section titles | `home.html` |
| `test_report_has_four_sections` | `report.md` has Problems, New links, Lines that repeat, Comparison | same as run test |
| `test_program_and_department_counts` | BS CS shows semesters and course rows; CS department has 9 people and a head-of-department line | `academics__programs__bscs.html`, `academics__departments__computer-science.html` |
| `test_would_remove_lists_lines_only` | on the saved pages, a line on every page is listed under "Cleaner would remove", and the stored page texts still contain it (nothing removed) | same as run test |
| `test_junk_found` | a page text with "Share" and a bare `###` is listed under Problems | text written in the test |
| `test_empty_page_row` | BS Economics gets result "empty" and is listed under Problems | `academics__programs__bs-economics.html` |
| `test_retry_then_stop` | "unreachable" twice → one 10 s wait, run marked stopped, later pages not fetched | fake fetch |
| `test_delay_never_below_one_second` | every recorded sleep is ≥ 1.0 | recorded `time.sleep` |
| `test_limit_reads_no_pdfs` | `--limit 3` → 3 pages, PDF reader never called | fake `read_pdf` |
| `test_pdf_filter_only_chosen` | only the 3 chosen PDFs reach the real reader | fake `read_pdf` |
| `test_ctrl_c_still_writes_report` | `KeyboardInterrupt` during the run still writes `report.md` | fake fetch that raises |
| `test_no_openai_import` | in a subprocess, after an offline run, `knowledge_base` and `openai` are not in `sys.modules` | `home.html` |
| `test_no_playwright_exit_1` | `PLAYWRIGHT_AVAILABLE = False` → exit code 1 | none |

## 5. How to check each acceptance criterion

1. item 1: `test_selection_counts`, `test_selection_about_and_campus_rules`, `test_selection_copies_csv_text`; the live report lists them.
2. item 2: `test_run_on_saved_pages` (fingerprint) + owner runs `git status` and reads "data/ unchanged: yes" in the report after the live run.
3. item 3: `test_no_openai_import`, `test_recording_kb`.
4. item 4: `test_run_on_saved_pages`; live check, the owner counts rows and files.
5. item 5: `test_report_has_four_sections`.
6. item 6: `test_site_note_written`; live check, the owner reads `site_note.txt`.
7. item 7: `test_delay_never_below_one_second`; live report shows the smallest gap; worker count is 1 in `web_scraper.py:2080`.
8. item 8: owner or coder runs `python -m pytest tests/ -q`.
9. item 9: live check, the owner runs `--limit 3` first and reviews it.
10. item 10: human review by the owner (5 page texts + site note + report), with a fix/accept decision per problem.

## 6. Commands the owner runs by hand

```
python -m pytest tests/ -q
python -X utf8 tools/dry_run_scrape.py --limit 3
git status
```
Review `reports/dry_run_07/report.md`, then:
```
python -X utf8 tools/dry_run_scrape.py
git status
```
Old test scripts (baseline, known fails: 2 and 3):
```
python test_page_furniture.py
python test_ingest_safety.py
```

## 7. Risks (known limits, no open questions)

1. **403/429/503 cannot be seen directly** (accepted). `fetch_page` does not give the HTTP status, so only "unreachable" triggers the wait and retry. A 503 page that renders as short text shows as "too_short" with no retry.
2. **`scratch/_dryrun_web_cache.json` is still written** by `fetch_page` (`web_scraper.py:1716-1733`) (accepted). It is outside `data/`, and `scratch/` is untracked.
3. **"Would remove" may over-flag.** With `min_pages=1` and only 16 pages, the 16-page share can mark lines that a 115-page run would keep. The list is for a human to judge; nothing is removed.
4. **Ctrl+C during fetching** leaves raw page files marked "not cleaned" (accepted).
