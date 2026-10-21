# 05 — Update the Scraper for the React Website

Session: `05 update scraper`
Branch: `feature/05-update-scraper`
Plan: `.claude/plans/05_update_scraper.md`

---

## 1. Problem Statement

Our scraper (`web_scraper.py`) was written for the old WordPress site. Spec 04 showed that on the new React site it stores wrong text and loses real text.

Facts found in the offline page copies (`tests/fixtures/site/`):

1. **Page titles are lost.** Inside `main#main-content` every page has its own title box, and it is a `<header>` tag (`header.relative`) that holds the `<h1>`. The old scraper deletes every `<header>` tag, so it also deletes the page title ("Vice Chancellor's Message", "BS Computer Science" and others, about 11 pages).
2. **Only 1 of 8 semesters is read.** The BS CS page shows the courses of Semester I only (6 courses). Semesters II to VIII appear only after a click on the "Sem 2" … "Sem 8" buttons. Students will ask about these courses.
3. **Empty pages leak garbage.** BS Economics shows only "Program Details Not Found". Its text is under 100 characters, so the old scraper falls back to the whole page and stores the chat widget and footer text.
4. **Faculty cards are not read as people.** The old card code looks for `div.faculty-card`, which no longer exists. The names, jobs and emails are still in the text (90 people on `/academics/faculty`), but as loose lines mixed with "Profile", "→" and initials like "DA".
5. **Small button labels leak**: "Sem 1", "Share", "Print", "Profile", "→", "Tap course for syllabus".
6. **Long pages are cut.** `config.MAX_PAGE_CHARS` is 15,000. `/academics/undergraduate` already has 17,681 characters, so its end is lost. With 8 semesters, program pages grow too.
7. **The breadcrumb** ("Home › Academics › …", `nav[aria-label="Breadcrumb"]`) is inside `main` and is stored on every page.
8. **Old WordPress leftovers:** the `_NAV_NOISE` menu-word list (it can delete a real line like "Computer Science"), `_FOOTER_NOISE` holds "muhammad khawar abbas" (old developer credit, but M. Khawar Abbas is now the real CS HOD), the dead ad image `_AD_IMAGE_URL`, and `_faculty_department` reads old `/faculty/<dept>` addresses.
9. **Slow start:** EasyOCR loads at import time, even when no image is read.
10. **The delay between pages is 0.8 seconds.** Our rule is at least 1 second.
11. **The extra crawler is blind.** The rebuild runs `advanced_crawler.py` (requests and BeautifulSoup) after the page list. It cannot see React links, so it only adds noise.

## 2. Scope

Do:
- Change only how one page is opened and how its text is taken out.
- Test everything offline on the 25 page copies.

Do not:
- Change the cleaning code (`page_furniture.py`, `knowledge_base.py`). That is spec 06.
- Change categories, link lists, chunk sizes, embeddings or thresholds.
- Scrape the live site in bulk. That is spec 07 (15 pages) and spec 08 (full).
- Follow "Profile" links or click course rows ("Tap course for syllabus"). That is a later spec, if needed.

## 3. Functional Requirements

**A. Opening a page (`_fetch_with_playwright`)**
1. Wait until the page has loaded, then scroll to the bottom (spec 02b lesson).
2. If `main#main-content` has buttons named "Sem 1", "Sem 2" …, click each one in order, wait until the table changes, and keep a copy of each semester's table. Add each copy to the page HTML under a heading "Semester I", "Semester II" … so the text step can read all of them.
3. Wait at least 1 second between pages.

**B. Taking the text out (`_extract_text`)**
1. If `main#main-content` exists, read only it. Never fall back to the whole page.
2. Remove the breadcrumb inside it.
3. Keep the page title box (`header` inside `main`). Remove only the site parts outside `main`.
4. Faculty people: each person becomes one line, for example:
   `M. Khawar Abbas — Lecturer, Department of Computer Science. Email: m.khawar@uoli.edu.pk.`
   A person marked "HOD" or "Department Head" also gets the line:
   `M. Khawar Abbas is the Head of the Department of Computer Science at the University of Loralai.`
   The department comes from the person's own text, not from the address.
5. Drop exact button labels: "Sem N" (after the tables are read), "Share", "Print", "Profile", "→", "Tap course for syllabus", "Showing N Courses", and 2-letter initials that sit next to a person.
6. Remove `_NAV_NOISE` and the "muhammad khawar abbas" item from `_FOOTER_NOISE`. They belonged to the old site.
7. Keep tables as rows, as today.

**C. Empty pages**
- If a page's own text (after removing the breadcrumb and the title) is shorter than 150 characters, or it shows "Program Details Not Found" / "We could not find the requested", do not store it. Return the reason `"empty"` and log it.

**D. Other fixes**
1. Raise `config.MAX_PAGE_CHARS` to 40,000, and log a warning when a page is cut.
2. Load EasyOCR only the first time an image is read.
3. Remove the dead `_AD_IMAGE_URL`. The home page change check uses text only.
4. The crawler decision: turn off Phase B (`advanced_crawler.py`) in `setup_knowledge_base.py` with a config switch `USE_AUTO_CRAWLER = False`. The page list in `config.py` (115 pages from 3 crawls) is the only source. New pages are found by re-running the spec 02 and 02b tools by hand.
5. Update the old comments in the code you touch (for example "footer and aside do not exist").

## 4. API Contract

No new command. The same functions keep their names and inputs:

- `fetch_page(url) -> (text or None, reason)`. The reason is one of `"ok"`, `"dead"`, `"too_short"`, `"unreachable"`, and the new `"empty"`.
- `_extract_text(soup, base_url) -> str`
- `scrape_page(url) -> str or None`
- New in `config.py`: `USE_AUTO_CRAWLER = False`. Changed: `MAX_PAGE_CHARS = 40000`.

The text keeps today's format: `#` for headings, `•` for list items, `| a | b |` for table rows.

## 5. Constraints

- Files that may change: `web_scraper.py`, `config.py` (two settings only), `setup_knowledge_base.py` (the Phase B switch only), `pdf_ingest.py` (one line only: get the OCR reader through `web_scraper._get_ocr_reader()`), and new tests in `tests/test_web_scraper_react.py`.
- `playwright` and BeautifulSoup only. No new library. No OpenAI.
- Tests read the files in `tests/fixtures/site/`. No internet in tests.
- The clicking part (A2) cannot be tested on the copies, because they are saved files. Test it with a small fake HTML page that has working tabs, plus one live check of the BS CS page after your OK.
- Claude does not run `web_scraper.py`, `setup_knowledge_base.py` or any scrape.
- Old test scripts must not get worse: `python test_page_furniture.py` (2 known FAIL) and `python test_ingest_safety.py` (3 known FAIL).

## 6. Edge Cases and Error Handling

- `main#main-content` is missing on a page → use the old way, and log "no main".
- A semester click does not change the table within 5 seconds → keep what is there, log it, and go on.
- A page has tabs that are not "Sem N" → do not click them. Only log them.
- A person has no email → write "Email not listed." (as today).
- A person's text has no department → write the line without it.
- An HOD appears twice (top box and list) → write their lines once.
- An "empty" page is only skipped for this run. It stays in the `config.py` list and is checked again on every run, so it is stored as soon as the website fills it. Nothing is written to a "never again" list.
- A long page is cut → log the address and size.
- A page breaks the text step → log the error, return `"unreachable"`, and go on with the next page.

## 7. Acceptance Criteria

On the offline copies:
1. All 25 pages: the stored text has no breadcrumb, no "UOLI AI Assistant", no "Developed by", no "Skip to main content".
2. The page title is kept on the 11 pages that lost it in spec 04 (the plan lists them, taken from `reports/site_structure/structure_report.md`).
3. The event page keeps "SESSION LEAD & FACILITATOR" and "Supervision / Partner".
4. `bs-economics` returns `"empty"`.
5. `computer-science`: 9 people lines (one per email), each with the right email, and one HOD line for M. Khawar Abbas (he appears twice on the page, but is written once).
6. `/academics/faculty`: 90 people lines, each with a department.
7. No line is exactly "Share", "Print", "Profile", "→" or "Sem 1".
8. `/academics/undergraduate` is not cut.
9. A fake tabs page gives the courses of all its semesters.
10. Importing `web_scraper` does not load EasyOCR.
11. `pytest tests/` passes. The old scripts have the same FAIL count as before.
12. `git diff` shows changes only in the allowed files (section 5).

Live (after your OK, one page):
13. The BS CS page gives the courses of all 8 semesters (about 48 courses).

Human check:
14. Open the saved text of 3 pages (the plan says where): `about/vc-message`, `academics/departments/computer-science` and `academics/programs/bscs`. Read it. It should look like the page, with no menu or footer.

## 7b. Decisions after the plan review (21 Oct)

1. OCR: `pdf_ingest.py` calls `web_scraper._get_ocr_reader()` (one line), so scanned PDFs still work.
2. Filter buttons: drop the text of a button group when one of its buttons starts with "All" ("All Departments", "All Events (12)"). A test must prove the FAQ questions on `admissions/faq` are all kept.
3. Also drop these exact labels: "Fullscreen", "Tap course for details", "View Outline →", "Try Again", "Ask AI", "Ask AI Assistant".
4. When the semester tables were copied, remove the original course area (the smallest box that holds all the "Sem N" buttons and the table, including the phone copy of the list). Find it by its structure, not by Tailwind class names.
5. "empty" pages get their own counter, like "dead". They are not counted as "failed".
6. A "Visiting" badge goes into the line, for example "Lecturer (Visiting)".
7. Use `config.UNIVERSITY_NAME` in the HOD line.
8. `_FOOTER_NOISE` is used only on the fallback path (a page with no `main#main-content`), because the footer is already outside `main`.

## 8. Git

Needed: yes. Pull, new branch, push, PR, merge.

## 9. What You Do

1. Planning session: paste the prompt from `.claude/PLANNING_PROMPT.md` with `NN_name` = `05_update_scraper`. It saves `.claude/plans/05_update_scraper.md` and stops.
2. Bring the plan to the supervisor session for review. Fix it until it is approved.
3. New coding session (Sonnet) → `/rename 05 update scraper`
4. `git pull origin main`
5. `git checkout -b feature/05-update-scraper`
6. Tell it: "Read `.claude/PROJECT_STATUS.md`, `.claude/specs/05_update_scraper.md` and the approved plan `.claude/plans/05_update_scraper.md`. Do the plan step by step. Show me each edit. Do not commit until I say so." Check each edit, and bring the code to the supervisor for review.
7. Run the old scripts when Claude asks (Claude gives the commands).
8. Say OK for the one live BS CS page check.
9. Do the human check (acceptance 14).
10. Tell Claude: "Commit only the changed files and the plan, by name. Do not use `git add .`"
11. Push → PR → merge. Then `git checkout main`, `git pull origin main`, `git branch -D feature/05-update-scraper`.
