# 04 — Study the New Website Structure

Session: `04 study site structure`
Branch: `feature/04-study-site-structure`
Plan: `.claude/plans/04_study_site_structure.md`

---

## 1. Problem Statement

The new React website has a new header, footer, menu, chat widget and card layouts. Our scraper was written for the old WordPress site. If we scrape without knowing the new structure, we store garbage: menu words, footer text, the "UOLI AI Assistant" widget text, and the same lines on every page.

Before we change the scraper (spec 05) or the cleaning rules (spec 06), we need facts:
- Where is the real content on a page?
- Which parts are furniture (header, footer, menu, widget)?
- What would the OLD scraper store today?

This spec only studies. It changes no existing code.

## 2. Scope

Do:
- Open 25 sample pages (one of each type) in the browser and measure them.
- Find the exact place of the real content, and of each furniture part.
- Compare it with what the old scraper would store.
- Save the pages as offline test files for later specs.
- Save screenshots, so you can see the result with your own eyes.

Do not:
- Change the scraper, the cleaning code, `config.py` or any existing file.
- Scrape into the database, or run OCR.
- Open more than 25 pages.

## 3. The 25 Sample Pages

All are in `url_lists/pages.csv`. Why each one:

| Page type | Address (after `https://uoli.edu.pk`) |
|---|---|
| Home | `/` |
| About, messages, leaders | `/about`, `/about/vc-message`, `/about/leadership` |
| Fee table | `/admissions/fees` |
| Long text page | `/admissions/eligibility` |
| Steps | `/admissions/how-to-apply`, `/services/scholarships` |
| Questions and answers (may hide text) | `/admissions/faq` |
| Program list and program page | `/academics/programs`, `/academics/programs/bscs` |
| Empty program page | `/academics/programs/bs-economics` |
| Department list, department page | `/academics/departments`, `/academics/departments/computer-science` |
| Faculty list | `/academics/faculty` |
| Credit-hour tables | `/academics/undergraduate` |
| Office | `/offices/registrar` |
| Contact | `/contact` |
| News list, news article | `/news`, `/news/news-0` |
| Event page, events list | `/research/innovation/events/three-days-olive-value-addition-olive-soap-activity-completed-successfully`, `/campus-life/sports/events` |
| PDF list | `/services/student-resources` |
| Library, privacy | `/services/library`, `/privacy-policy` |

## 4. Functional Requirements

For each sample page, the tool opens it once, waits until the text stops changing, scrolls to the bottom (spec 02b lesson), and records:

1. **Landmarks:** are there `header`, `nav`, `main`, `footer`, `aside` elements? Give the exact CSS selector of each, and the text size inside and outside `main`.
2. **Furniture:** header, footer, menu, breadcrumb, chat widget, pop-ups, back-to-top button, cookie bar. Give the selector of each, and whether it is the same on all pages.
3. **Repeated lines:** lines that appear on at least half of the pages, with counts. This is the furniture list.
4. **Old scraper test:** run the old `web_scraper._extract_text` on the same loaded page, and compare it with the text inside `main`:
   - how much of the old output is outside `main` (garbage)
   - whether any text inside `main` is missing from the old output (lost content)
   - up to 5 example garbage lines per page
5. **Special content:**
   - tables (how many, and one example row)
   - faculty or HOD cards (how they look)
   - hidden text (accordions, tabs, "All Departments" filters): is it in the page when closed?
   - number counters ("100 Faculty Members"): is the label before or after the number?
   - carousel or slider copies of the same text
   - images that carry text
6. **Loading:** time until the content is stable, and whether more content appears after scrolling.
7. **Screenshots:** one picture per page. The real content area has a green box, and each furniture part has a red box.
8. **Offline copies:** save each loaded page as an HTML file (scripts, styles, SVG and base64 images removed, each file at most 500 KB), with an index file.
9. Write a report and a short summary.

Go slowly: one page at a time, at least 1 second apart. The site once answered 503 when we opened three browsers at once, so use one browser only.

## 5. API Contract

Command:
```
python -X utf8 tools/study_site_structure.py --limit N
```
(`--limit` is optional. It studies only the first N pages, for small tests.)

Output:
- `reports/site_structure/structure_report.md` — the summary, easy to read:
  1. The best selector for real content, and how many of the 25 pages agree.
  2. The furniture list (selectors and repeated lines).
  3. Old scraper test: a table with one row per page (garbage %, lost content yes/no).
  4. Special content findings.
  5. Problems and surprises.
- `reports/site_structure/structure_pages.csv` — one row per page with the numbers.
- `reports/site_structure/screens/<page>.png` — the screenshots.
- `tests/fixtures/site/<page>.html` and `tests/fixtures/site/index.csv` (address, file, page type). These are pushed to GitHub, so later specs can test offline.

## 6. Constraints

- New files only: `tools/study_site_structure.py`, `tests/test_study_site_structure.py`, `tests/fixtures/site/`, `reports/site_structure/`.
- Reuse helpers from `tools/find_site_urls.py` where they fit. No copy-paste.
- `playwright` only. No new library. No OpenAI. No database.
- If importing `web_scraper` has side effects (OCR loading, downloads), say so in the plan and import only `_extract_text`.
- Total size of `tests/fixtures/site/` at most 6 MB.
- Claude runs only offline tests. A `--limit 3` live test happens after your OK. You run the full study (about 3 to 6 minutes).

## 7. Edge Cases and Error Handling

- Page loads slowly → wait up to 30 seconds, then use what is there and say "slow".
- Page shows a pop-up or dialog → record it as furniture, do not click it.
- The chat widget is inside an iframe or shadow DOM → record that, and note that normal text reading cannot see it.
- Text only appears after scrolling → scroll first, then measure, and note it.
- `main` is missing on a page → record "no main", and use the biggest content block instead.
- The old `_extract_text` crashes on a page → record the error, keep going.
- The empty program page (`bs-economics`) → record it as "no own content". This is a valid result.
- 403 or 429 → wait 10 seconds, retry once, then log "blocked".
- Stopped with Ctrl+C → save what is done.
- A fixture file is over 500 KB after cleaning → cut the largest repeated block and say so.

## 8. Acceptance Criteria

1. All 25 pages have a row, or a clear reason why not.
2. The report names one selector for real content and says how many pages agree (for example "24 of 25").
3. The report lists the selectors of header, footer, menu, breadcrumb and chat widget.
4. The furniture list is there, with counts, and it contains the lines we already know: "Skip to main content", the phone and email in the header, "Online Admissions", "UOLI AI Assistant", "Developed by".
5. The old scraper test has a row for every page, with garbage % and lost content yes/no.
6. All special content types are covered (tables, cards, hidden text, counters, carousel, images with text), each with at least one real example.
7. 25 screenshots exist, and 25 fixture files with an index. The size limit holds.
8. The delay is never below 1 second, and one browser is used.
9. `git diff` shows no change to existing files.
10. Offline tests pass. They read the fixtures, not the website.
11. Human check: open 3 screenshots (for example home, fees, computer-science). The green box covers only the real content. The red boxes cover the header, footer and widget. If not, tell Claude which page.

## 9. Git

Needed: yes (new files). Pull, new branch, push, PR, merge. The fixtures are pushed too. The `reports/` files stay on your computer.

## 10. What You Do

1. New session → `/rename 04 study site structure`
2. `git pull origin main`
3. `git checkout -b feature/04-study-site-structure`
4. Tell Claude: "Enter plan mode. Read `.claude/specs/04_study_site_structure.md`, `web_scraper.py` (only `_extract_text` and the code around it), `page_furniture.py`, `tools/find_site_urls.py`, and `url_lists/pages.csv`. Save the plan to `.claude/plans/04_study_site_structure.md`."
5. Read and approve the plan. Check each code edit.
6. Say OK for the `--limit 3` test.
7. Run the full study (Claude gives the command).
8. Open `reports/site_structure/structure_report.md` and read it. Do the human check (acceptance 11).
9. Tell Claude: "Commit only the new tool, its test, `tests/fixtures/site/` and the plan file, by name. Do not use `git add .`"
10. Push → PR → merge. Then `git checkout main`, `git pull origin main`, delete the branch.
