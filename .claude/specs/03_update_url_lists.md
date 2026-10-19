# 03 — Update the Page and PDF Lists in Code

Session: `03 update url lists`
Branch: `feature/03-update-url-lists`
Plan: `.claude/plans/03_update_url_lists.md`

---

## 1. Problem Statement

The code still has the old WordPress links in these places:
- `config.py` — `UNIVERSITY_PAGES` and `PDF_SOURCES`
- `web_scraper.py` — a second list called `GUARANTEED_PAGES`
- `graph/Agents.py` — about 50 links typed inside the code (offices, departments, leadership, contact, fee pages)

Spec 02 found the real new pages (108) and PDFs (18). It did not give them categories.

The biggest danger is a wrong or mistyped link. So the main rule of this spec is:

**No link is ever typed by hand.** Every link is copied by a tool from the spec 02 report, and then checked against it letter by letter.

## 2. Scope

Do:
- Make an Excel sheet from the spec 02 report, so you can review each page and PDF and set its category.
- Write the reviewed lists into `config.py`.
- Make `GUARANTEED_PAGES` come from `config.py` (no second list).
- Move the links typed in `Agents.py` into `config.py`, with new addresses that you approved.
- Add address rules for the new site layout, for pages not in the list.

Do not:
- Scrape anything or touch the database (spec 08).
- Change how the scraper works (spec 05) or how pages are cleaned (spec 06).
- Change search or answer logic. Only link values change.
- Add new categories. Valid ones: admissions, faculty, general, offices, overview, policy.

## 3. Facts from spec 02 (already checked)

- Use only `new_pages_2026-10-19.csv` and `new_pdfs_2026-10-19.csv`. The `_limit3` files are test files. Do not use them.
- The table `old_to_new` is a hint only. It has wrong matches. For example it maps `/office-of-registrar/` to `/about/registrar-message`, but the real page is `/offices/registrar`. It maps the old `...ED-Rules.pdf` to `Conduct-Rules`, but the right file is `E&D-Rules`. It says "no match" for `/admission/` and `/fee-structure/`, but `/admissions` and `/admissions/fees` exist. Never replace a link because of this table.
- All pages have the same title, so a title cannot prove a page is right.
- One row in the pages report is a spreadsheet file (`Sports Week 2026 Draw Updated.xlsx`), not a page. It must not be in the list.
- 6 PDFs are BLOCKED (403): the `Proforma-for-...pdf` files under `/wp-content/uploads/2026/04/`. They are not loaded in this spec.
- There is no `dean-message` page and no FAO page in the report. `Agents.py` uses both, so you decide what they point to.

## 4. Functional Requirements

1. A new tool makes `url_lists/pages.xlsx` and `url_lists/pdfs.xlsx` from the two spec 02 CSV files.
   - Columns for pages: `address`, `category`, `keep`, `note`, and for your help `section`, `text length`, `why this category`.
   - Columns for PDFs: `address`, `category`, `label`, `keep`, `note`, and for your help `size`, `found on`, `why this category`.
   - `category` is filled with a suggestion (see section 6). You change it where you disagree.
   - `keep` is `yes` for every real page and PDF. The spreadsheet file and the 6 blocked PDFs are `no`, with a note.
2. You review the Excel. You may change `category`, `keep`, `label` and `note`. You must not change `address`.
3. The tool checks the Excel and stops with a clear message if anything is wrong:
   - every `address` is exactly (letter by letter) an address in the spec 02 CSV
   - no address is listed twice
   - `category` is one of the 6 valid categories
   - `keep` is `yes` or `no`
   - every kept PDF has a label and was REAL_PDF in the CSV
   - no kept page was ERROR in the CSV
4. The tool writes the two lists into `config.py` between two marker lines (`# BEGIN URL LISTS` and `# END URL LISTS`). It changes nothing outside the markers. Running it twice gives the same file.
5. `web_scraper.GUARANTEED_PAGES` is built from `config.UNIVERSITY_PAGES`. The name stays, because `setup_knowledge_base.py` imports it.
6. For the links typed in `graph/Agents.py`:
   - Claude first writes a table in the plan: old link, new link, and why. Only links that exist in the page list may be new links.
   - You approve the table before any edit.
   - Then the tables move into `config.py` and `Agents.py` imports them.
7. In `config.py`, add address rules for the new layout. Only add rules. The Excel category always wins over a rule.
8. Fix the one example address in the help text of `setup_knowledge_base.py`.
9. Keep the old "why" comments for the home page, the contact page and the registrar page.

## 5. API Contract

Commands (the two CSV paths are always given, so there is no guessing which file is used):
```
python tools/build_url_lists.py --make-excel --pages-csv reports/new_urls/new_pages_2026-10-19.csv --pdfs-csv reports/new_urls/new_pdfs_2026-10-19.csv
python tools/build_url_lists.py --check      --pages-csv ... --pdfs-csv ...
python tools/build_url_lists.py --write      --pages-csv ... --pdfs-csv ...
```
- `--make-excel` creates the two Excel files. It refuses to overwrite a file that already exists.
- `--check` reads and validates only.
- `--write` does `--check`, then rewrites the block in `config.py`.

Result in `config.py` (same shape as today, so no other code changes):
```
UNIVERSITY_PAGES = [ {"url": "...", "category": "..."}, ... ]
PDF_SOURCES      = [ {"url": "...", "category": "...", "label": "..."}, ... ]
```
The home page is written `https://uoli.edu.pk/` (with `/`). All other addresses have no `/` at the end.

## 6. Suggested categories (a starting point, you decide)

| Address | Suggested category | Reason |
|---|---|---|
| home, `/contact` | general | Old measured note: `overview` would feed the address block into faculty search |
| `/admissions/...` | admissions | |
| `/academics/undergraduate`, `/graduate`, `/programs`, `/programs/...` | admissions | Old program pages were admissions |
| `/academics/departments/...`, `/academics/faculty` | faculty | Old department pages were faculty |
| `/academics` | overview | Hub page |
| `/about`, `/about/...` | overview | Same as old VC, registrar messages |
| `/offices`, `/offices/...` | offices | |
| `/offices/registrar` | overview | Old measured note: the registrar page stays `overview`. Change only after a retrieval test |
| `/research/oric` | offices | Old ORIC page was offices |
| `/campus-life/...`, `/services/...`, `/research`, `/news/...`, others | general | |
| PDFs: admission form | admissions | |
| PDFs: all other (rules, acts, policies) | policy | Same as the old list |

## 7. Constraints

- Allowed to change: `config.py`, `web_scraper.py`, `graph/Agents.py`, `setup_knowledge_base.py` (help text only).
- New files: `tools/build_url_lists.py`, `tests/test_build_url_lists.py`, `url_lists/pages.xlsx`, `url_lists/pdfs.xlsx`.
- `openpyxl` is already used in the project. No other new library.
- No scraping, no database, no OpenAI, no website requests in the tool.
- `config.py` must still load, and its own checks at the bottom must pass.
- Do not change settings or thresholds.
- Claude runs only offline tests. You run the two old test files and the link check.

## 8. Edge Cases and Error Handling

- An address in Excel that is not exactly in the CSV (a typo, or Excel changed it) → stop, show the row.
- Same address twice, or with and without `/` → stop, show both rows.
- Invalid category or `keep` → stop, show the row.
- A kept PDF that was BLOCKED → stop, tell you to set `keep` to `no` or explain in `note`.
- The markers are missing or appear twice in `config.py` → stop. Never guess where to write.
- `--make-excel` and the file already exists → stop. Your edits are never overwritten.
- A page missing from the list (found later in the menu check) → add a row at the bottom of the Excel with the exact address from a new spec 02 CSV, and run `--check` again.
- A link in `Agents.py` with no new page (dean message, FAO) → the plan table says "no page"; you decide to point it to the best page or remove the entry.
- Old dead image `_AD_IMAGE_URL` in `web_scraper.py` → not changed here (spec 05).

## 9. Acceptance Criteria

1. `--check` reports 0 errors.
2. The addresses in `config.UNIVERSITY_PAGES` are exactly the `keep = yes` addresses in `pages.xlsx`, and every one of them is in the spec 02 CSV (test, letter by letter). Same for PDFs.
3. The spreadsheet file and the blocked PDFs are not in `config.py`.
4. `GUARANTEED_PAGES` has the same addresses as `config.UNIVERSITY_PAGES`. There is no second typed list (test).
5. Every address in the `Agents.py` tables is in the page list (test).
6. No old address is left in code lines: no `/wp-content/`, no `/faculty/<name>`, no `-message` without `/about/`. Addresses left only in comments are listed for you.
7. `import config` works, and its own checks pass.
8. New tests pass (offline).
9. The two old test files give the same results before and after.
10. You run the link check from spec 01. Every address in the code is WORKING. Any other result is explained.
11. `git diff` touches only the allowed files. `config.py` changes only inside the markers, the new rules, and the moved tables.
12. Human check: open 8 random pages and 3 PDFs from `config.py` in your browser. They open. The categories look right.

## 10. Git

Needed: yes. Pull, new branch, push, PR, merge. The Excel files in `url_lists/` are pushed too, because they are the record of your decisions.

## 11. What You Do

Before the session:
1. Finish the spec 02 menu check: open the site, click every main menu and footer item, and see that each page is in `new_pages_2026-10-19.csv`. If a page is missing, tell Claude before spec 03 starts.
2. Run the two old tests and save the result (the "before"):
   ```
   python -m pytest test_page_furniture.py -q
   python -m pytest test_ingest_safety.py -q
   ```
3. If you already made an Excel with categories, put it in `url_lists/` and tell Claude in the plan session. Otherwise the tool makes one for you.

In the session:
4. New session → `/rename 03 update url lists`
5. `git pull origin main`
6. `git checkout -b feature/03-update-url-lists`
7. Tell Claude: "Enter plan mode. Read `.claude/specs/03_update_url_lists.md`, `config.py`, `web_scraper.py`, `graph/Agents.py`, and the spec 02 CSV files in `reports/new_urls/`. Save the plan to `.claude/plans/03_update_url_lists.md`."
8. Read and approve the plan. Check each code edit.
9. Run `--make-excel` (Claude gives the exact command). Open the two Excel files and review `category` and `keep` for every row. Do not touch `address`. Save.
10. Run `--check`, fix any error, then run `--write`.
11. Approve the `Agents.py` link table. Claude edits.
12. Run the two old tests again and compare. Run the link check. Do the human check.
13. Tell Claude: "Commit only the allowed files, the new tools, tests, `url_lists/` and the plan file. Do not use `git add .`"
14. Push → PR → merge. Then `git checkout main`, `git pull origin main`, delete the branch.
