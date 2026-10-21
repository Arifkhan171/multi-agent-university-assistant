# 06 — Cleaning Rules, Site-Wide Information and Links

## 1. Problem Statement

Since spec 05, the scraper reads only the main content area of each page (`main#main-content`). The site header and footer are removed, so their text is no longer repeated on 115 pages. The chatbot still cannot answer every student question, for four reasons.

**A. Links are lost.** The scraper keeps the words of a link and drops its address. Measured on the saved pages in `tests/fixtures/site/`:

| Information | Where it lives on the site | Kept today? |
|---|---|---|
| Emails of staff and offices | page content | yes |
| Rules and policy PDFs | footer and some pages | yes (each PDF is read on its own) |
| Online Admissions portal, Fee Challan, DIMS, E-Office | header, footer, some pages | words only, no address |
| Facebook, Instagram, LinkedIn, YouTube | footer only (icons with no words) | no |
| Google Maps location | contact page | words only, no address |
| Addresses of UOLI pages ("where is the fee page?") | menu and links | no |

A student who asks "give me the link to apply online", "what is UOLI's Facebook page" or "where can I find the fee structure" gets no answer.

**B. The header and footer are thrown away completely.** They hold real information (the portal links, the social pages, the contact block, the legal PDF list, the university motto). This information can also change at any time, for example when a new portal link is added to the footer. Today such a change would never reach the chatbot.

**C. The repeated-line cleaner now removes real content.** `page_furniture.find_furniture` deletes any short line found on 25% or more of the pages. It was designed for the old WordPress menu and footer. Those are already removed by spec 05, so it now catches real content: on the 24 saved pages it would delete "Undergraduate" from every page.

**D. Leftovers from the old site.**
- Short link and button words with no facts in them: "Back to About UOLI", "Back to All Academic Departments", "Read Article", "View All Events", "See More Events (4)".
- `knowledge_base.py` tags each chunk with a department (`_extract_department`) and a content type (`_detect_content_type`) from old WordPress address words. A news page with "education" in its address is tagged as the Education department, and `/research/oric` is not tagged as an office. The department tag is used in one place in search (`knowledge_base.py`, "Force-include department chunks"): when a question names a department, one chunk with that tag is always included. Wrong tags therefore push wrong pages into answers.
- `_DEPT_SLUGS` in `knowledge_base.py` is not used anywhere.
- `test_ingest_safety.py` has three failing checks. They expect the university address and phone number on an ordinary page. That expectation is out of date: the contact block belongs on the contact page.
- `test_page_furniture.py`, section 9, checks old page addresses (`/faculty`, `/bus-routes`).
- `.claude/CLAUDE.md` lists `pytest` commands for these two test files. They are scripts and must be run with `python`.

## 2. Goal

Every fact a student may need, including links, is stored in the knowledge base exactly once, and stays current with the website.

## 3. Scope

In scope:
- Keep useful link addresses inside page text.
- Read the header and footer on every scrape and store their useful content once, as a site-wide information note.
- Detect new content in the header or footer.
- Store a page directory, so the chatbot can give the address of any UOLI page.
- Make the repeated-line cleaner safe, and remove the leftover link and button words.
- Correct the department and content-type tags, the stale tests, and the test commands in `CLAUDE.md`.

Out of scope:
- Embeddings, chunk sizes, categories, search thresholds and answer prompts.
- Live scraping (spec 07 runs a 15-page test, spec 08 the full rebuild).
- The daily refresh schedule (spec 12b).

## 4. Functional Requirements

### 4.1 Link addresses in page text
1. A link that leaves `uoli.edu.pk` (portals, maps, partners, social pages), or points to a document (`.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`), is written as `words (address)`. Example: `Apply Online (https://uoli.ac.pk/admissions/)`.
2. A `mailto:` or `tel:` link gets its email or number added only when the visible words do not already show it. Example: `Email us (info@uoli.edu.pk)`.
3. Links to other UOLI pages are not written inline. The page directory (4.4) covers them, which keeps page text readable.
4. When the words already equal the address, the address is written once.
5. A button with no address (handled only by JavaScript) has nothing to keep and is skipped.

### 4.2 Site-wide information note
1. On every scrape, the header and footer of the home page are read **before** they are removed, and turned into one note, `UOLI official links and contacts`:
   - Official social pages, with addresses. The name comes from the address when the link has no words (`facebook.com` → Facebook).
   - Portal and service links (Online Admissions, Admission Form, Fee Challan, E-Office, DIMS, Online Portal), with addresses.
   - The contact block: address, phone numbers, email, website.
   - The "Rules & Legal Regulations" list, with each document's address.
   - Any other text in the header or footer, such as the motto, the charter line and any new notice. Only the items in 4.2.2 are left out.
   - The words of a link to the home page (for example the footer logo with "Chartered 2012") count as "other text", not as a menu label.
2. Left out, because they are not about the university: the footer column headings ("Navigation", "Contact Information", "Rules & Legal Regulations"; the note uses its own section titles), the "Skip to main content" link, the "Developed by" credit and its link (`myasir.dev`), the copyright line, the chat widget text, and menu labels that only name a page (the page directory already lists every page with its address).
3. The same item is written once, even when it appears in both header and footer, or twice for phone and desktop layouts ("Online Admissions Admissions" → "Online Admissions").
4. Agents search by category, so the note is stored once per category in `config.CATEGORIES` (six small copies). This is the only repetition, and it is on purpose: without it, an agent limited to "admissions" could not find the portal link. The note's categories and source addresses are set in `config.py`, the only place categories are decided.

### 4.3 Detecting new header and footer content
1. The header and footer of every scraped page are compared with the home page version.
2. A line or link that appears in a page's header or footer but not in the home page version is logged as a warning, added to the site-wide note, and listed in the scrape report.
3. A link in the menu, header or footer to a UOLI page that is not in `config.UNIVERSITY_PAGES` is logged as "new page found" and listed in the scrape report, so the page list can be updated.
4. When the note's text changes between runs, it is stored again, like any changed page.

### 4.4 Page directory
1. After all pages are fetched, one directory note per category lists that category's pages, one per line: `title — address`. Example: `Fee Structure — https://uoli.edu.pk/admissions/fees`. Directories are built only on a run that includes the full `config.py` page list. A partial run (a test of a few pages, or a single page) leaves the stored directories unchanged.
2. The address comes from `config.UNIVERSITY_PAGES`. The title is the page's first heading in the scraped text, or the last part of its address when it has none.
3. Empty, dead and excluded pages are left out.

### 4.5 Safe repeated-line cleaner
1. `find_furniture` removes a line only if it appears on at least 60% of pages (now 25%), and the line is still at most 40 characters.
2. Every line it removes is logged once with its page count.
3. `find_site_contact_lines` and the bare-heading rule in `strip_furniture` are unchanged.

### 4.6 Leftover link and button words
A line is removed when all of these are true:
- it is the whole text of one link or button inside `main`,
- it is 40 characters or fewer,
- it starts with "Back to", "Read Article", "Read More", "View All", "View Details", "See More", "See All" or "Learn More".

The rule checks the HTML element, so a sentence in a paragraph that starts with "See more" is kept.

### 4.7 Tags
1. `_extract_department` takes the department only from `/academics/departments/<name>`. Policy documents stay `all`. Every other page has no department.
2. `_detect_content_type`:
   - `leadership_message`: `/about/…-message` and `/about/leadership`
   - `office_info`: `/offices/…` and `/research/oric`
   - `faculty_page`: `/academics/faculty` and `/academics/departments/…`
   - `site_info` and `page_directory`: the note addresses (`#site-info-…`, `#pages-…`), checked first
   - All other rules are unchanged.
   - Pages with no department keep the current default value `general`, so stored data keeps its shape.
3. `_DEPT_SLUGS` is removed.

### 4.8 Tests and commands
1. `test_ingest_safety.py`: the three stale checks now require that the address and phone number are kept on the contact page and are not repeated on ordinary pages.
2. `test_page_furniture.py`, section 9: the old addresses are replaced with new ones copied from `url_lists/pages.csv`. Checks that read the old `data/` store still fail until the spec 08 rebuild. They are listed as expected failures.
3. `.claude/CLAUDE.md`: the two commands become `python test_page_furniture.py` and `python test_ingest_safety.py`.

## 5. API Contract

Unchanged signatures:
- `_extract_text(soup, base_url) -> str`: now includes `words (address)` for outside links and documents.
- `page_furniture.find_furniture(pages, threshold=0.60) -> set`
- `scrape_all_pages(kb, urls=None, include_guaranteed=True) -> dict`: new result keys `"notes"` (number of notes stored) and `"new_links"` (list of new header, footer or menu items found).

New, in `web_scraper.py`:
- `extract_site_info(soup, base_url) -> dict`. It runs on the full page, before the header and footer are removed.
  ```
  {
    "social":   [(name, url)],
    "portals":  [(words, url)],
    "contact":  [line],
    "pdfs":     [(words, url)],
    "other":    [line],
    "pages":    [url]       # UOLI page links in the menu, header and footer
  }
  ```
- `build_site_note(info: dict) -> str`: one fact per line, for example:
  ```
  University of Loralai (UOLI) — official links and contacts
  Facebook: https://www.facebook.com/uoliofficial/
  Online Admissions: https://uoli.ac.pk/admissions/
  Phone: +92 (824) 410051
  ```
- `build_page_directory(pages: dict, category: str) -> str`

New, in `config.py`:
- The source addresses and categories of the notes. Example: `https://uoli.edu.pk/#site-info-admissions`, `https://uoli.edu.pk/#pages-admissions`. The `#` part keeps them apart from real pages, and a student who opens the source link lands on the home page, which shows the same header and footer.
- `config._url_path` removes the `#` part, so without a change every note would resolve as the home page. `resolve_category` must first look up the full note address in the note settings and return that note's category. Any other code that compares addresses through `_url_path` must not merge a note with the home page.

## 6. Constraints

- Files that may change: `web_scraper.py`, `page_furniture.py`, `knowledge_base.py` (the two tag functions and `_DEPT_SLUGS` only), `config.py` (the note settings only), `setup_knowledge_base.py` (print the `notes` count and the `new_links` list in the scrape summary only), `test_ingest_safety.py`, `test_page_furniture.py`, `tests/test_web_scraper_react.py` (only where a checked line now carries a link address), `.claude/CLAUDE.md` (the two commands only), and the new `tests/test_cleaning_and_links.py`.
- The scrape report is the log plus `results["new_links"]`, printed by `setup_knowledge_base.py`. No new report file is written by the scraper.
- No link is typed by hand. Every address comes from the website's HTML or from `config.py`.
- The university name comes from `config.UNIVERSITY_NAME`.
- BeautifulSoup and the standard library only. No new library and no API calls.
- Tests use the saved pages in `tests/fixtures/site/` and never the internet.
- `python -m pytest tests/` stays green.

## 7. Edge Cases and Error Handling

| Case | Behaviour |
|---|---|
| The run has no home page, or the home page has no header or footer | Reuse the last saved site note (as `_load_furniture` does). If none exists, log a warning and store no note. |
| A social link is a share button (contains the page's own address) | Skip it. Only the university's own pages are kept. |
| An address contains spaces | Write each space as `%20`. It is the same address, and a link shown to a student does not break at the space. An address that already has `%20` is kept as it is. |
| An `http://` address with an IP number (DIMS, Fee Challan) | Keep it. These are real university systems. |
| An address longer than 200 characters | Keep it and log it. |
| A link inside a table cell | Keep the address inside the cell. |
| A page's footer differs from the home page footer | Add the extra lines to the site note and log them (4.3). |
| The header or footer is missing on one page | Log it. Nothing else changes. |

## 8. Acceptance Criteria

Using the saved pages:
1. The `admissions/how-to-apply` text contains `https://uoli.ac.pk/admissions/` next to its words.
2. The `contact` text has the Google Maps address next to "Get Directions on Google Maps".
3. The site note built from `home.html` contains the 4 social links (Facebook, Instagram, LinkedIn, YouTube), the 5 header links (Online Admissions, Admission Form, Fee Challan, E-Office, DIMS), the address, phone, email, the 12 rules documents, and the motto. It does not contain "Developed by", "myasir.dev", "©" or "Skip to main content".
4. Each item appears once in the note.
5. Every category in `config.CATEGORIES` receives the site note, and `config.resolve_category` returns the right category for each note address (not the home page's category).
6. A test page whose footer has one extra line produces a warning, and that line is in the note.
7. A test menu link to a UOLI page that is not in `config.UNIVERSITY_PAGES` is reported as a new page.
8. The page directory for each category lists every saved page of that category with its title and address. BS Economics is not listed.
9. `find_furniture` on the 24 saved page texts removes nothing, so "Undergraduate" stays.
10. No line is exactly "Back to About UOLI", "Back to All Academic Departments", "Read Article" or "View All Events". A paragraph that starts with "See more" is kept.
11. Tags: `/academics/departments/computer-science` → `computer-science`; a `/news/…` page with "education" in its address → no department; `/research/oric` → `office_info`; `/about/vc-message` → `leadership_message`.
12. `_DEPT_SLUGS` is removed and nothing breaks.
13. `python -m pytest tests/` passes. `python test_ingest_safety.py` has no failures. `python test_page_furniture.py` fails only on the checks listed as expected failures until spec 08.
14. Only the files in section 6 are changed.
15. Human review: a one-off offline command (not a test) writes the site note, the admissions page directory and the `how-to-apply` text to `reports/cleaning_06/`. A reviewer reads them and opens at least three of the links in a browser. Each link works and is correct.
