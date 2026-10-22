# UOLI Chatbot — Summary of Each Step

This file explains, step by step, how the UOLI chatbot is being rebuilt after the university website moved from WordPress to a new React website. Each step (called a "spec") has the same four parts:

- **Problem:** what was wrong.
- **Solution:** what we built or changed.
- **Why the WordPress-era code failed:** what is different on the new website.
- **Result:** what we have now.

It is written for everyone: the project owner, the IT Directorate, and readers without a technical background.

---

## Background: what changed between the old and the new website

The chatbot answers students from a copy of the university website. A program called the **scraper** visits each page, takes its text, cleans it, and stores it in the chatbot's knowledge base. The scraper was written for the old WordPress website. The new React website looks similar to a visitor, but it is built very differently underneath.

**1. Page addresses changed.**
Before: `/faculty`, `/bus-routes`, `/office-of-registrar`.
Now: `/academics/faculty`, `/offices/registrar`. Almost every old address stopped working.

**2. The server's answer can no longer be trusted.**
Before: a real page answered "OK" and a missing page answered "not found".
Now: every inner page answers "not found" (code 404) but still shows its content in the browser.

**3. Unknown addresses behave differently.**
Before: an unknown address showed an error page.
Now: an unknown address can quietly show a different real page.

**4. Some content is hidden until the visitor acts.**
Before: all text was in the page at once.
Now: some content appears only after scrolling or clicking, such as FAQ answers and semester course tables.

**5. Every page is wrapped in site furniture.**
Before: the header and footer were simple and held little.
Now: every page has a large header, menu, footer and chat widget around the real content, and the header and footer hold important links.

**6. Faculty and staff cards have no fixed labels.**
Before: cards carried labels such as "faculty-card".
Now: there are no fixed labels, so cards must be recognised by their layout.

**7. Photos can leave placeholder text.**
Before: photos were shown normally.
Now: when photos are not loaded, the site shows the text "Image Unavailable".

**8. Documents moved.**
Before: PDFs were under `/wp-content/uploads/…`.
Now: PDFs are under `/Docs/…`.

Because of these differences, nearly every part of the scraping and cleaning had to be checked and updated.

---

## Spec 00 — Backup

**Problem:** The rebuild changes the chatbot's stored data. A mistake could not be undone.

**Solution:** A full copy of the `data/` folder was made before any change.

**Result:** The old knowledge base can be restored at any time.

---

## Spec 01 — Check the old links

**Problem:** The chatbot's list of website pages and documents was written for WordPress. Nobody knew which links still worked.

**Solution:** A tool (`tools/url_health_check.py`) opened all 74 old links and recorded what happened to each.

**Why the WordPress-era code failed:** The new website answers "not found" for every inner page, even pages that work, and some unknown addresses show a different page. A simple "is the link alive?" test gives wrong answers. Each page has to be judged by what it actually shows.

**Result:** Of 61 pages, only 1 still worked as before; 43 had moved (and about 13 of those "moves" were false matches), 14 were dead, and 3 needed review. All 12 old PDF links and 1 image were blocked. The old list could not be repaired; the new pages had to be found from the new website itself.

---

## Spec 02 — Find all pages and documents on the new website

**Problem:** We needed the complete, correct list of the new website's pages and PDFs.

**Solution:** A tool (`tools/find_site_urls.py`) followed the new website's own menus and links and confirmed each page by its content. The owner checked the results by hand.

**Why the WordPress-era code failed:** The old page-discovery program reads raw page code without running it. React builds its links only when the page runs in a browser, so the old program saw almost nothing.

**Result:** 108 real pages and 18 PDFs (12 usable under `/Docs/`; 6 old "Proforma" files blocked). Some old pages no longer exist on the new website: the alumni page, the bus timetable, the Universities Act 2022 PDF and a separate financial aid office page.

---

## Spec 02b — Find hidden pages

**Problem:** Some pages, such as event pages under Research and Innovation, were missing from the list.

**Solution:** A second tool (`tools/find_hidden_pages.py`) opened about 40 "hub" pages, scrolled to the bottom, and followed "View All", "Read Article" and "See More" buttons.

**Why the WordPress-era code failed:** On the new website some links appear only after the visitor scrolls down. A tool that does not scroll never sees them.

**Result:** 5 more pages were found. The final list has 115 pages and 12 PDFs.

---

## Spec 03 — Put the new list into the chatbot

**Problem:** Page addresses were typed by hand in several places in the code. One wrong letter breaks a link silently.

**Solution:** The approved lists live in two files, `url_lists/pages.csv` and `url_lists/pdfs.csv`. A tool (`tools/build_url_lists.py`) copies them into `config.py` and checks them letter by letter. No address is typed by hand anywhere.

**Why the WordPress-era code failed:** The old code held the old addresses, spread across several files.

**Result:** One source of truth for all links. One page that had silently moved (a news page that now lives under Research and Innovation) was found and corrected.

---

## Spec 04 — Study how the new pages are built

**Problem:** Before changing the scraper, we had to know exactly where the real content sits on a new page and what is "furniture" (header, menu, footer, chat widget).

**Solution:** A tool (`tools/study_site_structure.py`) studied 25 sample pages, one of each type, measured them, took screenshots, and saved offline copies so later tests can run without touching the website.

**Why the WordPress-era code failed:** The old scraper was tested on what it would store today. It lost the page title on about 11 pages, lost the details of event pages, stored the chat widget text on empty pages, and kept button words such as "Share" and "Print".

**Result:** The real content of every page is inside one area called `main-content`. The header, footer and widget are always outside it. 25 offline page copies were saved for testing.

---

## Spec 05 — Update the scraper for the new website

**Problem:** The scraper lost real content and stored junk (see spec 04).

**Solution:**
- The scraper now reads only the main content area and removes the site furniture around it.
- It clicks each semester tab on program pages, so all 8 semesters' courses are stored (BS Computer Science: 47 courses instead of 6).
- It recognises faculty cards by their layout and writes one clear line per person: name, job, department and email (90 people on the faculty page).
- It skips pages that have no content yet (for example BS Economics). These pages stay on the list and are checked again on every run, so they are picked up as soon as the website fills them.
- It removes button words, waits at least 1 second between pages, and no longer cuts long pages.

**Why the WordPress-era code failed:** It deleted every "header" and "aside" element. On the new website the page title and event details are also built from those elements, so they were deleted too. It never clicked anything, so hidden semester tables were never read. It looked for a "faculty-card" label that no longer exists.

**Result:** Clean, complete page text, checked on all 25 sample pages and on the live BS Computer Science page.

---

## Spec 06 — Links, site-wide information and safe cleaning

**Problem:**
- The chatbot could not give links. It kept the words "Apply Online" but lost the address. Social media pages (Facebook, Instagram, LinkedIn, YouTube) were lost completely.
- The header and footer were removed entirely, so any information only there (portals, contact block, rules documents) was lost, and new footer information would never reach the chatbot.
- The old "repeated line" cleaner would now delete real words such as "Undergraduate" from every page.

**Solution:**
- Useful link addresses are kept next to their words, for example `Apply Online (https://uoli.ac.pk/admissions/)`.
- On every scrape, the header and footer are read once and turned into one note: social pages, portals (Online Admissions, Fee Challan, E-Office, DIMS), the contact block, the 12 rules documents and the university motto. It is stored once per topic area, not on every page.
- If the header or footer gains something new, it is reported and added to the note. A menu link to a page that is not on our list is reported as a new page.
- Each topic area gets a page list (page title and address), so the chatbot can answer "where is the fee page?" with a real link.
- The repeated-line cleaner was made much stricter, and leftover navigation words were removed.

**Why the WordPress-era code failed:** On the old website the header and footer held little, so throwing them away lost nothing. On the new website they hold important links. The old cleaner was designed for the old menu and footer, which no longer reach the text, so it started removing real content.

**Result:** A student can ask for the admission portal, a social media page, a form or any page, and the chatbot has the exact link.

---

## Spec 07 — Dry run on live pages

**Problem:** Everything so far was tested on 25 saved sample pages. 90 of the 115 pages had never been read by the new code. The full rebuild costs money and replaces the chatbot's data, so problems had to be found before it.

**Solution:** A tool (`tools/dry_run_scrape.py`) reads 16 live pages and 3 PDFs through the real scraper, but with a stand-in database that only records what would be stored. No cost, no change to the chatbot's data. It writes a report and one text file per page for human review.

**Why the WordPress-era code failed:** Small runs reuse saved lists of "lines to remove" that were measured on the old WordPress website. Used on the new pages, they could silently delete real lines. The dry run uses fresh, empty lists.

**Result:** 15 of 16 pages were good. 13 problems were found and decided (fix or accept).

---

## Spec 07b — Fixes from the dry run

**Problem:**
- FAQ answers were missing: on the Admissions FAQ page 9 of 10 answers were lost, and program pages kept questions without answers.
- "Image Unavailable" was stored as text, and once became a false "Status" for a head of department.
- News articles stored the titles and dates of other articles from a "Related Official Notices" box.
- Office staff cards could mix up names, jobs and emails.
- A long PDF had 2 pages skipped because of a page limit.

**Solution:**
- The scraper clicks every FAQ question and stores each answer under its question.
- The placeholder text is removed and can never become a fact.
- The related-articles box is removed from news pages.
- Each office staff member becomes one correct line, with phone numbers kept. When the website lists two people with the same email, both are kept and the second is marked "Email not listed".
- The PDF page limit was raised from 90 to 150.
- A safety net stops any cleaning step from deleting more than half of a page. This was added after a bug in the FAQ fix deleted most of a program page during testing; the bug was caught by the second dry run and fixed.

**Why the WordPress-era code failed:** The old website showed all text at once. The new website hides FAQ answers until they are clicked, shows placeholders when photos are not loaded, and builds staff cards without fixed labels.

**Result:** The second dry run on the same 16 pages was clean: every page starts with its own title, both BBA programs keep their full content plus FAQ answers, and no junk remains.

**Website issues found (for the university's web team):** a placeholder `[EMAIL_ADDRESS]` on the IT page, form download buttons with no file behind them, empty BS Economics and B.Ed Honors pages, `http://` addresses for Fee Challan and DIMS, two spellings of "Qarshi/Qureshi", and Mr. Inayat Ullah listed with Mr. Nasrullah's email.

---

## Next step

**Spec 08 — Full scrape and rebuild:** read all 115 pages and 12 PDFs with the new scraper, rebuild the chatbot's knowledge base, and check the data with a quality report.
