# UOLI Chatbot — Project Status and Rules

Last updated: 21 October 2026
Read this file first in every new session. It holds the aim, the rules, what is done, and what we learned.
Other key files: `uoli_work_plan.md` (the road map, specs 01–31), `uoli_pre_evaluation_guide.md` (weak points W1–W36), `.claude/CLAUDE.md` (project rules).

---

## 1. Our aim

Rebuild the UOLI chatbot on the new React website, clean and tested, step by step. Then test it in 3 levels (component, workflow, whole system) with 3 methods (program, AI judge, human), and measure quality, safety, speed and cost.

No rush. One small task at a time. Every task is checked before the next one.

---

## 2. How we work (rules)

**Roles**
- Claude is the supervisor. Claude writes every spec.
- The owner reviews and approves, runs the heavy commands, and does the human checks.

**Every spec must have** these sections (the owner's template):
Problem Statement, Functional Requirements, API contracts (inputs, outputs, data shapes), Constraints, Edge cases and error handling, Acceptance criteria. Claude may add: Scope, Facts, Dependencies, Git, What You Do.

**Before every spec:** Claude first gives a short overview (what the spec will do, what is done, what is still needed) and waits for the owner's ideas. Only then is the spec written.

**Spec style**
- Short, very easy English, no jargon, few tables.
- Specs are pushed to GitHub, so they read like an expert engineer wrote them. No "What You Do" list, git steps, session steps or owner instructions inside a spec. Those go in the chat, with every command ready to copy.

**Quality rule:** the chatbot must answer every student need from its own knowledge (links, portals, social pages, header and footer facts), never "go and look on the website". Each fact is stored once, not on every page. Header and footer are read on every scrape, so new information there reaches the chatbot.

**Files**
- Specs: `.claude/specs/NN_name.md`. Plans: `.claude/plans/NN_name.md`.

**Three sessions (from 21 Oct)**
- Supervisor session (Claude): writes specs, reviews plans and code, solves problems. It never commits or pushes until the owner says so.
- Planning session (Opus 5.5 or Fable): uses `.claude/PLANNING_PROMPT.md`, saves the plan and stops. No code.
- Coding session (Sonnet 5.5): a new one per spec. It follows the approved plan and commits on the branch.

**Flow for each spec (one new session per spec)**
1. Write the spec here (supervisor session) and push it to `main`.
2. New session → `/rename NN name`
3. `git pull origin main`
4. `git checkout -b feature/NN-name`
5. Plan mode: read the spec and the relevant code. Save the plan to `.claude/plans/`.
6. The owner approves the plan ("Yes, manually") and checks each edit.
7. Validate against the acceptance criteria. Live tests only after the owner's OK.
8. Commit **only named files**. Never `git add .`
9. Push the branch → PR on GitHub (in the browser, `gh` is not installed) → merge.
10. `git checkout main` → `git pull origin main` → `git branch -D feature/NN-name`
- Always say if a spec needs pull/push/PR.

**Hard rules**
- Claude never runs: `eval_ragas.py`, `setup_knowledge_base.py`, `setup_propositions.py`, `build_graph_rag.py`, `web_scraper.py` or any scraping, `uvicorn`, anything with `--force`. Claude writes the command; the owner runs it.
- The live website is touched gently: one browser, one page at a time, at least 1 second apart, only after the owner's OK. (Three parallel browsers once made the site answer 503.)
- **No link is typed by hand.** Links are copied by tools from reports, and checked letter by letter.
- All page and PDF links live in `config.py` only. The lists are written by `tools/build_url_lists.py` from `url_lists/*.csv`.
- Never fix the chatbot using the hidden final-exam questions (spec 14).
- Change one thing, measure, then change the next.

**Session size (from `/context`)**
- Read files with offset and limit, or `grep`, or `sed -n`. Do not read whole big files when a few lines are needed.
- Do not re-read files already read in the session unless they changed.

---

## 3. Status

| Spec | Name | Status |
|---|---|---|
| 00 | Backup of `data/` | done |
| 01 | URL health check (`tools/url_health_check.py`) | done, merged |
| 02 | Find new pages and PDFs (`tools/find_site_urls.py`) | done, merged |
| 02b | Find hidden pages, click crawl (`tools/find_hidden_pages.py`) | done, merged |
| 03 | Update link lists in code (`tools/build_url_lists.py`) | done, merged |
| 04 | Study the new site structure (`tools/study_site_structure.py`) | done, merged |
| 05 | Update the scraper for React (`.claude/specs/05_update_scraper.md`) | done, merged (live BS CS check: 8 semesters, 47 courses) |
| 06 | Cleaning rules and links (`.claude/specs/06_cleaning_and_links.md`) | done, merged (PR #7) |
| 07 | Small live test scrape (dry run, `tools/dry_run_scrape.py`) | done, merged |
| 07b | Fixes from the dry run (FAQ answers, placeholders, staff cards, related block, OCR limit) | done after merge; second dry run clean |
| 08 | Full scrape and rebuild | **next** |
| 06–31 | See `uoli_work_plan.md` | waiting |

---

## 4. Important files

| What | Where | In git? |
|---|---|---|
| Final page and PDF lists (owner's decisions) | `url_lists/pages.csv` (116 rows, 115 kept), `url_lists/pdfs.csv` (18 rows, 12 kept) | yes |
| Link lists used by the code | `config.py` between `# BEGIN URL LISTS` and `# END URL LISTS` (written by the tool, never by hand) | yes |
| Link tables used by agents | `config.OFFICE_URLS`, `DEPT_URLS`, `LEADERSHIP_PAGES`, `CONTACT_PAGES`, `FEE_FALLBACK_PAGES`, `FACULTY_LIST_PAGE` | yes |
| Offline copies of 25 real pages | `tests/fixtures/site/` + `index.csv` (1.6 MB) | yes |
| Tools and their tests | `tools/`, `tests/` (about 210 tests) | yes |
| Reports (link checks, crawls, site study, screenshots) | `reports/` | no, local only |
| Old evaluation data (old site) | `data/`, `eval_raw_results.json` | old, not valid |

---

## 5. What we learned about the new website

**Links**
- Almost all old WordPress links died. New layout: `/about/...`, `/admissions/...`, `/academics/departments/<name>`, `/academics/programs/<name>`, `/offices/<name>`, `/services/...`, `/campus-life/...`, `/research/...`, `/news/...`.
- The site answers HTTP 404 for every inner page but still shows the content (React). Every check must judge by page content, not by status.
- A made-up address can show another real page. Always check the final address in the browser.
- Some links only appear after scrolling. Always scroll before reading links.
- PDFs live under `/Docs/...` (12 real). 6 "Proforma" PDFs under `/wp-content/uploads/2026/04/` give 403 even in a browser: not used.
- Not on the new site: alumni page, bus timetable or route list, Universities Act 2022 PDF, a separate FAO page (FAO is inside `/services/scholarships`). The BS Economics program page has no body text yet.
- `old_to_new_*.csv` (spec 02) had wrong matches. It is a hint only, never a source.

**Page structure (spec 04, 25 pages)**
- Real content: `main#main-content`, on 25 of 25 pages.
- Header, menu, footer, chat widget ("UOLI AI Assistant ... Ask me anytime!"), back-to-top button and skip link: outside `main`, the same on every page. No pop-ups, cookie bars, carousels or iframes.
- The breadcrumb ("Home › About") is **inside** `main`.
- The header floats over the top of the page, so the screenshots look like the green box covers the menu. The HTML proves it does not.
- Number counters: the label comes after the number ("1500+" then "STUDENTS ENROLLED").

---

## 6. Open items for the next specs

**Spec 05 — scraper update**
1. Read only `main#main-content`. Remove the breadcrumb inside it.
2. The old scraper loses page titles and hero headings on about 11 pages ("Vice Chancellor's Message", "Scholarships & Financial Aid", "BS Computer Science" and others). Fix it.
3. The event page loses about 17 detail lines ("SESSION LEAD & FACILITATOR", "Supervision / Partner"). Fix it.
4. On empty pages the chat widget text leaks in (BS Economics: 55% garbage). Empty pages must be skipped.
5. Small UI labels leak: "Sem 1", "Sem 5", "Share", "Print".
6. Faculty and HOD cards: the tool found no "card" class, but the Computer Science page lost nothing. Confirm the HOD name, emails and the faculty list are kept (fixture `academics__departments__computer-science.html`).
7. The BS CS program page has a course table with semester tabs ("Showing 6 Courses"). Check whether the courses of all semesters are in the page text. If not, the scraper must click each semester tab.
8. The rebuild-time crawler `advanced_crawler.py` uses `requests` + BeautifulSoup, so it is blind to React. Make it React-aware, or rely on the `config.py` list. Decide in spec 05.
9. Old address shapes still in the code: `web_scraper.py` `_faculty_department` (about line 725, uses `/faculty/<dept>`), `_DEPT_SLUG_OVERRIDES` (about 647–649), `_NAV_NOISE` (old menu words, about line 94), the dead `_AD_IMAGE_URL` (about line 1222).
10. The scraper's OCR loads EasyOCR at import time (slow, may download about 100 MB).

**Spec 06 — cleaning rules**
- `knowledge_base.py` tags department and content type by words in the address: about lines 537–540, 566–568, 578, and the unused `_DEPT_SLUGS` (about 2945). Small gaps: `/research/oric` is not an office by those rules, and a news page with "education" in its address is tagged as the Education department.
- Old test baseline: `python test_page_furniture.py` has 2 known FAIL (old data), and `python test_ingest_safety.py` has 3 known FAIL (the address and phone are stripped from ordinary pages). Decide if those 3 are a real bug or a stale test.
- `test_page_furniture.py` section 9 checks old pages (`/faculty`, `/bus-routes`...). It will break after the rebuild, so update it.
- These two old test files are scripts. Run them with `python`, not `pytest`. CLAUDE.md still lists the wrong `pytest` commands.

- Header and footer facts (checked 21 Oct): phone, email and address are also in `/contact`; the motto and "Chartered 2012" are on the home page; all 12 footer PDFs are our 12 kept PDFs. Only the E-Office link is in the header and footer alone. Gap: the text step keeps link words but drops link addresses, so "where do I apply online" may lose the portal address (`uoli.ac.pk/admissions`). Decide: keep important link addresses, and/or add one small "site-wide info" document (portal, fee challan, E-Office, DIMS links) stored once.

- From the spec 05 code review: social media links now come from no page (they are only in the footer), so store them once in the same "site-wide info" note. "Back to All Academic Departments" style link labels leak. Bare `###` / `####` heading lines (old behaviour) remain.

**Spec 07 — small test scrape**
- Spec 05 changed the line de-duplication: table rows now repeat freely under different headings. Watch whether the 15 pages grow too much.
- `fetch_page` writes `scratch/_dryrun_web_cache.json`. Keep `scratch/` out of git (consider adding it to `.gitignore`).

**Spec 08 — rebuild (from specs 07 and 07b)**
- Clear or move the old WordPress-era lists before the rebuild: `data/page_furniture.json`, `data/page_contact_lines.json`. A run under 20 pages falls back to them.
- Watch the log for "scanned pages not read — MAX_OCR_PAGES" (now 150); the dry run could not prove the new limit live.
- The owner's concern (22 Oct): big PDFs (about 120 pages) were only partly read before, and the partial result was kept in `data/pdf_cache.json`. Delete that cache so every PDF is read fresh. The quality report must show, per PDF: total pages, pages read as text, pages read by OCR, pages skipped, characters stored, and any rejection reason.
- The quality report must also prove: every page and PDF has the intended category, each category has content, no page or PDF is missing, no empty or duplicate chunks, and a sample of chunks is read by a human.
- The full rebuild is expected to take about 30–45 minutes (OCR on the first run).
- Accepted website-side issues (report to the university web team): `[EMAIL_ADDRESS]` on the IT page, form buttons linking to `#`, empty BS Economics and B.Ed Honors pages, `http://` IP links for Fee Challan and DIMS, Qarshi/Qureshi spelling, Mr. Inayat Ullah listed with Mr. Nasrullah's email.

**Spec 08 — rebuild**
- Clear the old caches first (`ocr_cache.json`, `pdf_cache.json`, `proposition_cache.json`, `page_furniture.json`). Keep the `data/` backup.

**New spec 12b — daily refresh (after spec 08 works)**
- Today the daily job is OFF (`config.SCRAPE_INTERVAL_HOURS = 0`). When on, it re-reads every page in the `config.py` list and re-stores only pages whose fingerprint (`content_hash`) changed. It does not use dates.
- Empty pages: fine already. They stay in the list and are checked every run (spec 05 rule).
- New pages: NOT found today (the list is fixed and the Phase B crawler is off). Add a daily "find new pages" step that reuses the spec 02/02b logic (menu + hub pages, scroll, gentle speed). New addresses with a known shape (`/news/`, `/research/innovation/events/`, `/academics/programs/`, `/academics/departments/`, `/offices/`) are added automatically, with the category from the `config.py` address rules. Others go to a report for the owner.
- Removed pages: their old text stays in the database. Rule needed: a page that is dead or empty for 3 runs in a row has its old chunks removed, with a log line.
- Changed pages: check that the old chunks of a changed page are replaced, not kept next to the new ones.

**Spec 10 — wrong-answer bugs (from the plan 06 review)**
- `knowledge_base.py` "Force-include department chunks" (about line 3337) uses substring keywords ("cs" also matches "economics", "physics"), and its department list is the old one (no economics etc.). Fix it with whole-word matching and the department names from `config.DEPT_URLS`.
- `setup_knowledge_base.py` only shows its per-page log lines because importing `advanced_crawler.py` turns logging on. Make the setup script configure its own logging (spec 12).
- `_extract_social_links` in `web_scraper.py` finds nothing since spec 05 (the spec 06 site note replaces it). Remove it in spec 12 cleanup.

**Later**
- CLAUDE.md says 70 eval questions. The plan is a 100-question hidden final exam (spec 14). Update CLAUDE.md then.
- Do not write eval questions about the alumni page, the bus timetable, the Universities Act or BS Economics details. The chatbot should say "not available".
- Code weak points W1–W36 (eval path, chitchat, keywords, cache, speed, cost) are in `uoli_pre_evaluation_guide.md`. They are fixed in specs 09–12.

---

## 7. Next step

Spec 05 is written. Do the code session for it (plan, code, offline tests, one live BS CS check). Then write spec 06.

Spec 05 findings from the page copies (21 Oct): the page title box is a `<header>` INSIDE main (the old scraper deletes all headers, which is why titles were lost); BS CS shows only Semester I (6 courses) until the "Sem 2..8" buttons are clicked; the CS page has 9 people (HOD M. Khawar Abbas listed twice); `/academics/faculty` has 90 people; `_FOOTER_NOISE` contains "muhammad khawar abbas"; `MAX_PAGE_CHARS` 15000 cuts `/academics/undergraduate` (17,681); the page delay is 0.8 s; decision: Phase B crawler off (`USE_AUTO_CRAWLER = False`).
