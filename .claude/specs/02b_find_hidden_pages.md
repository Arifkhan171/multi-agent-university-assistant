# 02b — Find Hidden Pages (Click Crawl)

Session: `02b find hidden pages`
Branch: `feature/02b-find-hidden-pages`
Plan: `.claude/plans/02b_find_hidden_pages.md`

---

## 1. Problem Statement

Spec 02 found pages by reading `<a href>` links. But some cards and buttons ("Read Article", "View All Events", "See More") move to a page by JavaScript. They have no `href`, so no link-reading tool sees them.

Proof: the Innovation page lists event pages under `/research/innovation/events/...`. Our list had only one of them.

We cannot prove we found every page. But we can make the list much more complete, and we can check it with simple counts.

## 2. Scope

Do:
- Open the hub and list pages of the site, click the "more" buttons and cards, and record where they lead.
- Compare counts the page itself shows ("4 Official Events", "11 Departments") with what we have.
- Give you only the NEW pages to review, in the same format as `url_lists/pages.csv`.
- Add a safe `--append-from` option to `tools/build_url_lists.py`.

Do not:
- Change `config.py` by hand, or any scraper code (spec 05).
- Click anything that sends data, logs in, or downloads.
- Store page text.

## 3. Functional Requirements

1. Read `url_lists/pages.csv`. These are the known pages.
2. Open every kept page that has at most 2 path parts (for example `/news`, `/academics/programs`, `/research/innovation`, `/offices`). About 40 pages.
3. On each page, scroll to the bottom, then click elements that look like "more" actions:
   - buttons, cards, and clickable boxes without a link, whose text has words like: read, view, see more, more, all, load, next, details, explore, events, article.
   - "Load more" / "See more" buttons up to 5 times, to open the full list.
4. After each click, note the new address if it is on `uoli.edu.pk` and different. Then go back to the hub page.
5. Never click: the chat assistant widget, the header menu, the footer, forms and submit buttons, login or apply buttons, download buttons, links to other websites.
6. For every new address: open it, confirm it has its own content (reuse the spec 02 check), and check that it does not move. If it moves, use the final address and write "moved from ..." in the note.
7. Count check: find texts like "4 Official Events", "(4)", "11 Departments" on the hub pages. Show them next to the number of addresses we have for that section.
8. Write the review file. Category is only a suggestion, taken from `config.resolve_category()`.
9. Go slowly: at least 1 second between actions. At most 40 pages and 400 clicks.
10. `tools/build_url_lists.py --append-from <csv>` adds rows that are not yet in `pages.csv`, after the same checks as spec 03. Then `--write` updates `config.py`.

## 4. API Contract

```
python tools/find_hidden_pages.py --limit N
python tools/build_url_lists.py --append-from reports/new_urls/hidden_pages_for_review.csv
```
(`--limit` is optional: stop after N hub pages, for small tests.)

Output in `reports/new_urls/` (stays on your computer):
- `hidden_pages_for_review.csv` — columns: `address, category, keep, note` (same as `url_lists/pages.csv`). Only NEW addresses.
- `hidden_pages_log.csv` — columns: hub page, what was clicked, new address, result.
- `hidden_pages_summary.md` — counts, the count-check table, pages where clicking failed.

## 5. Constraints

- New files: `tools/find_hidden_pages.py`, `tests/test_find_hidden_pages.py`.
- Allowed to change: `tools/build_url_lists.py` and `tests/test_build_url_lists.py` (only the new option). Nothing else.
- Reuse functions from `tools/find_site_urls.py`. No copy-paste.
- `playwright` only. No new library. No OpenAI. No database.
- Claude runs only offline tests (with fake pages). A `--limit 3` live test only after your OK. You run the full crawl (about 15–25 minutes).

## 6. Edge Cases and Error Handling

- A click opens a new tab → record the address, close the tab.
- A click starts a download or opens a PDF → record it as a PDF candidate, do not download.
- A click goes to another website → ignore.
- A click changes nothing → ignore.
- The page moves away → go back; if that fails, reopen the hub page.
- The same card appears many times → click it once (same text and place).
- A "Load more" button that never ends → stop after 5 clicks.
- A new address that moves (like the news → events case) → use the final address.
- A new address already in `pages.csv` (with or without `/`) → not in the review file.
- Site slows or blocks (403/429) → wait 10 seconds, retry once, then log "blocked".
- Stopped with Ctrl+C → save what is done.
- `--append-from` gets a duplicate, bad category or bad address → stop and say which row.

## 7. Acceptance Criteria

1. The crawl finds the event pages that the Innovation page lists (about 4), and the events list page.
2. Every new address was opened and has real content. None is already in `url_lists/pages.csv`.
3. A fake page test: the tool never clicks the chat widget, header, footer or a submit button.
4. The delay is never below 1 second. The limits (40 pages, 400 clicks) hold.
5. The count-check table exists. You check 3 rows by hand on the site.
6. `--append-from` adds only valid rows. Running it twice changes nothing. `--check` has 0 errors, and `--write` updates `config.py`.
7. The spec 01 link check on the new list: no DEAD, no REDIRECTED. NEEDS_REVIEW is fine only for "HTTP 404 but the browser shows content".
8. `git diff` shows only the allowed files.
9. Human check: you read every row of the review file (keep, category), and open 5 random new pages.

## 8. Git

Needed: yes. Pull, new branch, push, PR, merge. The updated `url_lists/pages.csv` is pushed too.

## 9. What You Do

1. New session → `/rename 02b find hidden pages`
2. `git pull origin main`
3. `git checkout -b feature/02b-find-hidden-pages`
4. Tell Claude: "Enter plan mode. Read `.claude/specs/02b_find_hidden_pages.md`, `tools/find_site_urls.py`, `tools/build_url_lists.py`, and `url_lists/pages.csv`. Save the plan to `.claude/plans/02b_find_hidden_pages.md`."
5. Read and approve the plan. Check each code edit.
6. Say OK for the `--limit 3` test.
7. Run the full crawl (Claude gives the command).
8. Open `reports/new_urls/hidden_pages_for_review.csv`. For each row set `keep` (yes/no) and check `category`. Do not touch `address`. Check 3 rows of the count table on the site.
9. Run `--append-from`, then `--check`, then `--write` (Claude gives the exact commands).
10. Run the link check: `python tools/url_health_check.py --type page`
11. Tell Claude: "Commit only the allowed files by name: the new tool, its test, `tools/build_url_lists.py`, its test, `url_lists/pages.csv`, and the plan file. Do not use `git add .`"
12. Push → PR → merge. Then `git checkout main`, `git pull origin main`, delete the branch.
