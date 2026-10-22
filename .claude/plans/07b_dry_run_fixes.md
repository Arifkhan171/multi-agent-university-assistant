# Plan 07b — Fixes from the Dry Run

Spec: `.claude/specs/07b_dry_run_fixes.md`

## 1. Goal

Before the full rebuild: every FAQ answer is stored with its question, no "Image Unavailable" or navigation words are stored, news pages hold only their own facts, every office staff member is one correct line, and every PDF page is read.

## 2. Files to change

| File | Function / name (line) | What changes |
|---|---|---|
| `web_scraper.py` | `_fetch_with_playwright` (1263, call at 1310) | Also call a new `_expand_faq_answers(page)` right after `_expand_semester_tabs(page)`. |
| `web_scraper.py` | new `_FAQ_JS`, `_ADD_FAQ_JS`, `_expand_faq_answers` (next to `_expand_semester_tabs`, 1209) | Open each "?" question in main, copy its answer once it stops changing, and add all pairs in one marked `<section data-uoli-faq>`. |
| `web_scraper.py` | new `_remove_faq_originals` (next to `_remove_course_area`, 848) | When the marked FAQ section is there, delete the original question boxes, so no question is stored twice. |
| `web_scraper.py` | new `_remove_placeholders`; `_card_badges` (674) | Delete any element or line whose whole text is "Image Unavailable", and never use it as a badge. |
| `web_scraper.py` | `_LINK_LEFTOVER_STARTS` (867), used by `_remove_link_leftovers` (877) | Add "return to" and "explore all". |
| `web_scraper.py` | new `_remove_related_block` | On `/news/` and `/research/innovation/events/` pages, delete the box headed exactly "Related Official Notices". |
| `web_scraper.py` | new `_find_staff_cards` + staff-card step; reuse the line format of `_person_lines` (692) and the flow of `_extract_people` (742) | Office staff cards without a "Profile" button become one person line each. `/offices/` pages only. |
| `web_scraper.py` | `_extract_text` (1018, stage 2b at 1054) | Call the new steps in the right order (step 10). |
| `pdf_ingest.py` | `MAX_OCR_PAGES` (104) | 90 → 150. Add one line to the comment above it saying why 150. |
| `tools/dry_run_scrape.py` | `main` (716), `run` (347, already takes `selection`) | New `--pages-from <file>` option: read the page and PDF addresses from a saved first-run report and pass them to `run(selection=...)`. |
| `tools/dry_run_scrape.py` | `build_rows` (509), `count_people` (415) | Add a `faq_pairs` count and column. Also count person lines on office pages (today only department and faculty pages, line 528). |
| `tests/fixtures/site/` + `index.csv` | 4 new files, 4 new rows | BBA, Commerce, IT office, the news page. |
| `tests/test_web_scraper_react.py`, `tests/test_cleaning_and_links.py`, `tests/test_dry_run_scrape.py` | new tests | See part 4. |

## 3. Steps (in this order)

1. **Fixture helper (coder writes it, owner runs it).** Items 2–5. A one-time script `scratch/save_07b_fixtures.py`, not committed:
   - Reads the 4 addresses from `url_lists/pages.csv` by their path. No address is typed.
   - Opens each page the way the scraper does: same browser settings, images, media and fonts blocked (so "Image Unavailable" is in the copy). Scrolls to the bottom with `web_scraper._scroll_to_bottom`. Makes **no** clicks.
   - One page at a time, at least 1 second apart.
   - Cleans each copy with `tools/study_site_structure.clean_html` and `shrink_to_limit` (500 KB). Saves the file under `fixture_name(address)`.
   - **Adds** 4 rows to `tests/fixtures/site/index.csv`. Existing rows are never rewritten. A row that is already there is not added again.
2. **Read the new fixtures before coding E.** For the IT page, write down how one staff card looks (which tag holds the name, the job, the email), and what the top "Officer In-Charge" box shows. Item 5.
3. **PDF limit.** `MAX_OCR_PAGES = 150`. Item 6.
4. **Placeholder.** New `_remove_placeholders(main)`: delete the smallest element whose whole text is "Image Unavailable". In `_card_badges`, skip that text too. In the line loop of `_extract_text`, drop a line that is exactly "Image Unavailable". A longer sentence that contains the words is kept. Item 2.
5. **Navigation words.** Add `"return to"` and `"explore all"` to `_LINK_LEFTOVER_STARTS`. The 40-character limit and the "whole link or button" test stay the same. Item 3.
6. **Related block.** New `_remove_related_block(main, base_url)`, only for paths under `/news/` (not `/news` itself) and under `/research/innovation/events/`. Find a heading whose text is exactly "Related Official Notices". Go up to the smallest box that holds the heading and more than the heading, and delete it. If there is no such heading, nothing changes. Item 4.
7. **FAQ answers (browser side).** New `_expand_faq_answers(page)`, built like `_expand_semester_tabs`:
   - Questions = buttons in `main#main-content` whose text ends with "?". Category buttons ("General", "Fees" …) do not end with "?", so they are never clicked. The search box is never touched.
   - Only the first 40 questions; the rest are logged.
   - For each question, find its box: the smallest box around the button that holds more than the button.
   - **Answer already open** at page load (the first FAQ is): copy it **without a click**.
   - Otherwise click (timeout 5 s), then read the box every 200 ms until two reads in a row are equal, at most 3 s.
   - Copy only the answer text. Leave out the labels "Category: …" and "Official UOLI Policy".
   - If the click changed the page address (a link): go back, log it, keep the question only.
   - Click failed or no answer showed: log a warning, keep the question only, go to the next question.
   - Add one `<section data-uoli-faq>` to the end of main, with `<h3>question</h3><p>answer</p>` per pair.
   - The whole function sits inside try/except, like the semester tabs. Items 1 and 8.
8. **FAQ originals (text side).** New `_remove_faq_originals(main, base_url)`: if the marked section is there, delete the box of each original question that also appears in the section. Questions without an answer stay where they are. Item 1.
9. **Staff cards.** Item 5.
   - Only on pages under `/offices/`.
   - Runs after `_extract_people`, so cards with a "Profile" button are already done.
   - A card = the smallest box inside main with exactly one heading, plus an email link, a phone link, or a name that starts with Mr., Ms., Dr., Prof. or Engr.
   - Each card → one line in the spec 05 format: `Name — Job, Office. Email: … .` or `Email not listed.`
   - The office comes from the card when it is there, otherwise from the page's main title (`h1`).
   - A card that shows a job but no name: take the name from the top box **only if the top box shows the same job**. Otherwise write "Name not listed". Never take a name from a nearby card.
   - Uses the same `seen_emails` set as `_person_lines`, so one email gives one line. Two emails in one card: use the first, log it.
10. **Order in `_extract_text`.** After `_remove_breadcrumb`: `_remove_placeholders` → `_remove_related_block` → `_extract_people` → staff-card step → `_remove_faq_originals` → `_remove_course_area` → `_remove_filter_bars` → `_remove_link_leftovers` → the rest stays as it is. Placeholders go first so they can never become a badge. Items 1–5.
11. **Dry-run tool.** Item 9.
    - `--pages-from <file>`: the file is a copy of a first-run `report.md`. Read the `## Selection` lines: `- https://…` lines are pages, `- PDF: https://…` lines are PDFs. Keep their order. Pass `selection=(pages, notes, pdfs)` to `run()`. An empty list or a missing file stops with a clear error before any fetch.
    - Add `count_faq_pairs(text)` and a `faq_pairs` column. Count person lines on office pages too.
    - Nothing else in the tool changes.
12. **Tests** (part 4), then `python -m pytest tests/ -q`. Item 7. List every old test line that changed, for review.
13. **Live checks by the owner** (part 6): command A (item 8), then save the first report and run command B (item 9). Then the human review (item 10).

## 4. Tests

| Test | Checks | Fixture |
|---|---|---|
| `test_fake_faq_three_answers` | 3 hidden answers → 3 question/answer pairs, each question once in the text | fake page `FAKE_FAQ` (Chromium, through `open_fake`) |
| `test_fake_faq_open_answer_copied_without_click` | the answer open at load is kept, and its button got no click | `FAKE_FAQ` |
| `test_fake_faq_labels_dropped` | no "Category: …" or "Official UOLI Policy" line | `FAKE_FAQ` |
| `test_fake_faq_category_buttons_not_clicked` | "General", "Fees" buttons leave no `data-clicked` mark | `FAKE_FAQ` |
| `test_fake_faq_search_box_unused` | the search box value stays empty | `FAKE_FAQ` |
| `test_fake_faq_missing_answer_kept_as_question` | a question that shows no answer stays once, as a question | `FAKE_FAQ` |
| `test_faq_questions_all_kept` (existing, line 144) | still passes: offline there is no marked section, so the 10 questions stay | `admissions__faq.html` |
| `test_bba_faq_questions_kept_offline` | 8 questions present, once each | `academics__programs__bba.html` |
| `test_no_image_unavailable` | no line "Image Unavailable"; no line contains "Status: Image Unavailable" | `academics__departments__commerce.html` (and all fixtures) |
| `test_image_unavailable_in_sentence_kept` | a sentence with those words inside is kept | small HTML string |
| `test_return_explore_removed` | no line "Return to All Academic Departments" or "Explore All Campus Facilities" | Commerce and CS department fixtures, small HTML for campus |
| `LEFTOVERS` in `test_cleaning_and_links.py` (line 40) | add the two new phrases | existing parametrised list |
| `test_news_related_block_removed` | own title and date kept; "Related Official Notices" and the other articles' titles gone | `news__admissions-open-fall-2026-phase-ii-university-of-loralai.html` |
| `test_related_block_without_heading_kept` | a box without the exact heading is unchanged | small HTML string |
| `test_news_list_page_unchanged` | `/news` text is the same as before | `news.html` |
| `test_it_office_staff_lines` | one line per staff member; name, job and email belong together (exact lines written after step 2) | `offices__directorate-of-it.html` |
| `test_staff_card_name_from_top_box_same_job` | the name is taken only when the top box shows the same job, else "Name not listed" | small HTML strings (both cases) |
| `test_staff_cards_only_on_offices` | the same card on a non-`/offices/` address is not turned into a person line | small HTML string |
| `test_staff_card_two_emails_uses_first` | first email used, warning logged | small HTML string |
| `test_staff_same_email_once` | the same email on one page → one line | small HTML string |
| `test_profile_cards_unchanged` | CS department (9) and `/academics/faculty` (90) person lines are as before | existing fixtures |
| `test_max_ocr_pages_150` | `pdf_ingest.MAX_OCR_PAGES == 150` | none |
| `test_pages_from_reads_selection` (in `test_dry_run_scrape.py`) | pages and PDFs read in order from a small report file | temp file |
| `test_pages_from_missing_or_empty_stops` | clear error, no fetch | temp file |
| `test_count_faq_pairs`, `test_office_people_counted` | the new report counts | small text strings |

## 5. How to check each acceptance item

- item 1: `test_fake_faq_three_answers`, `test_fake_faq_category_buttons_not_clicked`
- item 2: `test_no_image_unavailable`
- item 3: `test_return_explore_removed` and the `LEFTOVERS` update
- item 4: `test_news_related_block_removed`
- item 5: `test_it_office_staff_lines`
- item 6: `test_max_ocr_pages_150`
- item 7: `python -m pytest tests/ -q` (coder runs it, offline)
- item 8: live check, owner runs command A (part 6)
- item 9: live check, owner runs commands B1 and B2 (part 6); the new report's Selection must equal the first one, then compare the results
- item 10: human review of the `/admissions/faq`, BBA, Commerce and IT office texts from commands A and B2

## 6. Commands the owner runs by hand

Save the 4 fixtures (step 1, live, after your OK):
```
python -X utf8 scratch/save_07b_fixtures.py
```

A. FAQ page live check (item 8). Reads the address from `url_lists/pages.csv`, OCR off, writes only to `scratch/`:
```
python -X utf8 -c "import csv,web_scraper as w; from bs4 import BeautifulSoup as B; w.OCR_AVAILABLE=False; u=next(r['address'] for r in csv.DictReader(open('url_lists/pages.csv',encoding='utf-8-sig')) if r['address'].endswith('/admissions/faq')); h=w._fetch_with_playwright(u); open('scratch/faq_live.txt','w',encoding='utf-8').write(w._extract_text(B(h,'html.parser'),u))"
```

B1. Save the first run's selection BEFORE the second run (the second run replaces `reports/dry_run_07/`):
```
cp reports/dry_run_07/report.md reports/dry_run_07_first_report.md
```

B2. Second dry run on the same pages (item 9):
```
python -X utf8 tools/dry_run_scrape.py --pages-from reports/dry_run_07_first_report.md
```

Offline tests:
```
python -m pytest tests/ -q
```

Old test scripts (known baseline: 2 FAIL and 3 FAIL):
```
python test_page_furniture.py
python test_ingest_safety.py
```

## 7. Risks

- The IT staff-card rule is written after the fixture is saved (step 2). If the live cards look different from the first run's text, the expected lines in `test_it_office_staff_lines` are set from the fixture, and the supervisor reviews them.
- Changing the order in `_extract_text` could change the text of existing fixtures. Every changed test line is listed for review (spec section 6).
- No saved page shows "Explore All Campus Facilities". The campus part of item 3 is checked by a small HTML test and by the live dry run only.
- FAQ clicks can add up to 40 × 3 s per page in the worst case. Normal answers settle in under 1 s.
- The first-run selection holds 16 pages and 3 PDFs. If the owner forgets step B1, the first report is lost and item 9 cannot compare. B1 must run first.
