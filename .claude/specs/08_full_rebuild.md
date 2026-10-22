# 08 — Full Scrape, Rebuild and Data Quality Report

## 1. Problem Statement

The chatbot's knowledge base in `data/` was built from the old WordPress website. Specs 01–07b produced a correct page list (115 pages, 12 PDFs), a scraper that reads the React website correctly, and cleaning rules proven on live pages. The knowledge base must now be rebuilt from the live website.

Several things left over from the WordPress era would silently corrupt a new build if they are not removed first:

- `data/pdf_cache.json` holds earlier PDF readings, including big PDFs that were only partly read. A rebuild would reuse them instead of reading the PDFs again.
- `data/page_furniture.json` and `data/page_contact_lines.json` hold "lines to remove" measured on the WordPress website. Small runs fall back to them.
- `data/ocr_cache.json` and `data/proposition_cache.json` hold image readings and propositions produced from old content and old rules.
- `data/graph_rag/` holds the old knowledge graph. The chatbot enables Graph RAG automatically whenever this folder exists (`graph/workflow.py`), so old facts would be mixed into new answers.
- The semantic cache (Valkey database 1) holds answers given from the old data. A cache hit skips the whole search, so old answers would be served again.

A rebuild that finishes without errors can still be wrong: a missing page, a partly read PDF, a wrong category or junk text would only show up later as a bad answer. The rebuild therefore needs a data quality report that proves the new knowledge base is complete and clean before students use it.

## 2. Goal

The knowledge base holds every page and PDF of the live website, complete, clean and correctly categorised, with no WordPress-era data left anywhere, and a report that proves it.

## 3. Scope

In scope:
- A preparation tool that backs up `data/` and moves every old cache and the old graph aside.
- The rebuild itself, run by the owner with the existing scripts.
- A data quality report tool that reads the finished knowledge base offline.
- Emptying the semantic cache, rebuilding Graph RAG, and a short human check of answers, after the report passes.
- Adding `scratch/` to `.gitignore`.

Out of scope:
- Changes to the scraper, cleaning, chunking, embedding or search code. A problem found by the report is fixed in a follow-up spec, followed by a new rebuild.
- The evaluation run with test questions (specs 13 onward).

## 4. Functional Requirements

### 4.1 Phase 1 — Prepare (`tools/prepare_rebuild.py`)
1. Without options, the tool only reports what it would do: the files and folders it would move, their sizes, and the backup location. Nothing changes.
2. With `--apply`, it:
   - copies the whole `data/` folder to `backups/data_<date-time>/` and checks the copy (same file count and total size);
   - moves these into `backups/old_caches_<date-time>/`: `data/pdf_cache.json`, `data/ocr_cache.json`, `data/proposition_cache.json`, `data/page_furniture.json`, `data/page_contact_lines.json`, the site-note cache from spec 06, and the folder `data/graph_rag/`;
   - leaves the knowledge stores (`data/chroma_db/`, `data/parent_chunks.json`, `data/bm25_index.pkl`) in place. `setup_knowledge_base.py --force` snapshots and resets them itself.
3. Before it changes anything, it checks:
   - `config.UNIVERSITY_PAGES` and `config.PDF_SOURCES` match `url_lists/*.csv` (`tools/build_url_lists.py --check`);
   - `python -m pytest tests/ -q` is green;
   - there is enough free disk space for the backup.
   If any check fails, it stops and changes nothing.
4. It prints the exact commands for Phase 2.
5. `backups/` is added to `.gitignore`, together with `scratch/`.

### 4.2 Phase 2 — Rebuild (run by the owner)
1. `python -X utf8 setup_knowledge_base.py --force`, with its full output saved to `reports/rebuild_08/setup_log.txt`.
2. `python -X utf8 setup_propositions.py`, with its output saved to `reports/rebuild_08/propositions_log.txt`.
3. Expected time: 30–45 minutes for step 1 (all pages read once, images and scanned PDF pages read for the first time) and a few minutes for step 2.
4. Expected cost: below USD 2 in total (embeddings with `text-embedding-3-large`, propositions with `gpt-4o-mini`). The plan gives a closer estimate from the dry-run page sizes before the owner runs anything.

### 4.3 Phase 3 — Data quality report (`tools/data_quality_report.py`)
The tool reads `data/` and the two log files. It makes no API calls and changes nothing. It writes `reports/rebuild_08/quality_report.md`, `reports/rebuild_08/sample_chunks.md` and `reports/rebuild_08/quality.json`.

The report states PASS or FAIL for each check, with the details behind it:

1. **Page coverage.** Every page in `config.UNIVERSITY_PAGES` is listed as stored, empty, dead, failed or excluded, with the reason taken from the log. FAIL if any page is missing without a reason.
2. **PDF coverage.** For every PDF in `config.PDF_SOURCES`: stored or not, total pages, pages read as text, pages read by OCR, pages skipped, characters stored, and any refusal reason. FAIL if a PDF has skipped pages, or is not stored without a reason.
3. **Categories.** Every stored page and PDF carries the category that `config.resolve_category` or `config.resolve_pdf_category` gives it. The number of pages, parent passages and chunks per category in `config.CATEGORIES`. FAIL if a category is empty or a source has the wrong category.
4. **Notes.** The site-info note is present in every category, and the page directories are present. FAIL if any is missing or has the wrong category.
5. **Junk.** No stored text contains the known junk: "UOLI AI Assistant", "Developed by", "myasir.dev", "Skip to main content", "Image Unavailable", "Related Official Notices", "Return to", a breadcrumb trail, or a line that is exactly a button label from spec 05/06/07b. FAIL on any hit, with source and line.
6. **Chunk health.** No empty chunks, no exact duplicate chunks within a category, no chunk whose parent passage is missing, and the BM25 index has the same number of entries as the vector store. FAIL on any problem.
7. **Embeddings.** Every stored vector has 3072 numbers. FAIL otherwise.
8. **Old data.** No stored source address uses an old WordPress address shape (`/wp-content/`, or an address not in the current page or PDF list and not a note). FAIL on any.
9. **Sizes.** Characters stored per page and per PDF, with the 10 smallest and 10 largest listed for a human to judge.
10. **Sample.** 30 random chunks, at least 3 from each category, with their source and category, written to `sample_chunks.md` for human reading.

### 4.4 Phase 4 — Finish (only after the report passes)
1. The semantic cache is emptied. The plan gives the exact command, which touches Valkey database 1 only and never database 0 (sessions).
2. `python build_graph_rag.py` rebuilds Graph RAG from the new data (about 15–20 minutes, about USD 0.25).
3. The owner starts the server and asks 10 questions written in advance in the plan, covering each agent, a link question ("give me the link to apply online"), an FAQ answer, a semester course and a staff email. Each answer is checked against the live website.

## 5. API Contract

```
python -X utf8 tools/prepare_rebuild.py             # report only
python -X utf8 tools/prepare_rebuild.py --apply     # back up and move old caches
python -X utf8 tools/data_quality_report.py         # after the rebuild
```

Exit codes:
- `prepare_rebuild.py`: `0` done (or report shown), `1` a pre-check failed and nothing changed.
- `data_quality_report.py`: `0` every check passed, `2` at least one check failed (the report is still written), `1` the report could not run (for example, `data/` is missing).

`quality.json` shape:
```
{
  "checks": {"page_coverage": {"status": "PASS", "details": [...]}, ...},
  "pages":  [{"address": "...", "status": "stored", "category": "...", "chars": 0}],
  "pdfs":   [{"address": "...", "status": "stored", "pages_total": 0,
              "pages_text": 0, "pages_ocr": 0, "pages_skipped": 0, "chars": 0}],
  "categories": {"admissions": {"pages": 0, "parents": 0, "chunks": 0}, ...}
}
```

## 6. Constraints

- New files only: `tools/prepare_rebuild.py`, `tools/data_quality_report.py`, their tests, and the `.gitignore` lines. No change to the scraper, knowledge base, proposition, Graph RAG or search code.
- Claude never runs `setup_knowledge_base.py`, `setup_propositions.py`, `build_graph_rag.py`, the server, or the semantic cache command. The owner runs each one, in order, and shares the result before the next.
- Claude may run `prepare_rebuild.py` without `--apply`, `data_quality_report.py` and the offline tests.
- No step deletes anything. Old data is moved into `backups/`, so every step can be undone.
- Tests are offline and use a small fake `data/` folder in a temporary directory, never the real one.

## 7. Edge Cases and Error Handling

| Case | Behaviour |
|---|---|
| A cache file is already missing | Report it as "not present" and continue. |
| The backup copy does not match the original | Stop before moving anything. |
| Not enough disk space for the backup | Stop before changing anything. |
| The rebuild stops halfway (network, rate limit, Ctrl+C) | The report shows exactly which pages and PDFs are missing. The owner reruns `setup_knowledge_base.py --force`. The backup from Phase 1 stays untouched. |
| The website answers 503 during the rebuild | The scraper's own retry and delay rules apply. Pages that still fail appear in the report as failed. |
| A log file is missing | The report runs without it, marks the reason fields "log not available", and fails the PDF coverage check. |
| Valkey is not running | The semantic cache step is skipped and noted. The cache is empty anyway. |
| The report fails | Phase 4 does not start. The failures are reviewed and fixed in a follow-up spec, then the rebuild is repeated. |
| The rebuild must be undone | The plan gives the exact steps to restore `data/` from `backups/data_<date-time>/`. |

## 8. Acceptance Criteria

1. `prepare_rebuild.py` without `--apply` changes nothing (checked by file times and sizes in a test).
2. `prepare_rebuild.py --apply` creates a verified backup, moves every old cache and `data/graph_rag/` into `backups/`, and refuses to run when a pre-check fails (tests on a fake `data/` folder).
3. `.gitignore` contains `backups/` and `scratch/`.
4. Offline tests for both tools pass, and `python -m pytest tests/ -q` stays green.
5. After the owner's rebuild, `data_quality_report.py` exits `0`: every check in 4.3 is PASS.
6. Every one of the 12 PDFs shows 0 skipped pages.
7. Every category in `config.CATEGORIES` has content, and every source has its intended category.
8. The semantic cache is empty and Graph RAG is rebuilt from the new data.
9. Human review: the owner reads `quality_report.md` and `sample_chunks.md`, and the 10 test questions get correct answers that match the live website.
10. The undo steps are written in the plan and were checked on the fake `data/` folder.
