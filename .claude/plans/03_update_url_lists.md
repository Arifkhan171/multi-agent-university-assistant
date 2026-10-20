# Plan 03 — Update the Page and PDF Lists in Code

Spec: `.claude/specs/03_update_url_lists.md`
Branch: `feature/03-update-url-lists`
(After you approve, I copy this file to `.claude/plans/03_update_url_lists.md`.)

## Context

The code still holds old WordPress links in `config.py`, `web_scraper.py` and `graph/Agents.py`. You made the final lists (`url_lists/pages.csv`: 111 rows, 110 kept; `url_lists/pdfs.csv`: 18 rows, 12 kept). A small tool copies those lists into `config.py`, so no link is typed by hand. The few links inside `Agents.py` move into `config.py` with new addresses that you approve in the table below.

Files changed: `config.py`, `web_scraper.py`, `graph/Agents.py`, `setup_knowledge_base.py` (help text only).
Changes in `graph/Agents.py`: the link values, plus the faculty filter fix at line ~1284 (your added item).
New files: `tools/build_url_lists.py`, `tests/test_build_url_lists.py` (the CSVs are already in `url_lists/`).

---

## Table for you to approve — links in `graph/Agents.py`

Every new address is in `pages.csv`. Nothing else in the code changes, only the link values.

### `_OFFICE_URL` (line ~387)

| Key(s) | Old link | New link | Why |
|---|---|---|---|
| controller, exam control | `/offices/controller-of-examination-office` | `/offices/controller-examination` | new office page |
| treasurer | `/office-of-treasurer` | `/offices/treasurer` | new office page |
| registrar | `/office-of-registrar` | `/offices/registrar` | new office page |
| oric | `/oric` | `/offices/oric` | office page. (`/research/oric` also exists; I use the office one, as in the spec) |
| directorate | `/directorate-of-it` | `/offices/directorate-of-it` | new office page. The old comment "Not /offices/directorate-of-it" is now wrong, so I rewrite it |
| fao | `/offices/fao` | `/services/scholarships` | FAO now lives inside the scholarships page |
| vc + 5 spellings | `/vc-message` | `/about/vc-message` | moved under /about |
| pro vc, pro-vc | `/pro-vc-message` | `/about/pro-vc-message` | moved under /about |
| dean | `/dean-message` | `/about/dean-message` | moved under /about |

### `_DEPT_URL` (inside `_get_faculty_docs_raw`, line ~1089)

| Keys | Old link | New link |
|---|---|---|
| cs, computer science | `/faculty/computer-science` | `/academics/departments/computer-science` |
| commerce, english, education, pashto, zoology | `/faculty/<name>` | `/academics/departments/<same name>` |
| mathematics, math | `/faculty/mathematics` | `/academics/departments/mathematics` |
| islamic, islamiyat | `/faculty/islamic-studies` | `/academics/departments/islamic-studies` |
| political | `/faculty/political-science` | `/academics/departments/political-science` |
| management | `/faculty/management-sciences` | `/academics/departments/management-sciences` |
| allied, health sciences | `/faculty/allied-health-sciences` | `/academics/departments/allied-health-sciences` |

All 11 departments have the same name on the new site.

### `_LEADERSHIP_PAGES` (line ~1123)

| Old link | New link |
|---|---|
| `/vc-message`, `/dean-message`, `/pro-vc-message`, `/registrar-message` | `/about/vc-message`, `/about/dean-message`, `/about/pro-vc-message`, `/about/registrar-message` |
| `/office-of-registrar`, `/office-of-treasurer` | `/offices/registrar`, `/offices/treasurer` |
| `/about-us` | `/about` |

### `_contact_urls` (line ~1594)

| Old link | New link | Why |
|---|---|---|
| `/contact` | `/contact` | same |
| `/contacts-old-new` | removed | the spec says drop it |
| `https://uoli.edu.pk/` | `https://uoli.edu.pk` | home, written the same way as in the CSV. Lookup still finds both forms (`_docs_by_source`) |

### Fee fallback list (line ~2386)

| Old link | New link |
|---|---|
| `/university-apply` | `/admissions/how-to-apply` |
| `/admissions-open-fall-2026` | `/admissions/fees` |
| `/admission` | `/admissions` |

No link is left without a new page.

---

## Steps (small, in order)

1. **Tool `tools/build_url_lists.py`**
   - Reads both CSVs (UTF-8 with or without BOM, removes spaces around values; Python `csv` handles the multi-line note in the `.xlsx` row).
   - Checks: required columns exist; address starts with `https://uoli.edu.pk`, no `?` or `#`, no `/` at the end (home only may); no address twice (compared with `config._url_path`, so `/x` and `/x/` count as the same); category is in `config.CATEGORIES`; keep is `yes`/`no`; every kept PDF has a label. Each error names the file, row number and value.
   - `--check`: prints errors and counts (rows, kept, per category).
   - `--write`: runs the check, then replaces only the lines between `# BEGIN URL LISTS` and `# END URL LISTS`. It stops if the markers are missing or appear twice. It keeps the file's line endings. Running it twice gives the same file.
   - Output shape is the same as today: `UNIVERSITY_PAGES = [{"url": ..., "category": ...}, ...]` and `PDF_SOURCES = [{"url": ..., "category": ..., "label": ...}, ...]`, in CSV order, with a "made by the tool, do not edit by hand" line at the top.
   - No library beyond the standard one. It never goes online.

2. **`config.py`: add markers.** Replace the old `UNIVERSITY_PAGES` and `PDF_SOURCES` with an empty marker block. Above the markers I keep the big header comment and the three "why" comments (home = general, contact = general, registrar = overview). Old comments about deleted pages (fee-structure, downloads, crawler-missed PDFs) go away with those pages. Then I run `python tools/build_url_lists.py --write` (offline, safe) to fill the block.

3. **`config.py`: link tables from Agents.** Below the markers, add `OFFICE_URLS`, `DEPT_URLS`, `LEADERSHIP_PAGES`, `CONTACT_PAGES`, `FEE_FALLBACK_PAGES` with the values in the tables above. The old "why" comments go with them.

4. **`config.py`: address rules for the new layout** (in `_structural_category`, rules only added). The CSV category always wins, because declarations are checked first.
   - `/academics/departments/...` and `/academics/faculty` → faculty
   - `/academics/programs/...`, `/academics/undergraduate`, `/academics/graduate` → admissions
   - `/research/oric` → offices

   `/about/...`, `/offices/...`, `/admissions/...` and `/contact` are already covered by today's rules. `/news`, `/campus-life` and `/services` already fall back to general.

5. **`graph/Agents.py`**: `_OFFICE_URL = config.OFFICE_URLS`, and the same for the other four (local names stay, so no other line changes). `_ROLE_FALLBACK_TERMS` is not touched.

5b. **`graph/Agents.py` faculty filter (line ~1284).** Today it keeps a doc only if `"/faculty" in source`. The new department pages are at `/academics/departments/<name>`, so they would all be dropped.
   - In `config.py`, next to `DEPT_URLS`, add `FACULTY_LIST_PAGE = "https://uoli.edu.pk/academics/faculty"` (the test checks it is in the page list).
   - In `Agents.py`, add a small module-level helper `_is_faculty_source(source)`. It returns True when the source, normalised with `config._url_path` (so `/x` and `/x/` are the same), is one of the `DEPT_URLS` addresses or `FACULTY_LIST_PAGE`. No link is typed in `Agents.py`.
   - The filter becomes `if _is_faculty_source(d.metadata.get("source", ""))`. Nothing else in that path changes.
   - I do not add `/academics/departments` (the department list page) to this set, because you named only the department links and the faculty list page. Tell me if you want it.

6. **`web_scraper.py`**: `GUARANTEED_PAGES = [p["url"] for p in config.UNIVERSITY_PAGES]` (the name stays). `_NAV_NOISE` and `_AD_IMAGE_URL` are not touched (spec 05).

7. **`setup_knowledge_base.py`**: change the help example to `--url https://uoli.edu.pk/about/vc-message`.

8. **Tests `tests/test_build_url_lists.py`** (offline, temp files):
   - each check error (missing column, duplicate with/without `/`, bad category, bad keep, PDF without label, `?`/`#`, trailing `/`)
   - BOM and spaces are handled
   - `--write` changes only the marker block, is the same when run twice, and stops on missing or double markers
   - `config.UNIVERSITY_PAGES` = kept rows of pages.csv; same for PDFs (acceptance 2)
   - no Proforma PDF and no `.xlsx` link in config (3)
   - `GUARANTEED_PAGES` = config addresses (4)
   - every address in the 5 Agents tables is in the page list (5)
   - no old address in code lines of the 3 files: `/wp-content/`, `/faculty/<name>`, `-message` without `/about/`. The one allowed exception is `_AD_IMAGE_URL` (spec 05) (6)
   - the new address rules give the expected category for sample future pages
   - `_is_faculty_source`: every new department page, with and without `/` at the end, and the faculty list page give True. A news page, `/about/vc-message` and an old `/faculty/<name>` address give False

9. **Report for you** (list only, no fixes):
   - old addresses left only in comments (for example the "~78 GUARANTEED_PAGES" notes in `web_scraper.py`)
   - every place in `knowledge_base.py`, `web_scraper.py` and `page_furniture.py` that depends on the OLD address shape (`/faculty/<name>`, `-message` without `/about/`, `office-of-...`, `/wp-content/`, and similar), with file and line. These are for specs 05 and 06.

---

## Points for you to check (I do not change them)

- `pages.csv` row `/privacy-policy`: category is `general`, but its note says "'policy' is a guess". The CSV wins, so it stays `general` unless you edit the CSV.
- The spec 01 link check reads links from our `.py` files, so it will also report `_AD_IMAGE_URL` (old dead image). That one is expected and belongs to spec 05.

## Verification

- I run (offline): `python tools/build_url_lists.py --check` (0 errors), `python -c "import config"`, `python -m pytest tests/test_build_url_lists.py -v`, `git diff --stat`.
- You run:
  - `python test_page_furniture.py` and `python test_ingest_safety.py`. They should match the "before" result: 2 + 3 known FAIL lines.
  - `python tools/url_health_check.py --type all`. Pass = no DEAD/REDIRECTED/ERROR. NEEDS_REVIEW is fine only for "HTTP 404 but the browser shows content". `_AD_IMAGE_URL` is the known exception.
  - Open 8 pages and 3 PDFs from `config.py` in your browser.
- Git: commit on this branch. Then you push, open the PR and merge (I give the commands).
