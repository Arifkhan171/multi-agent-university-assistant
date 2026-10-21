# 07 — Dry Run: Small Live Scrape Without the Database

## 1. Problem Statement

The new scraper (spec 05) and cleaning rules (spec 06) were built and tested on saved copies of 25 sample pages, one of each page type, plus one live page (BS Computer Science). The website has 115 pages and 12 PDFs, and 90 of those pages have never been read by the new code.

The full rebuild in spec 08 replaces the chatbot's data and costs money for embeddings. If the new code fails on a page type the samples did not cover, the failure would only be found after that cost, and inside the chatbot's answers.

A dry run on a small set of live pages, with no database and no API calls, finds those failures first.

There is also a hidden risk in the current code. A run of fewer than 20 pages cannot measure repeated lines, so `scrape_all_pages` falls back to the saved lists in `data/page_furniture.json` and `data/page_contact_lines.json`. Those lists were measured on the old WordPress site. Used on the new pages, they could delete real lines without warning. A dry run must not use them.

## 2. Goal

Show, on real live pages, exactly what the chatbot would learn from each page, with a report that makes every problem visible. Nothing in `data/` changes.

## 3. Scope

In scope:
- About 16 live pages and 3 PDFs, chosen by rule from `url_lists/pages.csv` and `url_lists/pdfs.csv`.
- The same fetch, extract and cleaning code the real rebuild uses (`scrape_all_pages`), with a stand-in database that only records what it is given.
- OCR of images on the selected pages.
- A written report and one text file per page.

Out of scope:
- The real database (ChromaDB, BM25, parent store), embeddings, propositions and Graph RAG.
- Changing scraper or cleaning code. Problems found here are fixed in a follow-up step, after review.
- The page directories (they are built only on a full run, by design in spec 06).

## 4. Functional Requirements

### 4.1 Page selection
The tool picks pages from `url_lists/pages.csv` (only rows with `keep = yes`). Addresses are copied from the file, never typed. It prefers pages that are not among the 25 saved samples (`tests/fixtures/site/index.csv`). In each group, it takes the first pages in alphabetical order:

| Group (address starts with) | Pages | What it tests |
|---|---|---|
| home page `/` | 1 | the live site note (header and footer) |
| `/academics/programs/` | 3 | semester tabs and course tables on other programs |
| `/academics/departments/` | 2 | faculty cards and head-of-department lines |
| `/about/` | 2: the first `…-message` page and `/about/vision-mission` | page titles and long text |
| `/offices/` | 2 | office details, emails and phones |
| `/campus-life/` | 2: the first page under `/campus-life/sports/` and the first other campus-life page | facilities and sports pages (sports is the largest campus group, 15 pages) |
| `/research/` | 1 | research and innovation pages |
| `/news/` | 2 | news articles and dates |
| `/services/` | 1 | service pages |

That makes 16 pages. Three PDFs are taken from `url_lists/pdfs.csv` (`keep = yes`, first three in file order).

### 4.2 Running the real code safely
1. Pages go through `web_scraper.scrape_all_pages(kb, urls=…, include_guaranteed=False)`, the same path as the rebuild.
2. `kb` is a recording stand-in. It stores every call to `load_scraped_page` (text, address, category) in memory and returns "added". It makes no database writes and no API calls.
3. Before the run, every cache path the code writes or reads is redirected to `reports/dry_run_07/cache/`: the furniture list, the contact-line list, the site-note cache, the OCR cache, the PDF cache and the image ownership state. The old lists in `data/` are never read.
4. PDFs go through `pdf_ingest.read_pdf`, with its cache redirected the same way. `scrape_all_pages` also reads every PDF the fetched pages link to (the home footer links all 12). The dry run limits this to the 3 selected PDFs, to keep the run small and gentle on the website.
5. One browser, one page at a time, at least 1 second between pages (the scraper's existing rule).

### 4.3 Outputs
In `reports/dry_run_07/`:
- `pages/<address>.txt`: the final text of each page, exactly as it would be stored.
- `pdfs/<name>.txt`: the text of each PDF.
- `site_note.txt`: the site note built from the live header and footer.
- `report.md`: the summary, described in 4.4.
- `results.json`: the raw result dictionary returned by `scrape_all_pages`.

### 4.4 The report
For each page and PDF, one row with:
- result (`ok`, `empty`, `dead`, `unreachable`, `too_short`) and number of characters
- number of link addresses kept
- number of person lines (for department and faculty pages)
- number of semesters and course rows (for program pages)
- suspected junk lines: any line matching the known junk ("UOLI AI Assistant", "Developed by", "Skip to main content", "Home ›", "Share", "Print", "Profile", "Back to", "Read Article", a bare `###`)
- lines the repeated-line cleaner would remove. A 16-page run is below its 20-page minimum, so it removes nothing during the run. The report therefore runs `find_furniture` once more on the 16 page texts with the minimum lifted, and lists what it would remove. This is a report only: nothing is removed.

Then four short sections:
1. **Problems:** every page with junk, a failure, no title, or 0 characters.
2. **New links:** the `new_links` list (new header or footer items, and new UOLI pages not in `config.py`).
3. **Lines that repeat on 3 or more pages:** with counts, for a human to judge (real content or junk).
4. **Comparison with spec 05/06 expectations:** semester tables found on every selected program page, a head-of-department line on every selected department page, and the site note holding social, portal, contact and document links.

## 5. API Contract

Command:
```
python -X utf8 tools/dry_run_scrape.py
python -X utf8 tools/dry_run_scrape.py --limit 3
```
`--limit N` reads only the first N selected pages and no PDFs. It is used for a quick first test.

Exit code `0` when the run completes, even if some pages failed (failures are listed in the report). Exit code `1` only when the run could not start (for example, Playwright is missing).

New file: `tools/dry_run_scrape.py`. New tests: `tests/test_dry_run_scrape.py`.

## 6. Constraints

- New files only. No change to `web_scraper.py`, `page_furniture.py`, `pdf_ingest.py`, `knowledge_base.py`, `config.py` or any file in `data/`.
- No OpenAI or other API calls. No database writes.
- Playwright and the existing scraper only. No new library.
- Tests are offline: they run the tool's selection, cache redirect and report code on the saved samples, with the fetch replaced by the saved HTML.
- The tool must check, before it fetches anything, that no cache path points into `data/`. If one does, it stops with a clear message.
- The live run is started by the owner. Claude runs only the offline tests.

## 7. Edge Cases and Error Handling

| Case | Behaviour |
|---|---|
| A group has fewer pages than asked | Take what exists and note it in the report. |
| All pages in a group are among the 25 samples | Take sample pages for that group and note it. |
| The site answers 403, 429 or 503 | Wait 10 seconds, retry once. If it fails again, stop the run, save what is done and say so in the report. |
| A page is slow | The scraper's own time limits apply. Record "slow" in the report if loading took over 30 seconds. |
| A PDF cannot be read | Record the reason from `pdf_ingest` and continue. |
| EasyOCR is not installed | Continue without OCR and say so at the top of the report. |
| The run is stopped with Ctrl+C | Write the report for the pages done so far. |
| A cache path would point into `data/` | Stop before any fetch, with a clear message. |

## 8. Acceptance Criteria

1. The selection gives 16 pages and 3 PDFs, all copied from the CSV files, at least 12 of them not among the 25 samples. The report lists them.
2. After the run, `git status` shows no change in `data/` and no change to any existing code file.
3. No OpenAI call is made (the recording stand-in is used, and no embedding code runs).
4. Every selected page and PDF has a row in the report and a text file.
5. The report has the four sections in 4.4.
6. The site note in `site_note.txt` is built from the live header and footer and holds the social pages, the portal links, the contact block and the document links.
7. The delay between pages is never below 1 second, and one browser is used.
8. Offline tests pass: `python -m pytest tests/ -q`.
9. A `--limit 3` live test runs first, and the full dry run only after it is reviewed.
10. Human review: a reviewer reads `report.md`, at least 5 page texts (one program, one department, one office, one news, one campus-life page) and the site note. Every problem found is listed, with a decision: fix before spec 08, or accept.
