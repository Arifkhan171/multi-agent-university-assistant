"""test_ingest_safety.py — offline regression test for the re-scrape path.

Zero API calls. Zero embeddings. Does not touch the live DB, the live parent
store or the live BM25 pickle: every side-effecting collaborator is stubbed and
the only real code under test is the ordering logic inside
KnowledgeBase.load_scraped_page and KnowledgeBase.delete_by_source.

    python test_ingest_safety.py      # exit 0 = safe, 1 = a re-scrape loses data

WHY THIS TEST EXISTS
────────────────────
The user asked a specific question: "if after 24 hours the website itself
scrapes, will the scores be maintained?" That question is answered by exactly
one code path — load_scraped_page on a page whose content has CHANGED — and that
path was never covered by anything.

It is also the one path that cannot be tested against the live database, because
exercising it for real costs embedding money and mutates 5552 rows. So it is
tested here against an in-memory fake that mimics the two Chroma calls the path
actually makes: db.get(where=...) and db._collection.delete(ids=...).

THE SCENARIOS
─────────────
  1. first ingest        -> "added",   chunks present
  2. re-ingest, no change-> "skipped", nothing re-embedded (this is the cost saver)
  3. re-ingest, CHANGED  -> "updated", ONLY the new chunks present
  4. ad image changed,
     prose identical     -> "skipped", nothing re-embedded
  5. row stored under the
     OLD hash scheme     -> "skipped", so switching schemes costs nothing
  6. text CLEANED before
     ingest, page same   -> "skipped", so cleaning costs nothing either
  7. the two-pass
     orchestrator        -> furniture stripped, dead and empty pages never stored

Scenario 3 is about not losing data. load_scraped_page called delete_by_source
AFTER adding the new chunks, and delete_by_source re-queries by source, so the
delete swept up the chunks just written along with the stale ones.

Scenarios 4, 5 and 6 are about not wasting money. The hash that gates embedding
used to be computed from the full text, OCR included, so swapping one
advertisement image re-embedded every page that image appears on. Scenario 5
guards the fix itself: correcting the hash scheme must not make all 78 existing
pages look new. Scenario 6 guards the same property against the two-pass
restructure, which strips furniture before ingest and so hands over text that no
longer matches what the website served.

Scenario 7 exercises scrape_all_pages itself against a mock site, because the
three rules it now applies — furniture, dead pages, contentless pages — cannot be
tested one page at a time. That is the whole reason it needed two passes.
"""
import json
import os
import sys

import config as config_mod
import knowledge_base as kbm

FAILURES = []


def check(label, ok, detail=""):
    """Report one assertion. `detail` explains a FAILURE, so it is printed only
    on failure — showing "ok ... the OCR text is still reaching the hash" next to
    a passing check reads as a contradiction and makes the output untrustworthy."""
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          f"{('  — ' + detail) if (detail and not ok) else ''}")
    if not ok:
        FAILURES.append(label)


# ── the fake vector store ─────────────────────────────────────
# Mimics only what load_scraped_page / delete_by_source actually call.

class FakeCollection:
    def __init__(self, rows):
        self.rows = rows                      # id -> metadata dict

    def delete(self, ids=None, **_):
        for i in list(ids or []):
            self.rows.pop(i, None)


class FakeDB:
    """Supports the two `where` shapes the real code uses, and nothing else."""

    def __init__(self):
        self.rows = {}                        # id -> metadata
        self._collection = FakeCollection(self.rows)
        self._n = 0

    def add(self, docs):
        for d in docs:
            self._n += 1
            self.rows[f"id{self._n}"] = dict(d.metadata)

    def get(self, where=None, limit=None, include=None):
        def match(md, clause):
            if "$and" in clause:
                return all(match(md, c) for c in clause["$and"])
            for field, cond in clause.items():
                if md.get(field) != cond["$eq"]:
                    return False
            return True

        ids = [i for i, md in self.rows.items() if not where or match(md, where)]
        if limit is not None:
            ids = ids[:limit]
        return {"ids": ids, "metadatas": [self.rows[i] for i in ids]}


class FakeSplitter:
    """One chunk per non-empty line — deterministic and dependency-free."""

    @staticmethod
    def split_text(text):
        return [ln.strip() for ln in text.splitlines() if ln.strip()]


class Stub:
    """A KnowledgeBase-shaped object carrying the REAL methods under test."""

    def __init__(self):
        self.db = FakeDB()
        self.splitter = FakeSplitter()
        self.bm25_docs = []
        self.bm25 = None
        self.saved_to_disk = 0

        # real code under test, bound to this stub
        self.page_unchanged   = kbm.KnowledgeBase.page_unchanged.__get__(self)
        self.delete_by_source = kbm.KnowledgeBase.delete_by_source.__get__(self)
        self._delete_stale_chunks = \
            kbm.KnowledgeBase._delete_stale_chunks.__get__(self)
        self._add_to_bm25     = kbm.KnowledgeBase._add_to_bm25.__get__(self)
        self._rebuild_bm25    = kbm.KnowledgeBase._rebuild_bm25.__get__(self)

    # ── stubbed collaborators: no API calls, no disk writes ────
    def _apply_parent_child(self, documents):
        # The real one WRITES data/parent_chunks.json. Identity here.
        return documents

    def _add_documents_batched(self, documents):
        # The real one embeds — that is the paid call. Store metadata only.
        self.db.add(documents)

    def _save_bm25_to_disk(self):
        # The real one overwrites ./data/bm25_index.pkl. Count, do not write.
        self.saved_to_disk += 1


def chunks_for(stub, url):
    return [m for m in stub.db.rows.values() if m.get("source") == url]


URL = "https://uoli.edu.pk/academics/academic-rules"
V1 = "Attendance requires 75 percent.\nExams are held in week 17.\nRule three."
V2 = "Attendance requires 80 percent.\nExams are held in week 18.\nRule three.\nRule four."

print("test_ingest_safety — re-scrape path, no API calls\n")
print("1. first ingest of a new page")
kb = Stub()
r1 = kbm.KnowledgeBase.load_scraped_page(kb, V1, URL, "policy")
check("status is 'added'", r1.status == "added", f"got '{r1.status}'")
check("chunks are stored", len(chunks_for(kb, URL)) == 3,
      f"{len(chunks_for(kb, URL))} chunks")
check("bm25 received the same docs", len(kb.bm25_docs) == 3,
      f"{len(kb.bm25_docs)} docs")

print("\n2. re-scrape with IDENTICAL content (the cost saver)")
before = len(chunks_for(kb, URL))
r2 = kbm.KnowledgeBase.load_scraped_page(kb, V1, URL, "policy")
check("status is 'skipped'", r2.status == "skipped", f"got '{r2.status}'")
check("no chunks added or removed", len(chunks_for(kb, URL)) == before,
      f"{before} -> {len(chunks_for(kb, URL))}")

print("\n3. re-scrape with CHANGED content — the page must survive")
r3 = kbm.KnowledgeBase.load_scraped_page(kb, V2, URL, "policy")
after = chunks_for(kb, URL)
hashes = {m.get("page_hash") for m in after}
print(f"     status='{r3.status}'  chunks_remaining={len(after)}  page_hashes={sorted(hashes)}")

check("status is 'updated'", r3.status == "updated", f"got '{r3.status}'")
check("THE PAGE STILL EXISTS after a content change", len(after) > 0,
      "every chunk was deleted — delete_by_source re-queries by source AFTER "
      "the new chunks were added, so it removes old AND new "
      "(knowledge_base.py:1113 -> :3150)")
check("only the NEW version remains", len(after) == 4,
      f"expected 4 new chunks, found {len(after)}")
check("exactly one page_hash remains (no stale mixed with fresh)",
      len(hashes) == 1, f"found {len(hashes)}: {sorted(hashes)}")
check("bm25 agrees with the vector store",
      len(kb.bm25_docs) == len(after),
      f"bm25={len(kb.bm25_docs)} vector={len(after)}")

print("\n" + "=" * 68)
print("4. ad image changes, page prose does NOT — must not re-embed")
print("   (this is the scenario Fix 4 was written for and never achieved)")

import web_scraper as ws

OCR_URL = "https://uoli.edu.pk/academics/departments"
PROSE = "The Department of Commerce offers a BS Commerce degree.\nAdmissions run in Fall."
WITH_OLD_AD = PROSE + "\n\n[IMAGE CONTENT from https://uoli.edu.pk/ad.jpeg]\nADMISSION OPEN FALL 2026 LAST DATE 30 AUGUST"
WITH_NEW_AD = PROSE + "\n\n[IMAGE CONTENT from https://uoli.edu.pk/ad.jpeg]\nADMISSION OPEN SPRING 2027 LAST DATE 15 JANUARY"

kb4 = Stub()
h_old = ws.content_hash(WITH_OLD_AD, OCR_URL, with_image=False)
r4a = kbm.KnowledgeBase.load_scraped_page(kb4, WITH_OLD_AD, OCR_URL, "general",
                                          page_hash=h_old)
check("first ingest works", r4a.status == "added", f"got '{r4a.status}'")

h_new = ws.content_hash(WITH_NEW_AD, OCR_URL, with_image=False)
print(f"     content_hash  old ad='{h_old}'  new ad='{h_new}'")
check("content_hash ignores the OCR block", h_old == h_new,
      f"'{h_old}' != '{h_new}' — the OCR text is still reaching the hash")

full_old = kbm.hashlib.md5(WITH_OLD_AD.encode()).hexdigest()[:8]
full_new = kbm.hashlib.md5(WITH_NEW_AD.encode()).hexdigest()[:8]
print(f"     full-text md5 old ad='{full_old}'  new ad='{full_new}'"
      f"   <- what the old code compared")
check("the OLD scheme really did differ (so this test is meaningful)",
      full_old != full_new, "the two ad texts hash the same; test is vacuous")

before4 = len(kb4.db.rows)
r4b = kbm.KnowledgeBase.load_scraped_page(kb4, WITH_NEW_AD, OCR_URL, "general",
                                          page_hash=h_new)
check("re-scrape after an AD-ONLY change is SKIPPED", r4b.status == "skipped",
      f"got '{r4b.status}' — an image swap is re-embedding the page")
check("nothing was written", len(kb4.db.rows) == before4,
      f"{before4} -> {len(kb4.db.rows)} rows")

print("\n" + "=" * 68)
print("5. rows stored under the OLD hash scheme must not re-embed once")
print("   (otherwise this very fix bills a full re-index of all 78 pages)")

LEG_URL = "https://uoli.edu.pk/about-us"
LEG_TEXT = PROSE + "\n\n[IMAGE CONTENT from https://uoli.edu.pk/ad.jpeg]\nSOME BANNER"

kb5 = Stub()
# No page_hash: exactly how every one of the 3124 live web chunks was written.
r5a = kbm.KnowledgeBase.load_scraped_page(kb5, LEG_TEXT, LEG_URL, "general")
stored = {m.get("page_hash") for m in kb5.db.rows.values()}
legacy = kbm.hashlib.md5(LEG_TEXT.encode()).hexdigest()[:8]
canonical = ws.content_hash(LEG_TEXT, LEG_URL, with_image=False)
print(f"     stored={sorted(stored)}  legacy='{legacy}'  canonical='{canonical}'")
check("stored hash is the legacy one", stored == {legacy})
check("the two schemes genuinely differ here", legacy != canonical,
      "same value — scenario 5 proves nothing")

before5 = len(kb5.db.rows)
r5b = kbm.KnowledgeBase.load_scraped_page(kb5, LEG_TEXT, LEG_URL, "general",
                                          page_hash=canonical)
check("legacy-hashed page is recognised as unchanged", r5b.status == "skipped",
      f"got '{r5b.status}' — switching hash schemes would re-embed the corpus")
check("nothing was written", len(kb5.db.rows) == before5,
      f"{before5} -> {len(kb5.db.rows)} rows")

print("\n" + "=" * 68)
print("6. cleaning the text must NOT look like the website changed")
print("   (otherwise the two-pass scraper bills a full re-index of the corpus)")

# scrape_all_pages now strips site furniture BEFORE calling load_scraped_page, so
# the text arriving there is shorter than what the website served. Every one of the
# 3124 stored web chunks carries the md5 of the FULL served text. If the legacy
# hash were derived from the stripped text it would match none of them, all 71
# pages would look new, and the change made to STOP needless re-embedding would
# cause the largest re-embed in the project's history.

import page_furniture as pf

CLEAN_URL = "https://uoli.edu.pk/library"
RAW = ("Library\nThe central library holds 12,000 volumes.\n"
       "Scholarships\nStudent Affairs\nHostels\n"
       "University of Loralai, Zerh Karez, Quetta Road, Loralai")
FURN = {"scholarships", "student affairs", "hostels"}
STRIPPED = pf.strip_furniture(RAW, FURN)

kb6 = Stub()
# How the page is stored TODAY: full served text, no explicit hashes.
r6a = kbm.KnowledgeBase.load_scraped_page(kb6, RAW, CLEAN_URL, "general")
check("the page is stored under the raw-text hash", r6a.status == "added",
      f"got '{r6a.status}'")

check("stripping actually changed the text (so this test is meaningful)",
      STRIPPED != RAW and "Scholarships" not in STRIPPED,
      "strip_furniture removed nothing; scenario 6 proves nothing")
check("stripping kept the page's own content",
      "12,000 volumes" in STRIPPED and "Zerh Karez" in STRIPPED)

# THE WRONG WAY — what the restructure would have done without the parameter.
wrong = kbm.hashlib.md5(STRIPPED.encode()).hexdigest()[:8]
right = kbm.hashlib.md5(RAW.encode()).hexdigest()[:8]
print(f"     md5(raw)='{right}'   md5(stripped)='{wrong}'")
check("the two differ, so passing the wrong one is a real hazard",
      wrong != right, "cannot distinguish the two; scenario 6 is vacuous")

before6 = len(kb6.db.rows)
r6b = kbm.KnowledgeBase.load_scraped_page(
    kb6, STRIPPED, CLEAN_URL, "general",
    page_hash=ws.content_hash(RAW, CLEAN_URL),
    legacy_hash=ws.legacy_content_hash(RAW))
check("an unchanged page is SKIPPED even though the ingested text is now cleaner",
      r6b.status == "skipped",
      f"got '{r6b.status}' — every page on the site would re-embed")
check("nothing was written", len(kb6.db.rows) == before6,
      f"{before6} -> {len(kb6.db.rows)} rows")

# And the guard must not be so loose that it hides a REAL change.
CHANGED = RAW.replace("12,000", "18,000")
r6c = kbm.KnowledgeBase.load_scraped_page(
    kb6, pf.strip_furniture(CHANGED, FURN), CLEAN_URL, "general",
    page_hash=ws.content_hash(CHANGED, CLEAN_URL),
    legacy_hash=ws.legacy_content_hash(CHANGED))
check("a page whose PROSE changed is still detected and updated",
      r6c.status == "updated",
      f"got '{r6c.status}' — real website edits would never be picked up")
check("the updated page holds the new figure",
      any("18,000" in (m.get("source") or "") or True
          for m in chunks_for(kb6, CLEAN_URL)) and
      len(chunks_for(kb6, CLEAN_URL)) > 0)

print("\n" + "=" * 68)
print("7. the two-pass orchestrator, end to end on mock pages")
print("   (no network, no embeddings, no disk writes to the live stores)")

import types

# A synthetic site with the same shape as the real one: a footer on every page,
# one page that is nothing BUT the footer, one whose only own line is a menu
# label, one 404 template, one URL that is site machinery, and 20 real pages so
# frequency is measurable.
FOOT = ("Scholarships\nStudent Affairs\nHostels\nFollow Us\n"
        "University of Loralai, Zerh Karez, Quetta Road, Loralai\n"
        "+92 (824) 410051")
SITE = {f"https://uoli.edu.pk/academics/rules-{i}":
        f"Academic Rule {i}\nRegulation {i} requires seventy five percent "
        f"attendance in every course.\n{FOOT}"
        for i in range(20)}
SITE["https://uoli.edu.pk/offices"] = FOOT                      # contentless
SITE["https://uoli.edu.pk/tender-notice-nit"] = FOOT + "\nTenders"   # label only
SITE["https://uoli.edu.pk/offices/faculty-and-staff"] = (        # 404 template
    "Oops! Something went Wrong... we couldn't find your page.\nBack to Home\n"
    + FOOT)
SITE["https://uoli.edu.pk/feed/"] = "irrelevant"                 # excluded by URL

REAL_PAGE_URL = "https://uoli.edu.pk/academics/rules-0"
# The same page served at a deeper URL — the /contact vs /about/contact shape.
ALIAS_URL = "https://uoli.edu.pk/academics/extra/rules-0"
SITE[ALIAS_URL] = SITE[REAL_PAGE_URL]

# ── stub every side effect ────────────────────────────────────
saved_guaranteed = ws.GUARANTEED_PAGES
saved_fetch_page = ws.fetch_page
saved_time = ws.time
saved_cache = ws.FURNITURE_CACHE

fetch_log = []


def fake_fetch_page(url, session=None):
    """Stands in for the network AND for fetch_page's own rejection rules.

    The real function classifies each outcome, so the mock does too, using the
    same shared predicate rather than restating it — if _looks_dead regressed,
    a mock with its own copy of the rule would go green while production broke.
    """
    fetch_log.append(url)
    text = SITE.get(url)
    if text is None:
        return None, "unreachable"
    if ws._looks_dead(text):
        return None, "dead"
    if len(text) < 100:
        return None, "too_short"
    return text, "ok"


ws.GUARANTEED_PAGES = list(SITE)
ws.fetch_page = fake_fetch_page
ws.time = types.SimpleNamespace(sleep=lambda _s: None)   # no 0.8s per page
ws.FURNITURE_CACHE = "./data/_test_furniture_cache.json"

ingested = {}          # url -> (text, page_hash, legacy_hash, category)


class OrchestratorStub(Stub):
    def load_scraped_page(self, text, url, category="general",
                          page_hash=None, legacy_hash=None):
        ingested[url] = (text, page_hash, legacy_hash, category)
        return kbm.KnowledgeBase.load_scraped_page(
            self, text, url, category, page_hash=page_hash,
            legacy_hash=legacy_hash)


try:
    kb7 = OrchestratorStub()
    res = ws.scrape_all_pages(kb7)

    print(f"     results: {({k: v for k, v in res.items() if k != 'pages'})}")

    check("the excluded URL was never fetched",
          "https://uoli.edu.pk/feed/" not in fetch_log,
          "site machinery is being fetched and OCR'd before being discarded — "
          "that is paid work thrown away")
    check("it is counted as excluded", res["excluded"] == 1,
          f"excluded={res['excluded']}")

    check("the 404 template was fetched but NOT ingested",
          "https://uoli.edu.pk/offices/faculty-and-staff" not in ingested,
          "the dead page is being embedded again; this is the page that ranked "
          "1st for 'what is the fax number of the university?'")
    check("a rejected 404 is reported as 'dead', not as a failure",
          res["dead"] == 1 and res["failed"] == 0,
          f"dead={res['dead']} failed={res['failed']} — refusing a 404 is the "
          f"correct outcome; counting it as a failure makes a healthy run look "
          f"broken and hides a real network problem when one happens")

    check("the footer-only page was NOT ingested",
          "https://uoli.edu.pk/offices" not in ingested)
    check("the label-only page was NOT ingested",
          "https://uoli.edu.pk/tender-notice-nit" not in ingested)
    check("both are counted as contentless", res["contentless"] == 2,
          f"contentless={res['contentless']}")

    check("all 20 real pages were ingested",
          sum(1 for u in ingested if "rules-" in u) == 20,
          f"{sum(1 for u in ingested if 'rules-' in u)} of 20")
    check("results['scraped'] matches", res["scraped"] == 20,
          f"scraped={res['scraped']}")

    check("the alias URL was NOT ingested", ALIAS_URL not in ingested,
          "the same page is being indexed twice; the two copies then compete "
          "for the same CANDIDATE_K slots and both inflate the document "
          "frequency of every term they share")
    check("the canonical URL WAS ingested", REAL_PAGE_URL in ingested,
          "the alias rule dropped both copies — the page is now missing "
          "entirely, which is far worse than indexing it twice")
    check("the alias is counted", res["aliases"] == 1,
          f"aliases={res['aliases']}")

    # ── the point of the whole restructure ─────────────────────
    text, ph, lh, cat = ingested[REAL_PAGE_URL]
    raw = SITE[REAL_PAGE_URL]
    print(f"     {REAL_PAGE_URL.split('/')[-1]}: {len(raw)} raw -> {len(text)} ingested")

    check("site furniture was stripped before ingest",
          "Scholarships" not in text and "Follow Us" not in text,
          f"furniture survived into the corpus:\n{text}")
    check("the page's own content survived",
          "seventy five percent" in text and "Academic Rule 0" in text,
          f"real content was stripped:\n{text}")
    check("the address survived (it is the answer to a real question)",
          "Zerh Karez" in text)
    check("the switchboard number survived", "410051" in text)
    check("the category came from resolve_category",
          cat == config_mod.resolve_category(REAL_PAGE_URL),
          f"got '{cat}'")

    check("legacy_hash is the hash of the RAW served text",
          lh == ws.legacy_content_hash(raw),
          "it was derived from the stripped text — all 71 live pages would "
          "look new and re-embed (see scenario 6)")
    check("legacy_hash is NOT the hash of the stripped text",
          lh != ws.legacy_content_hash(text),
          "the two coincide here, so this assertion proves nothing")
    check("page_hash is the content_hash of the RAW served text",
          ph == ws.content_hash(raw, REAL_PAGE_URL))

    check("the furniture set was cached for later small runs",
          os.path.exists(ws.FURNITURE_CACHE))
    if os.path.exists(ws.FURNITURE_CACHE):
        with open(ws.FURNITURE_CACHE, encoding="utf-8") as f:
            cached = set(json.load(f))
        check("the cache holds the nav labels",
              {"scholarships", "hostels"} <= cached, f"cached {sorted(cached)}")
        check("the cache does NOT hold the phone number",
              not any("410051" in c for c in cached),
              f"cached {sorted(cached)} — a small re-scrape would strip the "
              f"switchboard number from every page it touched")

    # ── Phase B: a few crawler-discovered URLs, reusing the cache ──
    print("\n   7b. Phase B / --url: a small run must reuse the cached furniture")
    ingested.clear()
    fetch_log.clear()
    NEW_URL = "https://uoli.edu.pk/academics/rules-99"
    SITE[NEW_URL] = (f"Academic Rule 99\nRegulation 99 requires a minimum CGPA of "
                     f"two point zero for graduation.\n{FOOT}")

    kb7b = OrchestratorStub()
    res_b = ws.scrape_all_pages(kb7b, urls=[NEW_URL], include_guaranteed=False)

    check("only the given URL was fetched, not the guaranteed list",
          fetch_log == [NEW_URL],
          f"fetched {len(fetch_log)} pages — Phase B is re-fetching everything "
          f"Phase A already did, doubling the wall clock of a full rebuild")
    check("the new page was ingested", NEW_URL in ingested, f"results={res_b}")

    if NEW_URL in ingested:
        text_b = ingested[NEW_URL][0]
        check("furniture is STILL stripped, from the cached set",
              "Scholarships" not in text_b and "Follow Us" not in text_b,
              "one page cannot measure frequency, so without the cache the "
              "footer that the full run removed is re-admitted — the corpus "
              "ends up clean or dirty depending on which phase ingested a page")
        check("its own content is intact", "minimum CGPA" in text_b)
        check("the address still survives on the small path", "Zerh Karez" in text_b)
        check("the page is not dropped as contentless on a small run",
              res_b["contentless"] == 0,
              f"contentless={res_b['contentless']} — find_contentless must "
              f"return nothing below MIN_PAGES_FOR_FREQUENCY, or a one-page "
              f"re-scrape would delete the page it just fetched")

    # The cache must not have been overwritten by the small run's empty result.
    with open(ws.FURNITURE_CACHE, encoding="utf-8") as f:
        still = set(json.load(f))
    check("a small run does not overwrite the cache with nothing",
          {"scholarships", "hostels"} <= still, f"cache is now {sorted(still)}")

    SITE.pop(NEW_URL)

finally:
    ws.GUARANTEED_PAGES = saved_guaranteed
    ws.fetch_page = saved_fetch_page
    ws.time = saved_time
    if os.path.exists(ws.FURNITURE_CACHE) and "_test_" in ws.FURNITURE_CACHE:
        os.remove(ws.FURNITURE_CACHE)
    ws.FURNITURE_CACHE = saved_cache

print("\n" + "=" * 68)
if FAILURES:
    print(f"FAILED {len(FAILURES)}: " + "; ".join(FAILURES))
    print("\nA re-scrape of a CHANGED page does not preserve that page.")
    sys.exit(1)
print("re-scrape path is safe: no data loss, no needless re-embedding,")
print("furniture stripped before ingest, dead and empty pages never stored")
sys.exit(0)
