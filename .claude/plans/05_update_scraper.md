# Plan 05: Update the Scraper for the React Website

Spec: `.claude/specs/05_update_scraper.md` (including section 7b, decisions of 21 Oct)
Branch: `feature/05-update-scraper`
Status: updated after the supervisor's 8 decisions. Waiting for the owner's review.

---

## 1. Goal

Make `web_scraper.py` read the new React pages correctly: keep the real text (titles, all 8 semesters, people), drop the junk (breadcrumb, buttons, filter bars, chat widget), and skip empty pages. Everything is tested offline on the 25 saved pages.

---

## 2. What I found in the code and the page copies (facts, checked)

1. **There are two `main#main-content` tags.** 24 of 25 pages have one inside the other (not the home page). The outer one holds the breadcrumb, the title `<header>` and the inner `main`. `soup.select_one("main#main-content")` returns the outer one, which is the one we want.
2. **Inside the outer `main` there are 24 breadcrumbs, 24 title `<header>`s and 1 `<aside>`.** The `<aside>` is on the event page and holds the 17 lost lines ("SESSION LEAD & FACILITATOR"). So we must stop deleting `<aside>` inside `main`, not only `<header>`.
3. **Each person card has a "Profile →" button.** That gives 9 cards on the CS page and 90 on `/academics/faculty`, each with exactly one `<h3>` name. All 99 cards show a department line. The CS top box (welcome message, `<h4>` M. Khawar Abbas) has **no** Profile button, so it stays as normal text.
4. **The CS HOD card has the badges "HOD" and "Department Head"**, then "Lecturer". Some faculty cards have a "Visiting" badge.
5. **Three faculty emails are Gmail.** The email is read from the `mailto:` link, so this works.
6. **Filter bars ("All…" groups) are on 10 pages.** The smallest box around an "All…" button holds only filter buttons (3 to 12 of them) and no other text. On `admissions/faq` the filter bar ("All Questions", "General", …) is separate from the **10 question buttons**, which end with "?" and are kept. Careful: "Allied Health Sciences" also starts with the letters "All", so the rule must match the whole word `All` (`^All\b`).
7. **BSCS course area.** The smallest box that holds all 8 "Sem N" buttons and the table also holds the heading "Curriculum Breakdown", the line "Semester I • 18 Cr. Hrs", and the phone copy of the course list. The table's last row is "Total Courses: | 6 | 18 Cr. Hrs | Tap course for syllabus", so the totals are inside the table and the copies keep them.
8. **BS Economics:** its own text is 91 characters ("Program Details Not Found … Try Again"), which is under 150, so it will be marked "empty".
9. **`/academics/undergraduate`** has 17,681 characters in `main`.
10. **The OCR reader is used outside the scraper.** `pdf_ingest.py:362` reads `web_scraper._OCR_READER`. `tools/study_site_structure.py:124` and its test need `OCR_AVAILABLE` to be `False` when easyocr is blocked.
11. **One browser at a time is already true.** `scrape_all_pages` forces `_workers = 1`.
12. **`config.UNIVERSITY_NAME`** is "University of Loralai (UOLI)" (`config.py:188`).

---

## 3. Files to change

| File | Function / place (line) | What changes |
|---|---|---|
| `web_scraper.py` | EasyOCR block (68–80) | Only check that easyocr is installed (`importlib.util.find_spec`). Do not import it or build the reader at import time. `OCR_AVAILABLE` keeps its meaning. |
| `web_scraper.py` | new `_get_ocr_reader()` | Builds the EasyOCR reader the first time it is needed and reuses it. Returns `None` if OCR is not available. |
| `web_scraper.py` | `_read_image_text` (322, reader used at 362) | Use `_get_ocr_reader()`. |
| `web_scraper.py` | `_NAV_NOISE` (91–103) | Delete it, and its use at 928. |
| `web_scraper.py` | `_DEPT_HEADING_RE`, `_DEPT_SLUG_OVERRIDES`, `_faculty_department`, `_parse_faculty_card`, `_extract_faculty_cards` (617–775) | Replace with React card code: `_find_person_cards(main)`, `_person_lines(...)`, `_extract_people(main, base_url)`. Keep the WHY comment about "Email not listed", shortened. |
| `web_scraper.py` | new `_remove_filter_bars(main)` | Decision 2: removes the buttons of each group that has a button starting with the word "All". |
| `web_scraper.py` | new `_remove_course_area(main)` | Decision 4: if the semester copies exist, removes the original course area (found by structure). |
| `web_scraper.py` | `_extract_text` (778–956) | Use `main#main-content` only. Remove landmarks only outside `main`. Remove the breadcrumb. Call `_remove_course_area` and `_remove_filter_bars`. Add the full label drop list. Use `_FOOTER_NOISE` only on the fallback path (decision 8), without "muhammad khawar abbas". Leave out label cells in table rows. Update old comments. |
| `web_scraper.py` | new `_is_empty_page(soup)` | True if the page's own text is under 150 characters, or shows "Program Details Not Found" / "We could not find the requested". |
| `web_scraper.py` | `_fetch_with_playwright` (959–1010) | Scroll to the bottom, then call `_expand_semester_tabs(page)`. |
| `web_scraper.py` | new `_expand_semester_tabs(page)` | Clicks "Sem N" buttons, copies each table, and adds the copies to `main` in a marked `<section>`. Logs other tabs without clicking them. |
| `web_scraper.py` | `fetch_page` (1062–1179) | New reason "empty". try/except around `_extract_text` returns "unreachable". Warn when a page is cut. Update the docstring list of reasons. |
| `web_scraper.py` | `_get_image_content_hash` (1193), `_AD_IMAGE_URL` (1222), `_HOMEPAGE_URLS` (1235), `content_hash` (1238–1293) | Delete the ad image parts. `content_hash` uses text only. `with_image` stays as an unused argument, so `knowledge_base.py` and the old tests still work. |
| `web_scraper.py` | `scrape_all_pages`: results dict (1413), `time.sleep(0.8)` (1457), reason branch (1501–1515) | Add `"empty": 0` to the results. Change the sleep to 1.0. Add an `elif reason == "empty"` branch that counts it there, not as "failed" (decision 5). |
| `web_scraper.py` | module docstring (about lines 24, 29) | Remove the Advertisement.jpeg lines. |
| `pdf_ingest.py` | line 362 | One line (decision 1): `reader = web_scraper._get_ocr_reader()` instead of `getattr(web_scraper, "_OCR_READER", None)`. |
| `config.py` | line 475 area and `MAX_PAGE_CHARS` (479) | Add `USE_AUTO_CRAWLER = False` with a one-line WHY comment. Set `MAX_PAGE_CHARS = 40000`. |
| `setup_knowledge_base.py` | `step1_scrape_website`, Phase B (475–509) | Run Phase B only if `config.USE_AUTO_CRAWLER`. Otherwise print "Phase B off" and use the Phase A counts. Fix the docstring (451–452). |
| `tests/test_web_scraper_react.py` | new | All new tests (section 5). |

Do not touch: `image_passes_url_gate` (`test_page_furniture.py` uses its advertisement rule), `page_furniture.py`, `knowledge_base.py`, or any other line of `pdf_ingest.py`.

---

## 4. Steps (in order)

1. **Baseline first.** The owner runs the two old scripts and writes down the FAIL counts (expected: 2 and 3). This is for acceptance item 11.
2. **Lazy OCR (decision 1).** Change the EasyOCR block, add `_get_ocr_reader()`, update `_read_image_text`, and change the one line in `pdf_ingest.py:362`. Serves item 10.
3. **Config switches.** Add `USE_AUTO_CRAWLER = False` and set `MAX_PAGE_CHARS = 40000`. Serves item 8 and D4.
4. **Phase B switch** in `setup_knowledge_base.py`. Serves D4 and item 12.
5. **Find `main` in `_extract_text`:** take the outer `main#main-content`.
   - Remove header, nav, footer and aside only when they are outside `main` and do not contain it.
   - Remove `nav[aria-label="Breadcrumb"]` inside `main`.
   - If there is no `main#main-content`, use the old selector list, the old landmark removal and `_FOOTER_NOISE` (fallback path), and log "no main".
   - Serves items 1, 2 and 3.
6. **Remove `_NAV_NOISE`.** Move the `_FOOTER_NOISE` check so it runs only on the fallback path (decision 8), and remove "muhammad khawar abbas" from it. Serves B6.
7. **Person cards (decisions 6 and 7):** for each "Profile" button, climb to the smallest box that holds that button and exactly one `<h3>`.
   - Read the name (h3), the job (the first `<p>` after the h3), the department (the `<p>` that starts with "Department of", "Directorate", "Office" or "Centre"), the email (`mailto:`), and the badges.
   - Line: `{name} — {job}{ (Visiting) if badge}, {department}. Email: {email}.` If there is no email, write "Email not listed." If there is no department, leave that part out.
   - HOD line when a badge or the job says "HOD" or "Department Head": `{name} is the Head of the {department} at the {config.UNIVERSITY_NAME}.`
   - Skip an email already written on this page.
   - Replace the card with the lines. This also removes the initials and "Profile →".
   - Serves items 5 and 6.
8. **Filter bars (decision 2):** `_remove_filter_bars(main)`. For each button whose text matches `^All\b`, climb to the smallest box that holds at least 2 buttons, and remove every button in that box. Log the page and the button names. Do not touch other buttons, for example the FAQ questions. This runs after the person cards, so the "Profile" buttons are already gone. Serves item 7 and the new FAQ test.
9. **Label drop list (decision 3)** in the Stage 4 loop. Drop lines that are exactly one of: "Share", "Print", "Profile", "Profile →", "→", "Tap course for syllabus", "Tap course for details", "View Outline →", "Fullscreen", "Try Again", "Ask AI", "Ask AI Assistant". Also drop lines matching `^Sem \d+$` and `^Showing \d+ Courses$`. Keep the list as one module-level constant `_BUTTON_LABELS`. In table rows, leave out a cell whose text is exactly one of these labels, so the row "| Total Courses: | 6 | 18 Cr. Hrs |" keeps no "Tap course for syllabus". Serves item 7.
10. **Course area (decision 4):** `_remove_course_area(main)` works only if the marked semester `<section>` from step 13 is present.
    - Find the "Sem N" buttons outside that section. Find the first `<table>` outside that section.
    - Climb from the first Sem button to the smallest box that holds all the Sem buttons and that table, then remove it. This removes the original Sem 1 table, the buttons and the phone copy.
    - It is found by structure (buttons + table), never by class names.
    - If no section is present (the offline copies), do nothing: the "Sem N" lines are dropped by step 9.
    - Serves items 7 and 9 and the new "no repeated courses" test.
11. **Empty pages (decision 5):** add `_is_empty_page(soup)`. It works on a copy of `main` with the breadcrumb and title header taken out. In `fetch_page` it runs before `_extract_text` and returns `(None, "empty")` with a log line. In `scrape_all_pages`, count "empty" in `results["empty"]`. Serves item 4.
12. **`fetch_page` safety:** try/except around `_extract_text` returns "unreachable" and logs the error. Add a warning with the address and size when a page is cut, and update the docstring. Serves item 8 and the edge cases.
13. **Opening a page:** after `goto`, scroll to the bottom until the page height stops growing (at most 10 rounds), then call `_expand_semester_tabs(page)`. That function:
    - finds buttons in `main#main-content` whose text starts with "Sem N";
    - copies the table that is already showing (Sem 1);
    - clicks Sem 2 to Sem N in order, waiting up to 5 s for the table to change (if it does not, it logs and keeps what is there);
    - adds to the end of the outer `main` one `<section data-uoli-semesters>`. The section starts with the course area's own heading, copied from the page ("Curriculum Breakdown"), then `<h3>Semester I</h3><table>…</table>` for each semester. The semester name is taken from the button's own text ("Semester I"), or "Semester N" if it is missing;
    - logs the names of other tabs and does not click them.

    Serves item 9 and A1/A2.
14. **Delay:** change `time.sleep(0.8)` to `time.sleep(1.0)`. Serves A3.
15. **Ad image:** remove `_AD_IMAGE_URL`, `_get_image_content_hash` and `_HOMEPAGE_URLS`, and make `content_hash` text only. Fix the docstring. Serves D3.
16. **Write `tests/test_web_scraper_react.py`** (section 5).
17. **The owner runs** the tests and the old scripts (section 7). Items 11 and 12.
18. **Human check texts:** a one-off offline command writes `reports/scraper_05/vc-message.txt` and `computer-science.txt` from the copies. The owner's live command writes `bscs.txt`. Items 13 and 14.

---

## 5. Tests (`tests/test_web_scraper_react.py`)

Common setup: a fixture that sets `web_scraper.OCR_AVAILABLE = False` with `monkeypatch`, so no image is downloaded and `data/ocr_cache.json` is not touched. A helper `text_of(file, url)` runs `_extract_text` on a copy. Tests that call `fetch_page` use `monkeypatch.chdir(tmp_path)`, because `fetch_page` writes `scratch/_dryrun_web_cache.json`, and they replace `_fetch_with_playwright` with "return the copy's HTML".

The fake tabs page is an HTML string inside the test file, written to `tmp_path`. It has:
- a heading;
- 3 "Sem N" buttons whose small script swaps the table;
- a phone copy of the Sem 1 courses as loose `<div>` lines;
- one "All Departments" filter group.

Every course has a unique code (e.g. `FAKE-101`).

| Test | What it checks | Copy used |
|---|---|---|
| `test_no_site_chrome_on_any_page` | no "Home ›" breadcrumb, "UOLI AI Assistant", "Developed by" or "Skip to main content" (item 1) | all 25, from `index.csv` |
| `test_titles_are_kept` (parametrized) | the title line is in the text (item 2) | the 10 pages listed below |
| `test_event_page_keeps_aside_details` | "SESSION LEAD & FACILITATOR" and "Supervision / Partner" are in the text (item 3) | `research__innovation__events__three-days-olive-…-activit.html` |
| `test_bs_economics_is_empty` | `fetch_page` returns `(None, "empty")` (item 4) | `academics__programs__bs-economics.html` |
| `test_only_bs_economics_is_empty` | `_is_empty_page` is false on the other 24 | all 25 |
| `test_cs_people_lines` | 9 lines with "Email:", each email right; HOD line exactly once for M. Khawar Abbas and ends with `config.UNIVERSITY_NAME` (item 5, decision 7) | `academics__departments__computer-science.html` |
| `test_cs_welcome_message_kept` | the welcome message text is still there | same |
| `test_faculty_directory_90_people` | 90 person lines, each with "Department of"/"Directorate"/"Office", no initials line (item 6) | `academics__faculty.html` |
| `test_visiting_badge_in_line` | at least one line has "(Visiting)", and no line is just "Visiting" (decision 6) | `academics__faculty.html` |
| `test_faq_questions_all_kept` | all 10 question texts that end with "?" (read from the copy's buttons) are in the text, and "All Questions" is not a line (decision 2) | `admissions__faq.html` |
| `test_filter_bars_removed` | no line is exactly "All Departments", "All Faculties", "All Events (12)", "All Degree Programs" or "All"; "Allied Health Sciences" still appears in the faculty text, in the person lines (decision 2) | `academics__faculty.html`, `academics__departments.html`, `campus-life__sports__events.html`, `academics__programs.html`, `news.html` |
| `test_no_button_labels` | no line is exactly one of `_BUTTON_LABELS` or "Sem 1" (item 7, decision 3) | all 25 |
| `test_table_rows_have_no_label_cells` | no "\|" row contains "Tap course for syllabus" | `academics__programs__bscs.html` |
| `test_undergraduate_not_cut` | `fetch_page` text is over 15,000 characters and ends with the same last line as `main` (item 8) | `academics__undergraduate.html` |
| `test_semester_tabs_are_read` | `_fetch_with_playwright("file:///…")` on the fake page, then `_extract_text`, gives "Semester I/II/III" and every course code. Skipped if Chromium is missing (item 9) | fake tabs page |
| `test_fake_tabs_no_repeated_courses` | on the same output, each course code appears exactly once; no "Sem N" line; no phone-copy line (decision 4) | fake tabs page |
| `test_non_sem_tabs_not_clicked` | the "All Departments" button is not clicked (a click on it changes a marker the test checks), and a log line names it | fake tabs page |
| `test_import_does_not_load_easyocr` | a fresh `import web_scraper` in a subprocess leaves "easyocr" out of `sys.modules` (item 10) | none |
| `test_pdf_ingest_uses_ocr_getter` | with `_get_ocr_reader` replaced by a stub that returns `None`, the `pdf_ingest` OCR function returns `("", False, 0.0)` and the stub was called (decision 1) | none |
| `test_people_line_without_email_or_department` | a small HTML card gives "Email not listed." and no department part | small HTML in the test |
| `test_empty_counted_separately` | `scrape_all_pages` with `fetch_page` stubbed to return "empty" puts it in `results["empty"]`, not "failed" (decision 5) | stub, no copy. The coder must check that the ingest pass is not reached when nothing is fetched (it returns early at "Nothing was fetched"). |
| `test_footer_noise_only_on_fallback` | a line "Curriculum developed by HEC" inside `main#main-content` is kept; the same line in a page without that `main` is dropped (decision 8) | small HTML in the test |
| `test_extract_error_gives_unreachable` | if `_extract_text` raises, `fetch_page` returns "unreachable" | small HTML |
| `test_content_hash_is_text_only` | the home page hash makes no network call and has no "_" | none |

The 10 title checks come from `reports/site_structure/structure_report.md`, section 3. The copy file name is in brackets:

- `/about` (`about.html`): "Academic Heritage & Overview"
- `/about/vc-message` (`about__vc-message.html`): "Vice Chancellor's Message"
- `/services/scholarships` (`services__scholarships.html`): "Scholarships & Financial Aid"
- `/academics/programs` (`academics__programs.html`): "Program Discovery"
- `/academics/programs/bscs` (`academics__programs__bscs.html`): "BS Computer Science"
- `/academics/faculty` (`academics__faculty.html`): "Distinguished Professors, Researchers & Scholars at University of Loralai"
- `/news` (`news.html`): "News & Announcements"
- `/news/news-0` (`news__news-0.html`): "Official Announcement — Research"
- the event page (`research__innovation__events__three-days-olive-…-activit.html`): "SESSION LEAD & FACILITATOR"
- `/services/student-resources` (`services__student-resources.html`): "Student Resources & Downloads"

BS Economics, the 11th "lost" row, is checked by `test_bs_economics_is_empty`.

Existing `tests/test_study_site_structure.py` must still pass, especially `test_load_old_extractor_keeps_ocr_off_and_restores_sys_modules`.

---

## 6. How to check each acceptance item

1. `test_no_site_chrome_on_any_page` passes.
2. `test_titles_are_kept` passes for all 10.
3. `test_event_page_keeps_aside_details` passes.
4. `test_bs_economics_is_empty` and `test_empty_counted_separately` pass.
5. `test_cs_people_lines` passes.
6. `test_faculty_directory_90_people` passes.
7. `test_no_button_labels`, `test_filter_bars_removed` and `test_faq_questions_all_kept` pass.
8. `test_undergraduate_not_cut` passes.
9. `test_semester_tabs_are_read` and `test_fake_tabs_no_repeated_courses` pass. They must not be skipped: Chromium is installed on the owner's PC.
10. `test_import_does_not_load_easyocr` and `test_pdf_ingest_uses_ocr_getter` pass.
11. `python -m pytest tests/ -v` is all green. The old scripts show the same FAIL counts as the step 1 baseline.
12. `git diff --stat` and `git status` list only `web_scraper.py`, `pdf_ingest.py` (1 line), `config.py`, `setup_knowledge_base.py`, `tests/test_web_scraper_react.py` and the plan.
13. The live command prints "ok", the number of "|" course rows (about 48 courses plus 8 total rows expected), and `True` for "Semester VIII".
14. The owner opens the 3 files in `reports/scraper_05/` and reads them.

---

## 7. Commands the owner runs by hand

The scraper test commands use `pytest`. The two old scripts run with `python`, not `pytest`.

**Git (start of the coding session):**
```
git pull origin main
git checkout -b feature/05-update-scraper
```

**Before any code (baseline, step 1):**
```
python test_page_furniture.py
python test_ingest_safety.py
```

**After the code (item 11):**
```
python -m pytest tests/ -v
python test_page_furniture.py
python test_ingest_safety.py
git status
git diff --stat
```

**Live check, one page, only after the owner's OK (items 13 and 14).** It opens one page once. It also writes `scratch/_dryrun_web_cache.json`, as `fetch_page` always does.
```
python -X utf8 -c "import os, web_scraper as w; t, r = w.fetch_page('https://uoli.edu.pk/academics/programs/bscs'); os.makedirs('reports/scraper_05', exist_ok=True); open('reports/scraper_05/bscs.txt', 'w', encoding='utf-8').write(t or ''); print(r, len(t or ''), (t or '').count('\n|'), 'Semester VIII' in (t or ''))"
```

---

## 8. Remaining risks

- **The clicking code is only tested on a fake page.** The live check (item 13) is the real proof. If the site's table does not change its HTML on a click, the 5 s wait times out and logs, and only Semester I is kept. In that case, `_remove_course_area` still removes the original area, so nothing is repeated, but semesters are missing; the live check will show it.
- **The course area rule depends on the page structure.** If a future page puts the Sem buttons and the table in a box that also holds other real text, that text would be removed with it. To limit this, `_remove_course_area` logs the size of what it removes, and the live check text is read by a human (item 14).
- **The filter bar rule depends on the word "All".** A real content button that starts with the word "All" (for example "All students must…") would be removed together with its group. No such button exists on the 25 copies; spec 07 (15 live pages) is the next check.
- **Home page hash.** `content_hash` changes for the home page because the image part is removed, so the home page is embedded again once. That is cheap, and spec 08 rebuilds everything anyway.
- **Uncommitted files.** The working folder has uncommitted changes to `.claude/PROJECT_STATUS.md` and the spec, plus a new `.claude/PLANNING_PROMPT.md`. Spec flow step 1 says the spec is pushed to `main` first, so these should reach `main` before the coding session runs `git pull`.
