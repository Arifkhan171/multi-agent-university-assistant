# Plan 02b: Find Hidden Pages (Click Crawl)

Spec: `.claude/specs/02b_find_hidden_pages.md`
Branch: `feature/02b-find-hidden-pages`
(After you approve, I copy this file to `.claude/plans/02b_find_hidden_pages.md`.)

## Context

The spec 02 tool only follows `<a href>` links. On the React site, some cards and buttons ("Read Article", "View All Events") open a page with JavaScript, so that tool never saw those pages. Example: `/research/innovation` lists about 4 event pages, and `pages.csv` has only 1 of them.

This task adds a small tool that opens the list pages, clicks the "more" buttons and cards, and writes down where they go. You get only the **new** addresses, in the same format as `pages.csv`. A new `--append-from` option in `build_url_lists.py` then adds the rows you approve.

Files changed: `tools/build_url_lists.py`, `tests/test_build_url_lists.py` (only the new option).
New files: `tools/find_hidden_pages.py`, `tests/test_find_hidden_pages.py`.
Nothing else changes. `config.py` is changed only when **you** run `--write`.

---

## What I reuse (no copy-paste)

From `tools/find_site_urls.py`:
- `LinkRenderer`: the browser. My new tool makes a small subclass of it that can also click.
- `clean_link()`: keeps only uoli.edu.pk pages and sorts PDF from page.
- `visit()` / `judge_new_page()` / `open_page()`: the "does this page have its own content" check, with the 403/429 wait-and-retry.
- `PageRow`, `REAL`, `THIN`, `DUPLICATE`, `BLOCKED_CODES`, `BLOCK_WAIT_SECONDS`, `REPORT_DIR`.

From `tools/url_health_check.py`: `probe_not_found()`, `normalize()`, `write_csv()`, `DELAY_SECONDS` (2 s, above the 1 s minimum).

From `config.py`: `resolve_category()` (category suggestion) and `_url_path()` (so `/x` and `/x/` count as the same address).

---

## Steps (small, in order)

### 1. Choose the hub pages
- Read `url_lists/pages.csv`. Keep rows with `keep = yes` and at most 2 path parts (44 today).
- Sort short addresses first (home, `/news`, then `/news/x`). Open the first **40**. The summary names the pages left out (your choice).

### 2. Find what can be clicked on one hub page
- Open the hub, scroll to the bottom slowly so lazy content loads.
- "Load more" / "See more" buttons: click up to **5 times** while the address stays the same.
- Then read the page once in the browser and list the clickable things:
  - `button`, `[role=button]`, `[onclick]`, and boxes with a pointer cursor (cards).
  - Not inside an `<a href>` (spec 02 already covers those).
  - Their text has one of the words: read, view, see more, more, all, load, next, details, explore, events, article.
- **Never clicked** (one filter function, tested):
  - anything inside `header`, `nav`, `footer`, `form`
  - the chat assistant widget (ids/classes with "chat", "assistant", "bot")
  - submit buttons and inputs
  - text with login, sign in, apply, download, submit, register
- Same card many times → one click only (same text and same place on the page).
- Also: links that appeared after "Load more" are read with `clean_link()`. This is cheap and finds lists that open only after the button.

### 3. Click and record
For each item, one at a time, with **2 s** between actions:
- Click. Wait a short time.
- **Address changed** to uoli.edu.pk → record it. Other website → ignore.
- **New tab** opened → record its address, close the tab.
- **Download** or a `.pdf` address → record as a PDF candidate, cancel the download.
- **Nothing changed** → ignore.
- Go back to the hub. If that fails, open the hub again.
- Every click is a row in the log: hub page, what was clicked, new address, result.
- Hard limits: **40 hub pages, 400 clicks** in total. When reached, stop and say so in the summary.

### 4. Check every new address
- Drop addresses already in `pages.csv` (compared with `_url_path`).
- Open each new one with `visit()` (same check as spec 02).
- If it moves to another address → use the final address, note "moved from ...", and check the final address again (and drop it if it is already known).
- Site blocked (403/429) → wait 10 s, retry once, then "blocked" in the log.

### 5. Count check
- On every hub page, find texts like "4 Official Events", "11 Departments", "(4)".
- Table in the summary: hub page, text found, number on the page, number of addresses we now have under that hub (known + new).
- You check 3 rows by hand on the site.

### 6. Write the reports to `reports/new_urls/`
- `hidden_pages_for_review.csv`: `address, category, keep, note`, only NEW addresses.
  - `category` = `config.resolve_category()` (a suggestion). If it says "skip", I write `general`, `keep = no`, with that note.
  - `keep = yes` for pages with real content. `keep = no` for "very little text", with the reason in `note`.
- `hidden_pages_log.csv`: one row per click.
- `hidden_pages_summary.md`: counts, the count-check table, PDF candidates, hub pages where clicking failed, hub pages left out, limits reached.
- A `--limit` test run gets `_limit3` in the file names, so it never overwrites the full run (as in spec 02).
- **Ctrl+C** → save what is done.
- No page text is saved. Only title and text length (as in spec 02).

### 7. `tools/build_url_lists.py --append-from <csv>`
- New option next to `--check` and `--write` (only one of them at a time).
- Reads the file with the same `read_rows()` and `check_rows()`. Any bad row (bad address, bad category, bad keep, or the same address twice in that file) → **stop**, name the row, change nothing.
- Rows already in `pages.csv` → skipped, with a message. So running it twice changes nothing.
- The rest are added at the end of `pages.csv` (same columns, same line endings). Then the full check runs on the new `pages.csv`.
- It does **not** touch `config.py`. You run `--check` and then `--write` after it.

### 8. Tests (offline, fake pages, no internet)
`tests/test_find_hidden_pages.py`:
- Hub choice: only `keep = yes`, at most 2 path parts, short first, stop at 40, the rest listed.
- Click filter: chat widget, header, nav, footer, form, submit button, login/apply/download are never chosen. A "Read Article" card and a "View All Events" button are chosen.
- Same card twice → one click.
- "Load more" stops after 5.
- New tab, download/PDF, other website, nothing changed: each handled as in step 3.
- Moved address → final address used, "moved from" note.
- Address already in `pages.csv` (with or without `/`) → not in the review file.
- Every sleep is at least 1 s. 400 clicks and 40 hubs are never passed.
- Count check finds "4 Official Events", "11 Departments", "(4)".
- Ctrl+C → files still written.

`tests/test_build_url_lists.py` (new tests only):
- Adds valid rows. Second run adds nothing.
- Stops on duplicate, bad category, bad address; names the row; `pages.csv` unchanged.

---

## Verification

1. I run (offline): `python -m pytest tests/test_find_hidden_pages.py tests/test_build_url_lists.py tests/test_find_site_urls.py -v`, and `git diff --stat` (only the allowed files).
2. After your OK, I run the small live test: `python tools/find_hidden_pages.py --limit 3`.
3. You run the full crawl: `python tools/find_hidden_pages.py` (about 15–25 min).
4. You read `reports/new_urls/hidden_pages_for_review.csv` (`keep`, `category`) and check 3 rows of the count table.
5. You run:
   - `python tools/build_url_lists.py --append-from reports/new_urls/hidden_pages_for_review.csv`
   - `python tools/build_url_lists.py --check` (0 errors)
   - `python tools/build_url_lists.py --write`
   - `python tools/url_health_check.py --type page` (no DEAD, no REDIRECTED)
6. Pass = the Innovation event pages (about 4) and the events list page are found. You open 5 random new pages.
7. Git: I commit only the named files (no `git add .`). You push, open the PR, merge. I give the commands.
