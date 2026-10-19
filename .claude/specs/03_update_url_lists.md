# 03 — Update the Page and PDF Lists in Code

Session: `03 update url lists`
Branch: `feature/03-update-url-lists`
Plan: `.claude/plans/03_update_url_lists.md`

---

## 1. Problem Statement

The code still has the old WordPress links in three places:
- `config.py` — `UNIVERSITY_PAGES` and `PDF_SOURCES`
- `web_scraper.py` — a second list called `GUARANTEED_PAGES`
- `graph/Agents.py` — about 50 links typed inside the code (offices, departments, leadership, contact, fee pages)

You have now made the final lists: `final_pages.csv` (111 pages) and `final_pdfs.csv` (18 PDFs, 12 kept). Each row has a category and a keep flag.

Main rule of this spec: **no link is typed by hand.** A tool copies the links from your CSV files into `config.py`. A live check at the end proves every link works.

## 2. Scope

Do:
- Write the two lists from your CSV files into `config.py`.
- Make `GUARANTEED_PAGES` come from `config.py` (no second list).
- Move the links typed in `Agents.py` into `config.py`, with new addresses you approved.
- Add address rules for the new site layout, for future pages not in the list.

Do not:
- Scrape anything or touch the database (spec 08).
- Change how the scraper works (spec 05) or how pages are cleaned (spec 06).
- Change search or answer logic. Only link values change.
- Add new categories. Valid ones: admissions, faculty, general, offices, overview, policy.

## 3. Facts we already know

- Not on the new site: Alumni page, bus route list or timetable, Universities Act 2022 PDF, a separate FAO page.
- FAO lives inside `/services/scholarships`. Bus info is inside `/campus-life/facilities` (general areas and hours only).
- The Dean message page exists: `/about/dean-message`.
- The 6 Proforma PDFs fail with 403 even in a real browser. They stay `keep = no`.
- The site answers HTTP 404 for every inner page but still shows the content (React). So the link check must judge by page content, not by status.
- The old `old_to_new` table has wrong matches. Do not use it.

## 4. Functional Requirements

1. Copy your two final files into the project as the saved record:
   `url_lists/pages.csv` and `url_lists/pdfs.csv`.
2. A new tool `tools/build_url_lists.py` reads them and checks:
   - address starts with `https://uoli.edu.pk`, has no `?` or `#`, and has no `/` at the end (only the home page may end with `/`)
   - no address is listed twice
   - `category` is one of the 6 valid categories
   - `keep` is `yes` or `no`
   - every kept PDF has a label
3. `--write` puts the kept rows into `config.py` between two marker lines (`# BEGIN URL LISTS` and `# END URL LISTS`). It changes nothing outside the markers. Running it twice gives the same file.
4. `web_scraper.GUARANTEED_PAGES` is built from `config.UNIVERSITY_PAGES`. The name stays, because `setup_knowledge_base.py` imports it.
5. For the links typed in `graph/Agents.py`:
   - Claude first writes a table in the plan: old link, new link, why. A new link must be in the page list.
   - You approve the table before any edit.
   - The tables then move into `config.py`, and `Agents.py` imports them.
6. In `config.py`, add address rules for the new layout, for example `/academics/departments/...` = faculty. Only add rules. The CSV category always wins over a rule.
7. Fix the example address in the help text of `setup_knowledge_base.py`.
8. Keep the old "why" comments for the home page, the contact page and the registrar page (above the markers).

Starting idea for the Agents table (Claude confirms each one in the plan):

| Used for | New address |
|---|---|
| VC, Pro-VC, Dean messages | `/about/vc-message`, `/about/pro-vc-message`, `/about/dean-message` |
| Registrar message | `/about/registrar-message` |
| Registrar, Treasurer offices | `/offices/registrar`, `/offices/treasurer` |
| Controller of Examination | `/offices/controller-examination` |
| ORIC | `/offices/oric` |
| Directorate of IT | `/offices/directorate-of-it` |
| FAO | `/services/scholarships` |
| 11 departments | `/academics/departments/<same name>` |
| About us | `/about` |
| Contact | `/contact` (drop `contacts-old-new` and `about/contact`) |
| Fee and apply pages | `/admissions/fees`, `/admissions/how-to-apply`, `/admissions` |

## 5. API Contract

```
python tools/build_url_lists.py --check
python tools/build_url_lists.py --write
```
- `--check` reads and validates only. It prints errors and counts.
- `--write` does `--check`, then rewrites the block in `config.py`.

Input columns:
- `url_lists/pages.csv`: `address, category, keep, note`
- `url_lists/pdfs.csv`: `address, category, label, keep, note`

Result in `config.py` (same shape as today, so no other code changes):
```
UNIVERSITY_PAGES = [ {"url": "...", "category": "..."}, ... ]
PDF_SOURCES      = [ {"url": "...", "category": "...", "label": "..."}, ... ]
```

## 6. Constraints

- Allowed to change: `config.py`, `web_scraper.py`, `graph/Agents.py`, `setup_knowledge_base.py` (help text only).
- New files: `tools/build_url_lists.py`, `tests/test_build_url_lists.py`, `url_lists/pages.csv`, `url_lists/pdfs.csv`.
- No new library. No scraping, no database, no OpenAI. The tool never visits the website.
- `config.py` must still load, and its own checks at the bottom must pass.
- Do not change settings or thresholds.
- Claude runs only offline tests. You run the two old test files and the link check.

## 7. Edge Cases and Error Handling

- CSV column name missing → stop, say which column.
- Same address twice, or with and without `/` → stop, show both rows.
- Bad category or `keep` → stop, show the row.
- A kept PDF without a label → stop, show the row.
- Markers missing or twice in `config.py` → stop. Never guess where to write.
- Excel may add odd characters when you save a CSV. The tool reads UTF-8 with or without BOM and removes spaces around values.
- A link in `Agents.py` with no new page → the plan says "no page", and you decide (point to the best page, or remove the entry).
- The old dead image `_AD_IMAGE_URL` in `web_scraper.py` → not changed here (spec 05).

## 8. Acceptance Criteria

1. `--check` reports 0 errors.
2. The addresses in `config.UNIVERSITY_PAGES` are exactly the `keep = yes` rows of `pages.csv`. Same for PDFs (test).
3. The Proforma PDFs and the spreadsheet link are not in `config.py`.
4. `GUARANTEED_PAGES` has the same addresses as `config.UNIVERSITY_PAGES`. No second typed list (test).
5. Every address in the `Agents.py` tables is in the page list (test).
6. No old address is left in code lines: no `/wp-content/`, no `/faculty/<name>`, no `-message` without `/about/`. Addresses left only in comments are listed for you.
7. `import config` works, and its own checks pass.
8. New tests pass (offline).
9. The two old test files give the same results before and after.
10. You run the spec 01 link check. Pass means: no DEAD, no REDIRECTED, no ERROR. NEEDS_REVIEW is fine only when the reason is "HTTP 404 but the browser shows content" (React site).
11. `git diff` touches only the allowed files. `config.py` changes only inside the markers, the new rules, and the moved tables.
12. Human check: open 8 random pages and 3 PDFs from `config.py` in your browser. They open. The categories look right.

## 9. Git

Needed: yes. Pull, new branch, push, PR, merge. The CSV files in `url_lists/` are pushed too, because they are the record of your decisions.

## 10. What You Do

Before the session:
1. Optional decision: about 40 of the 111 pages are news and sports events, all set to `general`. Open `reports/new_urls/final_pages.csv`. If you want to drop old, low-value ones (for example `annual-sports-week-2018`, `annual-sports-week-2024`, `pm-talent-hunt-table-tennis-2023`), set their `keep` to `no`. If unsure, keep everything.
2. Copy the final files:
   ```
   cd "E:/Uni bot/university chatbot evaluation/uni chatbot evaluation"
   mkdir -p url_lists
   cp reports/new_urls/final_pages.csv url_lists/pages.csv
   cp reports/new_urls/final_pdfs.csv url_lists/pdfs.csv
   ```
3. Run the two old tests and save the result (the "before"):
   ```
   python -m pytest test_page_furniture.py -q
   python -m pytest test_ingest_safety.py -q
   ```

In the session:
4. New session → `/rename 03 update url lists`
5. `git pull origin main`
6. `git checkout -b feature/03-update-url-lists`
7. Tell Claude: "Enter plan mode. Read `.claude/specs/03_update_url_lists.md`, `config.py`, `web_scraper.py`, `graph/Agents.py`, and `url_lists/pages.csv` and `pdfs.csv`. Save the plan to `.claude/plans/03_update_url_lists.md`."
8. Read and approve the plan. Approve the Agents link table. Check each code edit.
9. Run `--check`, then `--write` (Claude gives the exact commands).
10. Run the two old tests again and compare. Run the link check. Do the human check.
11. Tell Claude: "Commit only the allowed files, the new tools, tests, `url_lists/` and the plan file. Do not use `git add .`"
12. Push → PR → merge. Then `git checkout main`, `git pull origin main`, delete the branch.
