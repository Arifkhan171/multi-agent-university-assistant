# 02 — Find All New Pages and PDFs

Session: `02 find new urls`
Branch: `feature/02-find-new-urls`
Plan: `.claude/plans/02_find_new_urls.md`

---

## 1. Problem Statement

Spec 01 showed that almost all old links are gone. The new React site has a new layout, for example `/about/vc-message` and `/academics/departments/computer-science`.

We need a full, true list of the pages and PDFs that exist on the new site today. The sitemap is not enough: it has only 27 pages, and department pages, registrar and dean messages and student resources are missing from it.

Careful: on this site, a made-up address can show some other real page. So every page must be confirmed by its own content, not only by "it opened".

## 2. Scope

Do:
- Find pages and PDFs on `uoli.edu.pk`.
- Confirm each one is real.
- Show which old links have a new page and which have none.

Do not:
- Change `config.py` or any existing file (spec 03).
- Choose categories. Only `config.py` does that. This spec only writes the section name (the first part of the address, like `academics`).
- Store page text in the project. Keep only the title and the text length.

## 3. Functional Requirements

1. Start from `sitemap.xml` and from the home page menu and footer links.
2. Open pages in the browser (Playwright). Read every link on the page, including links inside menus.
3. Follow only links on `uoli.edu.pk`. Go up to 3 steps deep and stop at 300 pages.
4. Skip: other websites, `mailto:`, `tel:`, images, `#` parts, and `?` parts (page 2, filters).
5. Confirm each page is real. Reuse the "Not found" check from spec 01 (import it, do not copy it).
6. Give each page one result:
   - REAL — has its own content.
   - NOT_FOUND — looks like the "Not found" page.
   - DUPLICATE — has the same content as another address. Write which one.
   - THIN — almost no text (under 200 characters).
   - ERROR — timeout or network problem.
7. Collect every PDF link found on any page. Check that it really opens as a PDF. If it is blocked (403), say so and try once more through the browser.
8. For every old link from spec 01, write the most likely new page. Only suggest when the address words clearly match. Otherwise write "no match found". A human decides in spec 03.
9. Go slowly: one page at a time, at least 1 second apart.
10. Write the reports and a short summary.

## 4. API Contract

Command:
```
python tools/find_site_urls.py --limit N
```
(`--limit` is optional. It stops after N pages, for small tests.)

Output, in `reports/new_urls/` (these files stay on your computer):
- `new_pages_<date>.csv` — columns: address, section, title, text length, found on, in sitemap (yes/no), result, duplicate of, reason.
- `new_pdfs_<date>.csv` — columns: address, found on, link text, size, result, reason.
- `old_to_new_<date>.csv` — columns: old address, likely new address, why, or "no match found".
- `summary_<date>.md` — counts per result, pages not in the sitemap, old links with no match, any warnings.

## 5. Constraints

- New files only: `tools/find_site_urls.py`, `tests/test_find_site_urls.py`. No existing file is changed.
- Import the shared parts from `tools/url_health_check.py`. No copy-paste.
- Use `playwright` and `requests` only. No new libraries. No OpenAI. No database.
- Claude runs only the offline tests. A `--limit 3` live test happens after your OK. You run the full crawl.
- Expected time: about 100–300 pages × 3–5 seconds = 10–25 minutes.

## 6. Edge Cases and Error Handling

- Same page linked many times → one row, first place found is kept.
- Address with and without `/` → one row.
- A made-up path that shows the home page or another page → DUPLICATE, never REAL.
- Menu links that only appear after a click → the crawl reads what is in the page. The summary warns, and the human check (acceptance 9) catches gaps.
- Endless pages (news pages 1, 2, 3...) → skip `?` addresses, and the 300-page limit stops the rest.
- PDF link behind a button or a redirect → follow it once.
- Site slows or blocks us (403/429) → wait 10 seconds, retry once, then ERROR or "blocked".
- Stopped with Ctrl+C → save the rows done so far.
- Page opens a login → skip it and write "login".

## 7. Acceptance Criteria

1. Every page in `sitemap.xml` appears in the report.
2. Every page in the report was opened in the browser and has a result and a reason.
3. A made-up address, added as a test, is never REAL.
4. No page is REAL if it matches the "Not found" check.
5. Every old link from spec 01 has a row in `old_to_new`.
6. Every PDF has a result: real PDF, blocked, or not a PDF.
7. The delay is never below 1 second.
8. No existing file is changed. Offline tests pass.
9. Human check: open the site in your browser. Click every item of the main menu and the footer. Each must be in the list. Open 10 random REAL pages and confirm they are real.

## 8. Git

Needed: yes (new files). Pull, new branch, push, PR, merge. Report files are not pushed (ignored).

## 9. What You Do

1. New session → `/rename 02 find new urls`
2. `git pull origin main`
3. `git checkout -b feature/02-find-new-urls`
4. Tell Claude: "Enter plan mode. Read `.claude/specs/02_find_new_urls.md`, `tools/url_health_check.py`, and the spec 01 reports in `reports/url_health/`. Save the plan to `.claude/plans/02_find_new_urls.md`."
5. Read and approve the plan.
6. Check each code edit.
7. Say OK for the `--limit 3` test.
8. Run the full crawl (Claude gives the command).
9. Do the human check (acceptance 9).
10. Tell Claude: "Commit only `tools/`, `tests/` and the plan file. Do not use `git add .`"
11. Push → PR → merge. Then `git checkout main`, `git pull origin main`, delete the branch.
