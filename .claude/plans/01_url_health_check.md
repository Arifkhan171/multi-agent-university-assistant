# Plan 01 — URL Health Check

Spec: `.claude/specs/01_url_health_check.md`
Branch: `feature/01-url-health-check`

Only new files. No existing file is edited.

## Steps (small, in order)

1. **Scaffolding** — add `tools/__init__.py` (empty), `tests/conftest.py` (adds project folder to path), `tests/` folder.
2. **Link finder** — scan `.py` files (skip `test_*.py`, `tests/`, `data/`, `.claude/`, `tools/`) for `uoli.edu.pk` links.
   - Keep only real web links (`http(s)://...` or `www.`); ignore email addresses such as `x@uoli.edu.pk`, and bare domain mentions in comments.
   - Record file + line for each.
   - Guess type from the ending: `.pdf` = pdf, image extensions = image, otherwise page.
3. **Link cleaner** — treat `http/https`, `www.`, and trailing `/` as the same link; one row per link, keeping every place it was found.
4. **Fake-address probe** — open 2 made-up addresses first, remember what the "Not found" page looks like (status + visible text).
5. **Checker** — one link at a time, ≥1 s apart:
   - `requests` follows redirects, notes the final address (loop → DEAD).
   - Pages are also opened in Playwright; read visible text and compare with the learned "Not found" look.
   - 403/429 → wait, retry once, then NEEDS_REVIEW. Timeout → 2 more tries, then ERROR.
   - Result rules exactly as in spec section 3.5.
6. **Report writer** — CSV + summary markdown in `reports/url_health/`. Ctrl+C saves what is done so far. Warn in summary if the fake-page look could not be learned.
7. **CLI** — `--limit N`, `--type page|pdf|image|all`.
8. **Offline tests** (`tests/test_url_health_check.py`, no internet): link finder (ignores emails), link cleaner, result rules with fake responses, timeout → ERROR, report columns.

## What Claude runs
- Offline tests only.
- A 3-link live test only after your OK.

## What you run
- Full check: `python tools/url_health_check.py` (about 10–15 min).
- Then open 3 links from each result group in your browser to confirm.

## Git
Commit new files on this branch. Tell you if push is needed, then PR and merge.
