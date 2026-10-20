# Plan 04: Study the New Website Structure

Spec: `.claude/specs/04_study_site_structure.md`
Branch: `feature/04-study-site-structure`
Status: approved by the user.

## Context

Our scraper was written for the old WordPress site. The new React site has a new header, footer, menu, chat widget and card layout. If we scrape without knowing that layout, we store junk: menu words, footer lines, the "UOLI AI Assistant" widget text.

This task only **studies** the site. It builds one new tool that opens 25 sample pages, measures where the real content is and where the furniture is, and checks what the old `_extract_text` would store today. It also saves the pages as offline HTML files so specs 05 and 06 can be tested without the internet.

**No existing file changes.** New files only:
- `tools/study_site_structure.py`
- `tests/test_study_site_structure.py`
- `tests/fixtures/site/` (25 HTML files plus `index.csv`, written when **you** run the study)
- `reports/site_structure/` (report, CSV, screenshots; these stay on your computer)

---

## Important finding: importing `web_scraper` has side effects

The spec asks me to check this. It does have side effects:
1. **On import, EasyOCR loads its model** (`web_scraper.py:75`, `easyocr.Reader(...)`). That is slow, and the first time it can download about 100 MB.
2. **`_extract_text` runs OCR on the page's images** (`_extract_images_text`, `web_scraper.py:850`). That downloads images and **writes `data/ocr_cache.json`**, which would change an existing data file.

Python cannot import just one function without running the whole file. So, safe way:
- Before the import, I tell Python that `easyocr` is not installed: `sys.modules["easyocr"] = None`.
- **Approved addition:** right after the import, `sys.modules["easyocr"]` is put back exactly as it was (removed if it was not there before, or the old module restored), so other tests are not affected. A test checks this.
- Then `web_scraper` sets `OCR_AVAILABLE = False`, the model never loads, and `_extract_images_text` returns `""` straight away (`web_scraper.py:459`), before it touches the cache.
- The other imports (`config`, `page_furniture`, `table_reconstruct`, `ocr_quality`, `pdf_ingest`) are light. `config` only reads `.env`.
- All of this sits in one small function, `load_old_extractor()`, with a test that proves OCR stays off.

One difference from the real old scraper: it blocked images and did **not** scroll. I feed `_extract_text` the HTML after scrolling, so both tests look at exactly the same page. The report says this.

---

## What I reuse (no copy-paste)

| From | What |
|---|---|
| `tools/find_hidden_pages.py` | `ClickRenderer` (browser plus `scroll_bottom()`). My browser class is a small subclass of it. Also `DELAY_SECONDS` (2 s, above the 1 s minimum) and `CHAT_WIDGET` (regex for chat/assistant/bot). |
| `tools/find_site_urls.py` | `open_page()` (403/429: wait 10 s, retry once, then `Blocked`), `Blocked`. |
| `tools/url_health_check.py` | `normalize()`, `write_csv()`, `ROOT`, `FetchTimeout`, `with_retries` (through `open_page`). |
| `page_furniture.py` | `normalise()` (line compare key), `document_frequency()` (repeated lines), `page_has_own_content()` (the "no own content" check for `bs-economics`), `is_navigation_label()` (marks which repeated lines are labels). |
| `web_scraper.py` | `_extract_text` only, loaded as described above. |

The tool uses one browser and opens one page at a time.

---

## Steps (small, in order)

### 1. The 25 sample pages
- A fixed list in the tool: `(address, page type)`, exactly the 25 from spec table 3.
- At start I check that each one is in `url_lists/pages.csv` with `keep = yes`. If one is missing, the tool warns and still studies it.
- `--limit N` studies only the first N.

### 2. Open one page (`study_page`)
- Open it with `open_page()`, which also handles 403/429 → "blocked".
- **Wait until stable:** read the body text length every 0.5 s. Stable means the same length 3 times in a row. If it is not stable after 30 s, use what is there and mark it "slow".
- Note the text length, then `scroll_bottom()`, then wait until stable again. If the text grew, note "more content after scrolling".
- Record the load time in seconds.
- Do not click anything, including pop-ups (spec 7).
- Wait 2 s before the next page.

### 3. Measure in the browser (one JavaScript call, `MEASURE_JS`)
It returns plain data. No page text is kept, except the body text and the content-area text, which are used for comparison and stay in memory.
- **Landmarks:** `header`, `nav`, `main`, `footer`, `aside`, `[role=main]`. For each: is it there, its short CSS selector (tag plus id, or its first two classes), and its text length.
- **Content area:** first of `main`, `[role=main]`, `article`. If there is none: the "biggest content block", meaning the element with the most text that is not `body`, `#root` or the whole page. Then note "no main". Also record text inside and outside it.
- **Furniture parts** (selector, text length, a short text hash so I can tell whether it is the same on every page):
  - header, footer, menu (`nav`)
  - breadcrumb (`[aria-label*=breadcrumb]`, `.breadcrumb`, `nav ol`)
  - chat widget (id/class/aria matching `CHAT_WIDGET`, usually `position: fixed`). Also checks whether it lives in an **iframe** or **shadow DOM**. If so, the report notes that normal text reading cannot see it.
  - pop-ups (`[role=dialog]`, `.modal`, large fixed boxes)
  - back-to-top (a fixed button with "top" in its text or aria-label)
  - cookie bar (text contains "cookie")
  - "Skip to main content" link
- **Special content:**
  - tables: how many, plus the first row that has data
  - cards: elements whose class contains `card`, especially faculty or HOD cards. Count, class name, and one example text (cut to 200 chars).
  - hidden text: `details`, `[aria-expanded=false]`, `[role=tabpanel]`, `[hidden]`, and `display:none` boxes inside the content area. For each I compare `textContent` (what the HTML holds) with `innerText` (what is visible). If hidden text is in the HTML while the box is closed, the answer is "yes, in the page when closed". Also tab and filter buttons such as "All Departments".
  - counters: lines that are just a number ("100+"). Is the label on the line **before** or **after** it?
  - carousel: classes like `swiper`, `slick`, `carousel`, `slider`, plus the same text block repeated inside one page (slide copies)
  - images with text: `img` inside the content area with long alt text, or a filename like notice/poster/fee/advert. I count them and give one example src. **No OCR.**

### 4. Old scraper test (pure function `compare_old`)
- Get the HTML of the loaded page and run `_extract_text(BeautifulSoup(html), url)` on it. If it crashes, record the error and keep going.
- Compare its lines with the content-area text, using `page_furniture.normalise()`:
  - **garbage %** = characters of old-output lines that do not appear in the content area ÷ all old-output characters
  - **lost content** = content-area lines (15 chars or longer) that are not found anywhere in the old output → yes/no, a count, and up to 3 examples
  - up to 5 example garbage lines
- Lenient on purpose: the old code rewrites tables (`| a | b |`) and faculty cards (`Name — Title`), so I compare without the `| • #` markers. Words that only changed format should not count as lost.

### 5. Screenshot with boxes
- Add see-through overlay boxes at each element's position: **green** for the content area, **red** for each furniture part. Then take a full-page screenshot and remove the boxes.
- Saved to `reports/site_structure/screens/<name>.png`.

### 6. Offline copy (pure functions `clean_html`, `shrink_to_limit`)
- Clean the loaded HTML: remove `<script>`, `<style>`, stylesheet `<link>`, `<svg>` and `<noscript>`. Replace each base64 `data:` image src with `data:removed`.
- I **keep** inline `style="..."` attributes and `<iframe>` tags. They show which boxes are hidden and where the widget is, and specs 05 and 06 need that.
- Each file must be **at most 500 KB**. If it is bigger, find the largest group of repeated sibling blocks with the same text, keep the first one and remove the copies, then note it. If it is still too big, note "still over" in the report. Nothing is cut silently.
- File name: the path with `/` changed to `__`, cut to 80 chars, `home.html` for `/`.
- `tests/fixtures/site/index.csv` columns: `address, file, page type`.
- At the end I check the whole folder is **at most 6 MB** and warn if not.

### 7. Repeated lines (after all pages)
- `page_furniture.document_frequency()` over each page's **full body text**.
- Lines on **at least half** of the studied pages, with counts, become the furniture list. Each is marked "label" or "contact", using `is_navigation_label`.
- A check for the 5 known lines: "Skip to main content", the header phone and email, "Online Admissions", "UOLI AI Assistant", "Developed by". Each one gets found / not found.
- "No own content" per page: `page_has_own_content(content text, freq)`. `bs-economics` should come out as "no own content", which is a valid result.

### 8. Reports
- `reports/site_structure/structure_pages.csv`, one row per page: address, type, status (ok / slow / blocked / error), load seconds, grew after scroll, header/nav/main/footer/aside yes/no, content selector, text in / out of content, old output chars, garbage %, lost content yes/no, lost count, tables, cards, hidden text, counters, carousel, text images, widget kind (dom / iframe / shadow / none), own content yes/no, fixture KB, note.
- `reports/site_structure/structure_report.md`, short and in plain words:
  1. Best content selector and how many pages agree ("24 of 25"). This is the selector found on the most pages with real text.
  2. Furniture: a selector table (header, footer, menu, breadcrumb, widget, pop-up, back-to-top, cookie, skip link) with "same on all pages" yes/no, then the repeated-lines list with counts, then the 5 known lines check.
  3. Old scraper test table: page, garbage %, lost yes/no, plus example lines.
  4. Special content: tables, cards, hidden text, counters, carousel, images with text. Each has a real example or "not found on any page".
  5. Problems and surprises: slow, blocked, errors, no main, iframe/shadow widget, fixtures cut, size warnings, missing from pages.csv.
- **Ctrl+C:** stop, then write reports and fixtures for the pages done so far, the same way the spec 02 tool does.
- A `--limit` run writes `structure_report_limit3.md` etc., so it never overwrites the full run. Fixtures are written only on a full run, so a test run cannot leave a half-filled fixtures folder.

### 9. Tests (`tests/test_study_site_structure.py`, offline, no browser, no internet)
- Sample list: 25 unique addresses, all in `pages.csv`.
- `fixture_name`: `/` → `home.html`, long slug cut, `/` → `__`.
- `clean_html`: removes script/style/svg/base64 and keeps the visible text and inline `style`.
- `shrink_to_limit`: an HTML file with a big repeated block shrinks below the limit and returns a note.
- `compare_old`: a made-up case with menu garbage gives the right %, a made-up missing line gives "lost = yes", and table/card format changes do not count as lost.
- `repeated_lines`: the half-of-pages cut works.
- Counter order: label-before vs label-after.
- Best selector: picks the majority and writes "N of M".
- `load_old_extractor()`: after loading, `web_scraper.OCR_AVAILABLE` is `False`, and `data/ocr_cache.json` is not touched.
- Report builder: given made-up rows, all 5 sections are present.
- **Fixture tests:** index has 25 rows, every file exists and is at most 500 KB, the total is at most 6 MB, there is no `<script>` and no base64, and the old `_extract_text` runs on every fixture without crashing. These are **skipped until you run the full study**, because the fixtures do not exist yet. After your run I tell you to re-run them.

---

## Verification (who runs what)

1. **I run** (offline only): `python -m pytest tests/test_study_site_structure.py -v`, and also the existing `tests/` files so I know nothing else broke.
2. **After your OK**, live test: `python -X utf8 tools/study_site_structure.py --limit 3`. It opens 3 pages, takes about 30 s, and changes nothing outside `reports/`. We read the result together.
3. **You run** the full study: `python -X utf8 tools/study_site_structure.py`. It takes about 3 to 6 minutes for 25 pages, 2 s apart, with one browser.
4. Re-run the tests. The fixture tests now run as well.
5. **Your eye check (acceptance 11):** open `screens/` for home, fees and computer-science. Green should cover only the real content. Red should cover the header, footer and widget.
6. `git status` should show only new files.

## Git (at the end, after your OK)
- Commit **by name**: `tools/study_site_structure.py`, `tests/test_study_site_structure.py`, `tests/fixtures/site/`, `.claude/plans/04_study_site_structure.md`. No `git add .`
- Note: `.gitignore` hides `reports/**/*.csv` and `*.md`, but **not** the `.png` screenshots. Since we commit by name they stay local anyway. Push, PR and merge are your steps.
