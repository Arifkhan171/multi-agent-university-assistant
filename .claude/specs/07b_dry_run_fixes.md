# 07b — Fixes from the Dry Run

## 1. Problem Statement

The spec 07 dry run read 16 live pages and 3 PDFs with the new scraper. It found problems that would reach the chatbot's answers if the full rebuild (spec 08) ran today.

**A. FAQ answers are missing.** FAQ answers are hidden until their question is clicked, so only the questions are stored.
- On the saved copy of `/admissions/faq`, only the first of 10 answers is in the page ("How do I apply…"). The other nine are missing, including "Can Pre-Medical students apply for BSCS?", "Where can I deposit the application processing fee…?" and "Is hostel accommodation available…?".
- The live BBA program page keeps its 8 FAQ questions but none of the answers. Other program pages probably have the same layout.

**B. "Image Unavailable" is stored as text.** The scraper blocks images while loading a page, for speed. The site then shows the placeholder "Image Unavailable" where a photo would be. It appears on 9 of 15 live pages (8 times on the Commerce department page). On the Allied Health Sciences page it became a false fact: "Status: Image Unavailable" on the head-of-department line.

**C. Navigation words remain.** "Return to All Academic Departments" (department pages) and "Explore All Campus Facilities" (campus pages) are not caught by the spec 06 rule, which only knows "Back to", "View All" and similar.

**D. News pages repeat other articles.** Each news article ends with a "Related Official Notices" block that lists other articles' titles and dates. The same lines are stored on many news pages, and a date can be read as belonging to the wrong article. Every article is already scraped on its own page.

**E. Office staff cards may mix up people.** On `/offices/directorate-of-it` the staff cards have no "Profile" button, so the spec 05 person-card rule does not apply. The text comes out with the role before the name, and the first person's name appears only at the top of the page. A name can be paired with the wrong role or email, which is the error the person-card rule exists to prevent.

**F. Two scanned pages of a PDF are not read.** `pdf_ingest.MAX_OCR_PAGES` is 90. The HEC Anti-Harassment policy has more scanned pages than that, so its last two are skipped.

## 2. Goal

Before the full rebuild, every FAQ answer is stored with its question, no placeholder or navigation text is stored, news articles hold only their own facts, every staff member is one correct line, and every page of every PDF is read.

## 3. Scope

In scope: items A–F, offline fixtures for the affected page types, and a second dry run.

Out of scope, accepted as they are (decided in the dry-run review):
- Website-side problems: the IT page shows the placeholder `[EMAIL_ADDRESS]`, the registrar form buttons link to `#` (no file behind them), BS Economics and B.Ed Honors have no content yet, the spelling "Qarshi/Qureshi" varies. These are reported to the university's web team. The chatbot stores only what the site really shows.
- Fee Challan and DIMS use `http://` addresses with an IP number. They are the university's real systems and are kept.
- Numbered lists where the number sits on its own line, counters and repeated real labels, slow pages, and the double full stop in the motto.

## 4. Functional Requirements

### 4.1 FAQ answers (A)
1. While a page is open, the scraper finds the FAQ questions inside `main#main-content`: buttons whose text ends with "?".
2. It clicks each question in order, waits until the answer has stopped changing (two equal reads in a row, as for semester tables, at most 3 seconds), and copies the answer.
3. The copies are added to the page in a marked section, one pair per question: the question as a heading, then its answer. The original question list is then removed, so no question is stored twice.
4. A question whose answer does not appear is logged and kept as a question only.
5. An answer that is already open when the page loads is copied without a click (a click would close it).
6. The labels inside an answer box, "Category: …" and "Official UOLI Policy", are not copied.
7. Category filter buttons on the FAQ page ("General", "Fees" …) are not questions and are not clicked.
8. The page's search box is never used.

### 4.2 Placeholder text (B)
1. The exact text "Image Unavailable" is removed wherever it appears as a whole line or a whole element.
2. It is never used as a person's badge or status.
3. Image blocking stays as it is.

### 4.3 Navigation words (C)
The spec 06 rule (a whole link or button, 40 characters or fewer, inside `main`) also covers text that starts with "Return to" or "Explore All".

### 4.4 Related articles block (D)
On pages under `/news/` and `/research/innovation/events/`, the block headed "Related Official Notices" (the heading and everything in its box) is removed. The block is found by its heading text, not by class names. The `/news` list page itself is unchanged.

### 4.5 Office staff cards (E)
1. On pages under `/offices/` only, a staff card is a small box inside `main` that holds one heading and an email, a phone number, or a name with a title ("Mr.", "Ms.", "Dr.", "Prof.", "Engr."), even without a "Profile" button.
   - A card that shows a job but no name takes the name from the page's top box only when the top box shows the same job. Otherwise the line says "Name not listed". A name is never guessed from a nearby card.
2. Each staff card becomes one person line in the spec 05 format: `Name — Job, Office. Email: … .` or `Email not listed.`
3. The office name comes from the card when it is there, otherwise from the page's main title.
4. A page must not produce two person lines for the same email (spec 05 rule).

### 4.6 PDF page limit (F)
`pdf_ingest.MAX_OCR_PAGES` is raised to 150, so every page of the current PDFs is read. The existing warning stays for any larger file.

### 4.7 New fixtures
Saved copies of these live pages are added to `tests/fixtures/site/`, cleaned the same way as spec 04 (scripts, styles, SVG and embedded images removed, each file at most 500 KB) and listed in `index.csv`:
- `/academics/programs/bba` (FAQ on a program page)
- `/academics/departments/commerce` (placeholder text)
- `/offices/directorate-of-it` (staff cards without Profile)
- `/news/admissions-open-fall-2026-phase-ii-university-of-loralai` (related articles block)

The pages are saved before any click. Click behaviour (4.1) is tested with a small fake page, as the semester tabs were.

They are saved by a one-time helper script in `scratch/` (not committed). It loads each page the way the scraper does, with images blocked, so the placeholder text is in the copy. It adds rows to `index.csv` and never rewrites existing rows.

## 5. API Contract

No new command or public function. Changes stay inside the current page flow:
- `_fetch_with_playwright`: also opens FAQ answers (4.1), after the semester tabs.
- `_extract_text`: removes the placeholder (4.2), the extra navigation words (4.3) and the related block (4.4), and writes office staff lines (4.5).
- `pdf_ingest.MAX_OCR_PAGES = 150`.

The dry-run command from spec 07 is reused unchanged for the check:
```
python -X utf8 tools/dry_run_scrape.py
```

## 6. Constraints

- Files that may change: `web_scraper.py`, `pdf_ingest.py` (the limit only), `tools/dry_run_scrape.py` (only to report FAQ answers and person lines, and to add `--pages-from`), tests in `tests/`, and the four new fixtures plus `tests/fixtures/site/index.csv`.
- No class names in rules. Elements are found by structure and text, because the site's class names change with each build.
- The four fixtures are fetched live, one page at a time, at least 1 second apart, only after the owner's OK. All other tests are offline.
- Existing tests keep passing. Where a fixture line legitimately changes (an FAQ answer added, a placeholder removed), the test is updated, and the change is listed for review.
- No API calls, no database, no change in `data/`.

## 7. Edge Cases and Error Handling

| Case | Behaviour |
|---|---|
| Clicking a question closes the previous answer | Copy each answer right after its own click, so it does not matter. |
| A question opens a link instead of an answer | Do not follow it. Log it and keep the question. |
| More than 40 questions on a page | Click the first 40, log the rest. |
| A click fails | Log a warning and continue with the next question (as for semester tabs). |
| "Image Unavailable" is part of a longer sentence | Keep the sentence. Only a whole line or element is removed. |
| A staff card has two emails | Use the first, and log it. |
| An office page lists the same person in two places | One line, by email (spec 05 rule). |
| A related block has no heading | Leave the page unchanged. Only a block with the exact heading is removed. |

## 8. Acceptance Criteria

Offline:
1. A fake FAQ page with 3 hidden answers gives 3 question-and-answer pairs, each question once. Category buttons are not clicked.
2. The `commerce` fixture text has no "Image Unavailable", and no person line contains "Status: Image Unavailable".
3. No line in the department or campus fixtures is "Return to All Academic Departments" or "Explore All Campus Facilities".
4. The news fixture keeps its own title and date and has no "Related Official Notices" block.
5. The `directorate-of-it` fixture gives one person line per staff member, each with the name, job and email that belong together.
6. `pdf_ingest.MAX_OCR_PAGES` is 150.
7. `python -m pytest tests/ -q` passes.

Live (after the owner's OK):
8. `/admissions/faq` stores all 10 answers, each under its question.
9. The second full dry run reads the same 16 pages as the first. `tools/dry_run_scrape.py` gets a `--pages-from <file>` option, which takes the page addresses from the first run's saved selection (copied, never typed). The first run's selection is saved before its report folder is replaced. The run shows: FAQ answers on the BBA page, no "Image Unavailable" anywhere, no "Return to" or "Explore All" lines, no related block on news pages, correct staff lines on the IT office page, and no new problem compared with the first run.

Human review:
10. A reviewer reads the `/admissions/faq`, BBA, Commerce and IT office texts from the second run and confirms each point above.
