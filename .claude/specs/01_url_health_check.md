# 01 — URL Health Check

Session: `01 url health check`
Branch: `feature/01-url-health-check`
Plan: `.claude/plans/01_url_health_check.md`

---

## 1. Problem Statement

The university website moved from WordPress to React. Our code has old links. Some are dead, some redirect to a new address, some still work. We need to know which is which before we scrape.

Trap: a React site can say "OK" for a page that does not exist and only show "Not found" on the screen. So pages must also be opened in a real browser.

## 2. Scope

Do:
- Check every `uoli.edu.pk` link written in our `.py` files (pages, PDFs, images).

Do not:
- Find new links (spec 02).
- Change any existing code (spec 03).
- Scrape content or touch the database.

## 3. Functional Requirements

1. Find all `uoli.edu.pk` links in `.py` files (skip test files, `data/`, `.claude/`). Remember where each was found (file and line).
2. Treat small differences as the same link: ending `/`, `http`/`https`, `www.`.
3. First, open 2 fake addresses to learn what the new site's "Not found" page looks like.
4. For each link: follow redirects and note the final address. For pages, also open them in a browser and read the visible text.
5. Give each link one result:
   - WORKING — opens fine with real content.
   - REDIRECTED — moves to a new address that works (write the new address).
   - DEAD — error, or it looks like the "Not found" page, or a PDF link that opens a web page.
   - NEEDS_REVIEW — goes to the home page, very little text, or the site blocked us.
   - ERROR — timeout or network problem (never call this DEAD).
6. Go slowly: one link at a time, at least 1 second apart.
7. Write a CSV report and a short summary.

## 4. API Contract

Command:
```
python tools/url_health_check.py --limit N --type page|pdf|image|all
```
(Both options are optional. Without them, all links are checked.)

Output, in `reports/url_health/`:
- `url_health_<date>.csv` — one row per link. Columns: link, where found, type, final address, result, reason.
- `url_health_<date>_summary.md` — the number of links in each result, then a list of the dead, redirected (old → new) and review links.

## 5. Constraints

- New files only: `tools/url_health_check.py`, `test_url_health_check.py`, `reports/url_health/`.
- No new libraries (use `requests` and `playwright`).
- No OpenAI calls, no database.
- Claude runs only the offline tests. A 3-link live test happens only after your OK. You run the full check (about 10–15 minutes).

## 6. Edge Cases and Error Handling

- Same link written in two ways → one row.
- Redirect loop → DEAD.
- Redirect to the home page → NEEDS_REVIEW.
- Site blocks us (403/429) → wait, try once more, then NEEDS_REVIEW.
- Timeout → try 2 more times, then ERROR.
- Stopped with Ctrl+C → save what is done so far.
- Fake "Not found" page could not be learned → keep going, warn in the summary.

## 7. Acceptance Criteria

1. Every `uoli.edu.pk` link in the code appears in the report (count matches a simple search).
2. Every row has one result and a reason.
3. A fake address is reported DEAD.
4. The home page is reported WORKING.
5. A timeout is reported ERROR, not DEAD.
6. No existing file is changed (`git diff` is empty).
7. Offline tests pass.
8. You open 3 links from each result in your own browser and they match the report.

## 8. Git

Needed: yes (new files). Pull, new branch, push, PR, merge.
