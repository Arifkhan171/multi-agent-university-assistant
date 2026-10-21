# Plan 06 — Cleaning Rules, Site-Wide Information and Links

Spec: `.claude/specs/06_cleaning_and_links.md`
Saved by the planning session on 21 Oct 2026. Updated with the supervisor's answers (Q1–Q11) the same day.

Starting point (21 Oct): `python -m pytest tests/` → **256 passed**.

---

## 1. Goal

Every fact a student may need, including links, is stored in the knowledge base exactly once, and stays current with the website.

---

## 2. Files to change

| File | Function / name (line) | What changes |
|---|---|---|
| `web_scraper.py` | `_extract_text` (931) | Removes leftover link/button words; writes outside and document links as `words (address)`; adds mailto/tel values; keeps a line that is only a kept link address (the bare-URL filter at 1032 drops it today). |
| `web_scraper.py` | new `_remove_link_leftovers(main)` | Rule 4.6: removes a whole `a`/`button` whose words are ≤ 40 chars and start with "Back to", "Read Article", … |
| `web_scraper.py` | new `_inline_link_addresses(main, base_url)` | Rule 4.1: replaces each kept link with `words (address)`. |
| `web_scraper.py` | new `extract_site_info(soup, base_url) -> dict` | Reads the header, nav and footer outside `main` and sorts them into social / portals / contact / pdfs / other / pages. |
| `web_scraper.py` | new `build_site_note(info) -> str` | One fact per line, title from `config.UNIVERSITY_NAME`. |
| `web_scraper.py` | new `build_page_directory(pages, category) -> str` | `title — address` per page of that category. |
| `web_scraper.py` | `fetch_page` (1280), next to `_note_pdf_links` (1349) | Before the empty check and before `_extract_text`, calls `extract_site_info` and stores it in a new module dict `_SITE_INFO[url]` (same idea as `_PDF_REFERRERS`, 1243). New `reset_site_info()`. |
| `web_scraper.py` | `scrape_all_pages` (1532) | Calls `reset_site_info()`; after pass 2: compares each page's header/footer with the home page (4.3), stores the site note (6 copies), builds page directories on full runs only, saves/loads the note cache, adds result keys `"notes"` and `"new_links"`. |
| `web_scraper.py` | new `SITE_NOTE_CACHE = "./data/site_note.json"` next to `FURNITURE_CACHE` (1475) | Last good home header/footer info, reused when a run has no home page. |
| `page_furniture.py` | `find_furniture` (193), new `FURNITURE_THRESHOLD = 0.60` near `DEFAULT_THRESHOLD` (105) | Default threshold becomes 0.60; logs every removed line once with its page count. `DEFAULT_THRESHOLD` stays 0.25 for `find_site_contact_lines` (326), which must not change. |
| `knowledge_base.py` | `_extract_department` (473) | Department only from `/academics/departments/<name>`; policy words still give `all`; everything else keeps the default `general`. |
| `knowledge_base.py` | `_detect_content_type` (519) | New first check: note addresses → `site_info` / `page_directory`. Then adds `/about/…-message`, `/about/leadership` → leadership; `/research/oric` → office; `/academics/departments/…` → faculty_page. |
| `knowledge_base.py` | `_DEPT_SLUGS` (2944–2959) | Deleted (no code uses it; checked with grep). |
| `config.py` | new block after `_url_path` (586), before `_assert_category_declarations` (789) | Note settings: home address taken from `UNIVERSITY_PAGES`, and the two note address maps `#site-info-<category>` and `#pages-<category>` built from `CATEGORIES`. `_assert_category_declarations` also checks the note categories. |
| `setup_knowledge_base.py` | `step1_scrape_website`, the Phase A print (472) and Phase B print (507) | Also prints the `notes` count and the `new_links` list. Nothing else. |
| `test_ingest_safety.py` | section 7, lines 323–528 | Adds a contact page to the fake site; the 3 stale checks (453, 455, 507) now check "kept on contact page, not on ordinary pages"; redirects the contact-lines and site-note caches to test files. |
| `test_page_furniture.py` | section 9, lines 402–504 | Old addresses replaced with addresses copied by a tool from `url_lists/pages.csv`; checks that read old `data/` are marked as expected failures. |
| `tests/test_web_scraper_react.py` | only checks whose line now carries a link address | Update the expected line text. No other change. |
| `.claude/CLAUDE.md` | section 5, the 2 test commands | `python -m pytest test_page_furniture.py -v` → `python test_page_furniture.py`, same for `test_ingest_safety.py`. |
| `tests/test_cleaning_and_links.py` | new | All new offline tests (section 4 below). |

---

## 3. Steps (in this order)

1. **Config note settings** (items 5, 14). In `config.py`, after `_url_path`, add the home address (the entry of `UNIVERSITY_PAGES` whose path is `/`) and two maps `{category: home + "/#site-info-" + category}` and `{category: home + "/#pages-" + category}` for every category in `CATEGORIES`. No address typed by hand. Add a line to `_assert_category_declarations` that every note category is in `CATEGORIES`.
2. **Tags** (items 11, 12). Rewrite `_extract_department` and `_detect_content_type` as in spec 4.7. Match on the address path (`config._url_path`), so `/news/...education...` is not matched. Check the two note address shapes **first** and return `site_info` or `page_directory` (the `#` part is read before `_url_path` drops it). "No department" stays `"general"`. Delete `_DEPT_SLUGS`.
3. **Safe furniture cleaner** (item 9). In `page_furniture.py` add `FURNITURE_THRESHOLD = 0.60`, make it the default of `find_furniture`, keep the 40-char rule, and log each removed line once with its page count. Do not touch `find_site_contact_lines` or `strip_furniture`.
4. **Leftover link words** (item 10). Add `_remove_link_leftovers(main)`. For each `a` and `button` inside `main`: if its whole text is ≤ 40 chars and starts with one of the 8 phrases, remove the element. Call it in `_extract_text` right after `_remove_filter_bars` (973).
5. **Link addresses in page text** (items 1, 2). Add `_inline_link_addresses(main, base_url)`. Rules 4.1.1–4.1.5: outside links and document links become `words (address)`; relative `/Docs/...` addresses are made full with `urljoin(base_url, href)`; mailto/tel values are added only when the words do not already show them (compare digits only for phone numbers); same words and address → address once; no `href` → skip; links to other UOLI pages → words only. Call it after step 4 and **before** the table step (994), so a link in a table cell keeps its address. Person cards are already replaced at 967, so their emails are not touched twice.
6. **Keep address-only lines** (item 1). The filter at 1032 drops any line that is a bare `http...` address. Change it so a line that is exactly a kept link address (step 5 records them in a set) is kept. Addresses longer than 200 chars are kept and logged.
7. **Read the header and footer** (items 3, 4). Add `extract_site_info(soup, base_url)`. It works on each `header`/`nav`/`footer` outside `main#main-content`:
   - social: links to facebook / instagram / linkedin / youtube / twitter / x; name from the domain when there are no words; skip share links that contain the page's own address;
   - pdfs: links ending in `.pdf/.doc/.docx/.xls/.xlsx` (made full with `urljoin`);
   - portals: other outside links (not social, not the "Developed by" link);
   - contact: tel and mailto values, the address line, the website line (use `page_furniture.looks_like_contact_fact` and `_CONTACT_SIGNAL_RE`);
   - pages: UOLI page links (made full);
   - other: remaining text lines, such as the motto, plus the words of any link to the home page `/` (the footer logo with "Chartered 2012").
   Left out: the footer column headings ("Navigation", "Contact Information", "Rules & Legal Regulations"), "Skip to main content", the whole "Developed by" box (smallest box holding "Developed by" and its outside link, so "Muhammad Yasir" and "Visit" go too), the "©" line, the chat widget text, icon-only strings with no letters, and menu link words that name a page other than home.
   Repeats: one item per address (Admission Form is a PDF, so it is listed once, under documents); for link words that hold a phone and a desktop copy ("Online Admissions Admissions") take the longest text piece inside the link.
8. **Build the note** (items 3, 4). Add `build_site_note(info)`: first line `{config.UNIVERSITY_NAME} — official links and contacts`, then short section titles of the note's own, then one line per item, e.g. `Facebook: <address>`, `Online Admissions: <address>`, `Phone: <number>`. Each line written once.
9. **Capture info during the fetch** (items 6, 7). Add `_SITE_INFO: dict` and `reset_site_info()`. In `fetch_page`, right after `_note_pdf_links(html, url)` (1349) and before `_is_empty_page`, store `_SITE_INFO[url] = extract_site_info(soup, url)` inside try/except (never fail a page). A page with no header or footer is logged.
10. **Compare with the home page** (items 6, 7). In `scrape_all_pages`, after pass 1: take the home page info (or the cached one from `SITE_NOTE_CACHE`; if none, warn and store no note). For each other page, any line or link not in the home version → warning log, added to the note, and added to `results["new_links"]`. Any UOLI page link (home or other pages) whose path is not in `config.UNIVERSITY_PAGES` → log "new page found" and add to `results["new_links"]`. Save the home info to `SITE_NOTE_CACHE` when the run had it.
11. **Store the note 6 times** (item 5). For each category, call `kb.load_scraped_page(note, note_address, category, page_hash=content_hash(note, note_address))`. Count added/updated in `results["notes"]`, not in `results["scraped"]`. A changed note gets a new hash and is stored again (4.3.4).
12. **Page directory** (item 8). Add `build_page_directory(pages, category)`: for each page in `pages` whose `config.resolve_category` is `category`, write `title — address`. Address spelling from `config.UNIVERSITY_PAGES`; title = first `#` heading line of the text, else the last part of the address. In `scrape_all_pages`, only when `include_guaranteed=True` (the full `config.py` list), call it with the `live` pages minus aliases (dead, empty and excluded never reach `live`) and store each non-empty directory under its `#pages-<category>` address, counted in `results["notes"]`. On a partial run, log "directories left unchanged" and store nothing.
13. **Setup summary** (scrape report). In `setup_knowledge_base.py`, add `notes` and `new_links` to the Phase A and Phase B print lines (one item per line for `new_links`). No other change.
14. **Fix `test_ingest_safety.py`** (item 13). In section 7: add `https://uoli.edu.pk/contact` with the footer lines to `SITE`; save and redirect `ws.CONTACT_LINES_CACHE` and `ws.SITE_NOTE_CACHE` to `_test_` files like `FURNITURE_CACHE` (382, 526–528), so the test never reads or writes the real `data/` caches; change checks 453, 455 and 507 to "address and phone are in the contact page text" and "not in the ordinary page text". Update `res["scraped"] == 20` to the new real page count if the contact page is counted (notes are counted apart).
15. **Fix `test_page_furniture.py` section 9** (item 13). Replace the old address list at 465–469 and the `KNOWN_EMPTY` / `KNOWN_ALIASES` sets with new addresses copied from `url_lists/pages.csv` by a small read-only command (no typing). Add a `check(..., expected=True)` option that prints `XFAIL` and does not add to `FAILURES`; use it for the checks that read `./data/parent_chunks.json`, with the note "fails until spec 08 rebuild".
16. **Spec 05 tests** (item 13). Run `python -m pytest tests/test_web_scraper_react.py`. Where a check fails only because its line now carries a link address, update that expected line. Any other failure: stop and report.
17. **CLAUDE.md** (item 13). Change only the two test commands in section 5.
18. **New tests** (items 1–12). Write `tests/test_cleaning_and_links.py` (section 4).
19. **Run** `python -m pytest tests/`, `python test_ingest_safety.py`, `python test_page_furniture.py`, and `git status` to confirm only section-2 files changed (item 14).

---

## 4. Tests — `tests/test_cleaning_and_links.py`

Uses the same helpers as `tests/test_web_scraper_react.py` (`FIX` folder, `no_ocr` autouse fixture, `text_of`). No internet.

| Test | What it checks | Fixture |
|---|---|---|
| `test_how_to_apply_has_portal_address` | `https://uoli.ac.pk/admissions/` sits right after its link words | `admissions__how-to-apply.html` |
| `test_contact_has_maps_address` | Google Maps address after "Get Directions on Google Maps", spelled exactly as in the HTML | `contact.html` |
| `test_mailto_added_only_when_hidden` | "Contact Secretariat (registrar@…)" is added; "Contact VC Secretariat (vc@uoli.edu.pk)" is not doubled | `offices__registrar.html`, `about__vc-message.html` |
| `test_internal_links_not_inline` | no `https://uoli.edu.pk/...` page address inside ordinary text | `about.html` |
| `test_document_link_is_full_address` | `/Docs/...pdf` becomes a full address | `academics__programs__bscs.html` |
| `test_table_cell_keeps_address` | a link in a table cell keeps its address | `services__student-resources.html` |
| `test_link_without_href_skipped` | a `<button>` with no address adds nothing | small HTML string |
| `test_site_note_contents` | 4 social, 5 header links, address, phone, email, 12 rules documents, motto, "Chartered 2012" present | `home.html` |
| `test_site_note_leaves_out_credit` | no "Developed by", "myasir.dev", "©", "Skip to main content", and no bare column heading lines | `home.html` |
| `test_site_note_items_once` | every line of the note is unique; "Online Admissions Admissions" not present | `home.html` |
| `test_note_stored_for_every_category` | a stub kb receives the note once per `config.CATEGORIES` entry | `home.html` + stub kb + fake `fetch_page` |
| `test_extra_footer_line_warns` | a copy of a page with one extra footer line → warning logged (`caplog`), the line is in the note and in `new_links` | `home.html` + `about.html` (edited in memory) |
| `test_unknown_menu_link_reported` | a menu link to a UOLI address not in `config.UNIVERSITY_PAGES` is in `new_links` | `home.html` (edited in memory) |
| `test_page_directory_per_category` | each category's directory lists every saved page of that category with title and address; BS Economics absent | all 25 fixtures |
| `test_partial_run_keeps_directories` | `include_guaranteed=False` stores no directory note | fake `fetch_page` + stub kb |
| `test_furniture_keeps_undergraduate` | `find_furniture` on the 24 page texts returns an empty set | all fixtures except BS Economics |
| `test_link_leftovers_removed` | no line equals "Back to About UOLI", "Back to All Academic Departments", "Read Article", "View All Events" | `about__vc-message.html`, `academics__departments__computer-science.html`, `home.html`, event page |
| `test_see_more_paragraph_kept` | a `<p>See more ...</p>` sentence is kept | small HTML string |
| `test_tags` | the four tag cases in item 11, plus the two note addresses → `site_info` / `page_directory` | address strings only |
| `test_dept_slugs_removed` | `KnowledgeBase` has no `_DEPT_SLUGS` | none |

---

## 5. How to check each acceptance item

1. `test_how_to_apply_has_portal_address`
2. `test_contact_has_maps_address`
3. `test_site_note_contents`, `test_site_note_leaves_out_credit`
4. `test_site_note_items_once`
5. `test_note_stored_for_every_category`
6. `test_extra_footer_line_warns`
7. `test_unknown_menu_link_reported`
8. `test_page_directory_per_category`, `test_partial_run_keeps_directories`
9. `test_furniture_keeps_undergraduate`
10. `test_link_leftovers_removed`, `test_see_more_paragraph_kept`
11. `test_tags`
12. `test_dept_slugs_removed` + full `pytest tests/` run + grep shows no `_DEPT_SLUGS`
13. The owner runs the three test commands in section 6.
14. `git status` and `git diff --stat` show only the section-2 files.
15. Human review: the owner runs the review command in section 6, reads the 3 files and opens at least 3 links in a browser.

---

## 6. Commands the owner runs by hand

Tests and file check:
```bash
python -m pytest tests/ -q
python test_ingest_safety.py
python test_page_furniture.py
git status
git diff --stat
```

Review files (item 15). Offline: reads only the saved pages, no internet, no database. Run from the project folder in Git Bash:
```bash
python -X utf8 - <<'EOF'
import csv, os
from pathlib import Path
from bs4 import BeautifulSoup
import config, web_scraper as ws

ws.OCR_AVAILABLE = False   # no image reader is loaded and no image is downloaded

fix = Path("tests/fixtures/site")
out = Path("reports/cleaning_06"); out.mkdir(parents=True, exist_ok=True)
rows = list(csv.DictReader(open(fix / "index.csv", encoding="utf-8-sig")))
soup = lambda f: BeautifulSoup((fix / f).read_text(encoding="utf-8"), "html.parser")

home = next(r for r in rows if r["file"] == "home.html")
note = ws.build_site_note(ws.extract_site_info(soup("home.html"), home["address"]))
(out / "site_note.txt").write_text(note, encoding="utf-8")

pages = {}
for r in rows:
    s = soup(r["file"])
    if not ws._is_empty_page(s):
        pages[r["address"]] = ws._extract_text(s, base_url=r["address"])
(out / "pages_admissions.txt").write_text(
    ws.build_page_directory(pages, "admissions"), encoding="utf-8")

apply_url = next(r["address"] for r in rows if r["file"] == "admissions__how-to-apply.html")
(out / "how_to_apply.txt").write_text(pages[apply_url], encoding="utf-8")
print("written:", sorted(os.listdir(out)))
EOF
```

Then open these in a text editor and check at least 3 links in a browser:
```
reports/cleaning_06/site_note.txt
reports/cleaning_06/pages_admissions.txt
reports/cleaning_06/how_to_apply.txt
```

No scraping, server, eval or `--force` command is needed for this spec.

---

## 7. Risks

- **OCR during the review command.** `_extract_text` calls the image reader. On the saved pages, images are local copies or missing, so it should do nothing. If it tries to load EasyOCR or download images, stop it and tell the supervisor (the tests avoid this with the `no_ocr` fixture).
- **Department force-include changes.** After this spec only `/academics/departments/<name>` pages carry a department, and search uses that tag to force one chunk per named department (`knowledge_base.py` around 3337–3370). Expected and accepted; the spec 08 eval should watch department questions.
- **Note addresses share the home page path.** `config._url_path` drops the `#` part, so any code that groups chunks by `_url_path(source)` would see the notes as the home page. This plan did not check every caller. The coder must keep the full address, with `#`, as the note's source, and grep for `_url_path(` on stored sources (for example in `knowledge_base.py`) before relying on it.
- **Hidden header links.** The saved home page has only 11 UOLI page links in the header, menu and footer (the large menu may load only on hover). New-page detection can only see links that are in the HTML.
