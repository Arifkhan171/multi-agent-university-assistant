# Plan 02 — Find All New Pages and PDFs

Spec: `.claude/specs/02_find_new_urls.md`
Branch: `feature/02-find-new-urls`
(After you approve, I copy this file to `.claude/plans/02_find_new_urls.md`.)

New files only: `tools/find_site_urls.py`, `tests/test_find_site_urls.py`. No existing file is edited.

## What spec 01 taught us (shapes the design)

1. The React site answers HTTP 404 to almost everything, then its own router shows the page. **So HTTP status is useless for pages. We judge by what the browser shows.**
2. A made-up address often **jumps to a real section page** (`/academic-rules` -> `/academics`, `/tender-notice-nit` -> `/news`). So "opened with content" proves nothing. Rule: if the address the browser ends on is different from the address we asked for, the row is **DUPLICATE** of the final address, never REAL.
3. The site blocked us at 1 s delay in spec 01. Keep 2 s (`DELAY_SECONDS`), never below 1 s.
4. All 12 old PDFs (`/wp-content/uploads/...`) give 403, even in the browser. New PDFs probably live somewhere else, so we collect PDF links from the pages themselves.
5. "Not found" text is learned from 2 fake pages (spec 01 `probe_not_found`). Reuse it as is.

## Reuse from `tools/url_health_check.py` (import, no copy)

`BrowserRenderer`, `probe_not_found`, `looks_not_found`, `with_retries`, `fetch_unblocked`, `retry_with_browser`, `judge_binary`, `normalize`, `classify_type`, `collect_links` (gives the 74 old links), `write_csv`, `FetchTimeout`, `DELAY_SECONDS`, `MIN_TEXT_CHARS`, `DOMAIN`, `USER_AGENT`.

## Steps (small, in order)

1. **Link reader** — `LinkRenderer(BrowserRenderer)`: opens a page like `render()` and also returns every `a[href]` from the page source (DOM query, so hidden menu and footer links are included), plus whether the page has a password box (login). Also returns the final address.
2. **Link filter** — `clean_link(href, base)`: make absolute, drop `#` and `?` parts, drop `mailto:`/`tel:`/other sites/images, treat `www.`/http/trailing `/` as same (via `normalize`). Returns page, pdf or None.
3. **Sitemap reader** — `read_sitemap()`: fetch `sitemap.xml` (requests, fall back to browser fetch if blocked), parse `<loc>`, handle sitemap-index files. Failure = warning, crawl continues from home.
4. **Page judge** — `judge_new_page(requested, final, text, signature, home_text, seen_text, has_login)` returns (result, duplicate_of, reason). Order:
   - login box -> skipped, reason "login"
   - looks like Not found -> NOT_FOUND
   - final address differs from requested, or text equals home text, or text equals an earlier page -> DUPLICATE (+ which one)
   - under 200 characters -> THIN
   - else REAL
   Row keeps title, text length, found-on, in-sitemap yes/no, section (first path part only; `(home)` for `/`). Text itself is never saved, only a hash in memory.
5. **Crawler** — queue starts with sitemap + home page. Breadth-first, depth <= 3, <= 300 pages, one page at a time, `time.sleep(DELAY_SECONDS)`. One row per address (first "found on" kept). A DUPLICATE's final address is added to the queue so it gets its own row. 403/429: wait 10 s, retry once, then ERROR "blocked". Ctrl+C saves rows so far and adds a warning. `--limit N` stops after N pages.
6. **PDF checker** — every PDF link found, one row each. Use `fetch_unblocked` then `retry_with_browser`, then `judge_binary`. Result: real PDF / blocked / not a PDF. Size from Content-Length when available, else "unknown".
7. **Old -> new matcher** — `match_old_links()`: for each of the 74 old links, compare last path words with REAL pages only. Suggest only on a clear match (same last segment, or word overlap >= 50% after dropping filler words like "of", "office", "message"). Otherwise "no match found". We do NOT use the spec-01 redirect targets (step 2 above shows they can be wrong).
8. **Reports** — in `reports/new_urls/` (already git-ignored per spec): `new_pages_<date>.csv`, `new_pdfs_<date>.csv`, `old_to_new_<date>.csv`, `summary_<date>.md` with counts per result, pages not in sitemap, old links with no match, warnings (including "menus that need a click may be missed").
9. **CLI** — `python tools/find_site_urls.py --limit N`.
10. **Offline tests** (`tests/test_find_site_urls.py`, fake renderer, no internet): link filter rules; sitemap parsing; made-up address that jumps to another page is never REAL (acceptance 3); Not-found match is never REAL (4); same text twice -> DUPLICATE; under 200 chars -> THIN; login skipped; one row for `/x` and `/x/`; depth and 300 caps; delay constant >= 1 s (7); every old link gets a row (5); every PDF gets a result (6); Ctrl+C saves partial rows; report columns exactly as spec.

## Check before coding
- `reports/` is ignored in `.gitignore` (I will look; if not, I will tell you rather than edit it, because the spec says no existing file changes).

## What Claude runs
- Offline tests only: `python -m pytest tests/test_find_site_urls.py -v`.
- `python tools/find_site_urls.py --limit 3` only after your OK.

## What you run
- Full crawl (about 10-25 min): `python tools/find_site_urls.py`
- Human check (acceptance 9): click every main menu and footer item, confirm each is in `new_pages`; open 10 random REAL pages.

## Git
New files only. After your review: commit only `tools/`, `tests/` and the plan file (no `git add .`). I tell you when a push is needed; then PR and merge.
