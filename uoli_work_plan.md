# UOLI Chatbot — Work Plan

Written: 18 October 2026
Goal: rebuild the chatbot on the new React website, clean, tested and measured, step by step.
Details of every weak point are in `uoli_pre_evaluation_guide.md` (W = weak point, C = change).

---

## How every task works

1. You give the spec template.
2. We write the spec for one task (`NN_spec_<name>.md`).
3. You approve the spec.
4. Claude does the work.
5. We check the result against the spec's pass rules.
6. You sign off. Only then the next task starts.

Rules for all tasks:
- Commands that scrape, rebuild, start the server or run the eval are run by you. Claude writes the exact command.
- Anything that touches the live website goes slow (about 1 request per second) and only after your OK.
- Every task writes its result to a report file, so we can check it later.

---

## Phase 0 — Safety first (no spec needed, 10 minutes, you do it)

00. Backup
- Copy the whole `data/` folder to a safe place (for example `data_backup_2026-10-18/`).
- Commit the current code in git.
- Why: the rebuild deletes the old database. With a backup we can always compare old vs new.

---

## Phase 1 — URLs

01_spec — URL health check
- Check every URL we have today: `config.UNIVERSITY_PAGES`, `config.PDF_SOURCES`, `web_scraper.GUARANTEED_PAGES`, and the URLs written inside `graph/Agents.py`.
- Result for each URL: working / redirected (and to where) / dead.
- Important React trap: a React site often answers "200 OK" for every address, even for pages that do not exist (the page then shows "Not found" inside the browser). So a normal HTTP check is not enough. Pages must be opened in a real browser (Playwright) and the visible text checked.
- Output: a report table (CSV). No code is changed in this task.

02_spec — Find all URLs on the new website
- Sources: `sitemap.xml`, `robots.txt`, menus, header, footer, page links, and all PDF links.
- Result: a full list of new pages and PDFs, each with a suggested category from `config.CATEGORIES`.
- Output: a report table. No code is changed.

03_spec — Update the URL lists in code
- Remove dead URLs, replace redirected ones, add the new ones with a category.
- Move the URLs written in `Agents.py` and `web_scraper.py` into `config.py`, so there is only one list (fixes W16).
- Pass rule: the config checks pass; no URL in code outside `config.py`; every URL in the list is "working" in a fresh 01 check.

---

## Phase 2 — Make the scraper ready for React

04_spec — Study the new website structure
- Open 10–15 sample pages of each type (home, department, faculty list, office, news, admission, fee, policy, contact, PDF page).
- Write down: header, footer, menu, sidebar, cookie banner, chat widgets, pop-ups, how faculty cards look, how tables look, where PDFs and images are.
- Also check: how long a page needs to load fully in the browser.
- Output: a findings report. No code is changed.

05_spec — Update the scraper code
- Wait until the React page is fully loaded before reading it.
- Remove header, footer, menu and widgets by their HTML element, not only by guessing from repeated lines.
- Update the faculty card reader and table reader for the new HTML.
- Remove old WordPress-only parts (for example `wp-content` addresses).
- Pass rule: the existing tests (`test_page_furniture.py`, `test_ingest_safety.py`) still pass, plus new tests made from the 04 sample pages.

06_spec — Cleaning and repetition rules
- No header, footer or menu text inside page content.
- The same block repeated on many pages is stored only once.
- Two addresses that show the same page are stored once.
- "Not found" and empty pages are never stored.
- Fix BM25 word splitting now (W15), because the index will be rebuilt anyway.
- Decide which old caches to delete (`ocr_cache.json`, `pdf_cache.json`, `proposition_cache.json`, `page_furniture.json`).

07_spec — Small test scrape (dry run)
- Scrape about 15 pages into a test folder only, never into the database. You run it.
- We read the cleaned text together and run automatic checks: no footer lines, no repeated blocks, no "not found" pages, nothing too short.
- Pass rule: all checks pass on all sample pages. If not, go back to 05 or 06.

---

## Phase 3 — Rebuild the knowledge base

08_spec — Full scrape and rebuild (you run the commands)
- Full scrape → `setup_knowledge_base.py --force` → `setup_propositions.py`.
- After the rebuild, a data quality report: number of chunks per category, number of duplicates, any header/footer text left, pages with zero chunks, 20 random chunks to read by eye.
- Pass rule: agreed numbers in the spec (for example zero footer lines, zero empty pages).
- Graph RAG rebuild (`build_graph_rag.py`) comes at the end of this phase, only after the data report passes.

---

## Phase 4 — Code fixes before testing

Each one may become 2–3 smaller specs. See the guide for details.

09_spec — Honest evaluation path (C1–C6): the eval uses the same path as the students and records time, tokens and cost.
10_spec — Wrong-answer bugs (C9, C12–C17): chitchat, keyword matching, routing rules, cache, stream line breaks, names in code.
11_spec — Speed and cost (C18–C25).
12_spec — Many users and clean-up (C26–C30).

---

## Phase 5 — Test question sets (Claude drafts, you review)

13_spec — Development question sets (used for tuning)
- Routing set: about 100 questions, each with the correct agent (English, Roman Urdu, Urdu, typos, tricky words).
- Retrieval set: about 60 questions, each with the correct source URL and a short "must contain" fact.

14_spec — The 100-question evaluation dataset (kept separate, never used for tuning)
- Written from the NEW website, with a ground truth and source URL for each question.
- A fixed mix: admissions, fees, faculty, policy, campus, general, out-of-scope, Roman Urdu, multi-part, multi-turn.
- Why two separate sets: if we tune the bot on the same questions we test with, the score goes up but real students do not get better answers.
- Also update CLAUDE.md (70 → 100 questions).

---

## Phase 6 — Component tests (each part alone)

15_spec — Routing tests: chitchat, follow-up, keyword rules, AI router accuracy.
16_spec — Retrieval tests: hit rate at 5, rank of the first right document, zero-result questions, per agent.
17_spec — Generator tests: with fixed good documents, does the answer use them, refuse when the fact is missing, keep a good length, never invent contacts.
18_spec — Other parts: cache, session store, stream format, prompts, config checks.

---

## Phase 7 — System tests (the whole chatbot)

19_spec — RAGAS run: faithfulness, answer relevancy, context precision, context recall.
20_spec — DeepEval run: faithfulness, correctness (GEval), hallucination, contextual precision and recall.
21_spec — Speed and cost: time to first word, total time, p50 and p95, cost per question.
22_spec — Many users at the same time (load test).
23_spec — Multi-turn, languages, out-of-scope, safety tricks, failure tests (Valkey down, OpenAI down).
24_spec — Human review and the final report.

---

## Order and why

- URLs and scraper before the rebuild: otherwise we store garbage in the database again.
- Rebuild before the question sets: ground truth must come from the new website.
- Code fixes before testing: otherwise the test measures a broken path.
- Component tests before system tests: when a system score is low, the component results tell us which part to fix.
- Phase 4 does not depend on the new data. If a phase is waiting for you (for example during the rebuild), we can work on Phase 4 meanwhile.

---

## What can start today

1. Step 00 — Backup. You copy `data/` and commit the code. 10 minutes.
2. 01_spec — URL health check. This is the right first spec:
   - It does not change any code.
   - It does not touch the database.
   - It only reads the website, slowly, with your OK.
   - Its result decides everything in Phase 1 and Phase 2.

Next step: you send the spec template, and we write 01_spec together.
