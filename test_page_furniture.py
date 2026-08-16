"""test_page_furniture.py — offline test for the structure-vs-content rules.

Zero API calls. Zero embeddings. Writes nothing. Reads the live parent store, but
read-only and only for the final section, which is skipped if the file is absent.

    python test_page_furniture.py     # exit 0 = rules behave, 1 = a rule regressed

WHY THIS TEST EXISTS
────────────────────
page_furniture decides what counts as content. Four things now depend on it, and
each one is destructive or costly if it is wrong:

    repair_parent_text.py       rewrites 132 parent chunks
    repair_false_stats.py       deletes rows from ChromaDB and BM25
    web_scraper.scrape_page     refuses to ingest a page at all
    KnowledgeBase._is_boilerplate  sinks a document in the ranking

The dangerous direction is over-eagerness. Every rule here was written to be
narrower than it could be, for a reason recorded in the module, and a later edit
that widens one would be invisible until scores dropped. Each assertion below
pins a case that must NOT be caught, next to the case that must be.

THE FALSE POSITIVE THAT ALREADY HAPPENED
────────────────────────────────────────
An earlier version let the "this question asks for contact details" exemption
cover dead pages as well as footers. The held-out question "What is the fax
number of the university?" then returned the "Oops! Something went Wrong..." page
at RANK 1. Sections 1 and 2 exist so that cannot come back.
"""
import json
import os
import sys

import page_furniture as pf

FAILURES = []


def check(label, ok, detail=""):
    """Report one assertion. `detail` explains a FAILURE, so it prints only on
    failure — a passing line followed by an explanation of what went wrong reads
    as a contradiction."""
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          f"{('  — ' + detail) if (detail and not ok) else ''}")
    if not ok:
        FAILURES.append(label)


# The real text of https://uoli.edu.pk/offices/faculty-and-staff, 179 chars.
# Kept verbatim rather than paraphrased: the rule is calibrated against this
# exact template, so a paraphrase would test a different string than production.
DEAD = ("Oops! Something went Wrong... we couldn't find your page.\n"
        "Back to Home\n"
        "Scholarships\nStudent Affairs\nHostels\n"
        "University of Loralai, Zerh Karez, Quetta Road, Loralai\n"
        "+92 (824) 410051\nuoli.edu.pk")

FOOTER = ("University of Loralai, Quetta Road, Zerh Karez, Loralai, "
          "Balochistan. Phone: +92 (824) 410782 Email: info@uoli.edu.pk")

REAL_PAGE = ("Department of Computer Science\n"
             "The Department of Computer Science offers a BS in Computer "
             "Science.\nHead of Department: 1054\nhod.cs@uoli.edu.pk")

print("test_page_furniture — structure vs content rules, no API calls\n")

# ── 1. dead pages ─────────────────────────────────────────────
print("1. dead-page detection (must fire on the 404 template, nothing else)")

check("the real error template is detected", pf.looks_dead(DEAD))
check("the site footer is NOT a dead page", not pf.looks_dead(FOOTER),
      "the footer is the only record of the switchboard number; rejecting a "
      "page for carrying one would delete the answer to 'what is the phone "
      "number'")
check("a real department page is NOT a dead page", not pf.looks_dead(REAL_PAGE))
check("empty text is not a dead page", not pf.looks_dead(""))
check("None-safe", not pf.looks_dead(None))

# The window is the whole reason this rule is safe to apply before ingest. A page
# ABOUT error handling must survive; only a page that LEADS with an error dies.
late = ("A" * (pf.DEAD_PAGE_WINDOW + 50)) + " page not found"
check("a marker beyond the window does not condemn the page",
      not pf.looks_dead(late),
      f"a genuine page mentioning an error after char "
      f"{pf.DEAD_PAGE_WINDOW} is being rejected")
check("the same marker INSIDE the window does condemn it",
      pf.looks_dead("page not found"),
      "the window test has stopped working in the other direction, so nothing "
      "is being caught")

# ── 2. one rule, two layers ───────────────────────────────────
print("\n2. the scraper and the ranker must share one definition")
print("   (they diverged once already: see this module's docstring)")

import knowledge_base as kbm
import web_scraper as ws

check("KnowledgeBase._DEAD_PAGE IS page_furniture.DEAD_PAGE",
      kbm.KnowledgeBase._DEAD_PAGE is pf.DEAD_PAGE,
      "knowledge_base has its own copy again — a marker added to one layer "
      "will silently not apply to the other")
check("web_scraper._looks_dead exists (config.py:332 documents it)",
      hasattr(ws, "_looks_dead"))
check("web_scraper._looks_dead delegates to the shared rule",
      ws._looks_dead(DEAD) and not ws._looks_dead(REAL_PAGE))

# The ranking layer must keep treating the two shapes differently. This is the
# exact fax-number regression, asserted at the level that decides it.
K = kbm.KnowledgeBase
FAX = "What is the fax number of the university?"
DEPT = "What departments does UoL have?"
PENALTY = kbm.config.BOILERPLATE_PENALTY

check("a dead page is penalised even when the query asks for contact details",
      K._boilerplate_penalty(FAX, DEAD) == PENALTY,
      "this is the fax-number regression: no query is answered by a page that "
      "failed to load")
check("the footer is EXEMPT when the query asks for contact details",
      K._boilerplate_penalty(FAX, FOOTER) == 0.0,
      "the footer holds the switchboard number and must stay reachable")
check("the footer is penalised for an unrelated query",
      K._boilerplate_penalty(DEPT, FOOTER) == PENALTY)
check("a real page is never penalised",
      K._boilerplate_penalty(DEPT, REAL_PAGE) == 0.0)

# ── 3. navigation labels vs contact facts ─────────────────────
print("\n3. is_navigation_label — the measured discriminator")

FURNITURE = ["Scholarships", "Student Affairs", "Hostels", "News & Events",
             "Follow Us", "We are committed to building trust", "###"]
KEPT = ["University of Loralai, Zerh Karez, Quetta Road, Loralai",
        "uoli.edu.pk", "info@uoli.edu.pk", "+92 (824) 410782",
        "+92 (824) 410051"]

for line in FURNITURE:
    check(f"furniture: {line!r}", pf.is_navigation_label(line))
for line in KEPT:
    check(f"kept: {line!r}", not pf.is_navigation_label(line),
          "a line carrying a digit, an @ or a domain must always be kept — "
          "deleting a duplicate costs precision, deleting the university's "
          "phone number costs a correct answer")

# ── 4. frequency needs enough pages ──────────────────────────
print("\n4. find_furniture — a small re-scrape must change nothing")

one_page = {"https://uoli.edu.pk/about-us": "Scholarships\nHostels\nReal content."}
check("a 1-page run yields NO furniture",
      pf.find_furniture(one_page) == set(),
      "on a single page every line looks like it is on 100% of pages; "
      "setup_knowledge_base.py --url would strip real content")

many = {f"https://uoli.edu.pk/p{i}": "Scholarships\nHostels\n+92 (824) 410782\n"
                                     f"Unique fact number {i}."
        for i in range(pf.MIN_PAGES_FOR_FREQUENCY + 5)}
found = pf.find_furniture(many)
check("a full run detects the repeated nav labels",
      {"scholarships", "hostels"} <= found, f"found {sorted(found)}")
check("the repeated PHONE NUMBER is not treated as furniture",
      not any("410782" in f for f in found),
      f"found {sorted(found)} — frequency alone would delete the switchboard "
      f"number, which appears on 40 of 78 pages")
check("unique facts are never furniture",
      not any("unique fact" in f for f in found))

# ── 4b. the site-wide contact block ──────────────────────────
print("\n4b. find_site_contact_lines — state the facts once, not 65 times")

UNI = "University of Loralai (UOLI)"

check("/contact is the contact page", pf.is_contact_page("https://uoli.edu.pk/contact"))
check("a trailing slash does not hide it",
      pf.is_contact_page("https://uoli.edu.pk/contact/"))
check("the alias /about/contact is ALSO the contact page",
      pf.is_contact_page("https://uoli.edu.pk/about/contact"),
      "find_alias_groups decides /contact and /about/contact are the same page "
      "by comparing their TEXT — strip one and not the other and they differ, "
      "the grouping breaks, and a second copy of the contact page is ingested")
check("a stale lookalike is NOT the contact page",
      not pf.is_contact_page("https://uoli.edu.pk/contacts-old-new"),
      "it has its own distinct phone number (+92 315 8133399) and is a separate "
      "page, not the one the facts are kept on")
check("a department page is not the contact page",
      not pf.is_contact_page("https://uoli.edu.pk/faculty/english"))

check("a phone number is a contact fact",
      pf.looks_like_contact_fact("+92 (824) 410051", UNI))
check("an email is a contact fact",
      pf.looks_like_contact_fact("info@uoli.edu.pk", UNI))
check("a bare domain is a contact fact", pf.looks_like_contact_fact("uoli.edu.pk", UNI))
check("an address naming the university is a contact fact",
      pf.looks_like_contact_fact(
          "University of Loralai, Zerh Karez, Quetta Road, Loralai", UNI),
      "carries no digit, no @ and no domain — the address arm exists for exactly "
      "these two lines, which are also the longest and most costly duplicates")

check("an OFFICE NAME is not a contact fact",
      not pf.looks_like_contact_fact(
          "Office of Research, Innovation, and Commercialisation", UNI),
      "it is on 54 pages because it is in the mega-menu, but it is also the name "
      "of the office and /oric is the page that answers questions about it")
check("a programme with a digit in it is not a contact fact",
      not pf.looks_like_contact_fact("BS Computer Science (4 years)", UNI),
      "_CONTACT_HINT matches ANY digit, which is right for 'do not treat this as "
      "a nav label' and far too loose for 'delete this from 64 pages'")
check("the bare university name is not an address",
      not pf.looks_like_contact_fact("University of Loralai", UNI),
      "no commas: it is the nav label, not the postal address")

# The full rule, on a corpus shaped like the real site.
site = {f"https://uoli.edu.pk/p{i}":
        "Scholarships\n+92 (824) 410051\ninfo@uoli.edu.pk\n"
        "University of Loralai, Zerh Karez, Quetta Road, Loralai\n"
        "Office of Research, Innovation, and Commercialisation\n"
        f"Unique fact number {i}."
        for i in range(pf.MIN_PAGES_FOR_FREQUENCY + 5)}
site["https://uoli.edu.pk/contact"] = (
    "Scholarships\n+92 (824) 410051\ninfo@uoli.edu.pk\n"
    "University of Loralai, Zerh Karez, Quetta Road, Loralai\n"
    "Office of Research, Innovation, and Commercialisation\n"
    "Admin Office: +92 824-410051")

block = pf.find_site_contact_lines(site, university_name=UNI)
check("the phone, email and address are found",
      {"+92 (824) 410051", "info@uoli.edu.pk",
       "university of loralai, zerh karez, quetta road, loralai"} <= block,
      f"found {sorted(block)}")
check("the office NAME is not in the block",
      not any("commercialisation" in b for b in block),
      f"found {sorted(block)} — stripping this site-wide would stop /oric "
      f"saying what it is")
check("a unique fact is never in the block",
      not any("unique fact" in b for b in block))

check("no contact page in the run means no stripping",
      pf.find_site_contact_lines(
          {u: t for u, t in site.items() if not pf.is_contact_page(u)},
          university_name=UNI) == set(),
      "with nowhere for the surviving copy to live, stripping would DELETE the "
      "university's phone number rather than deduplicate it")
check("a small re-scrape strips nothing",
      pf.find_site_contact_lines(
          {"https://uoli.edu.pk/contact": "+92 (824) 410051"},
          university_name=UNI) == set(),
      "frequency is not measurable below MIN_PAGES_FOR_FREQUENCY")

check("the two rules never claim the same line",
      not (pf.find_furniture(site) & block),
      "a line here carries a contact token; find_furniture only accepts lines "
      "that carry none — they are disjoint by construction, and if they ever "
      "overlap one of the two rules has drifted")

# The point of the whole exercise, stated as an assertion.
kept_on_contact = pf.strip_furniture(site["https://uoli.edu.pk/contact"],
                                     pf.find_furniture(site))
kept_elsewhere = pf.strip_furniture(site["https://uoli.edu.pk/p3"],
                                    pf.find_furniture(site) | block)
check("the contact page still states the phone number",
      "410051" in kept_on_contact)
check("every other page no longer repeats it",
      "410051" not in kept_elsewhere)
check("every other page keeps its own content",
      "Unique fact number 3" in kept_elsewhere)

# ── 5. orphan counter numbers ────────────────────────────────
print("\n5. drop_orphan_stats — an unlabelled number is not a fact")

GLANCE = ("5\nState-of-the-Art Computer Labs\n"
          "5\nHostels Accomodation\n"
          "80%\nStudents Supported Through Financial Aid\n"
          "2000+\n"
          "1500+\n"
          "100+\nFaculty\n"
          "10+\nDegree Programs")
out = pf.drop_orphan_stats(GLANCE)
check("the two caption-less numbers are dropped",
      "2000+" not in out and "1500+" not in out,
      "these produced 'The University of Loralai has over 2000 faculty "
      "members', which contradicts the same page's '100+ Faculty'")
for num, cap in [("5", "Computer Labs"), ("80%", "Financial Aid"),
                 ("100+", "Faculty"), ("10+", "Degree Programs")]:
    check(f"labelled counter kept: {num} -> {cap}",
          num in out and cap in out)

# The faculty pages list an extension and an email as consecutive lines. The
# extension is a bare number, so a careless version of this rule deletes the only
# record of it.
HOD = "Dr. Someone\n1054\nhod.cs@uoli.edu.pk\nDr. Another\n1074\nhod.commerce@uoli.edu.pk"
out_hod = pf.drop_orphan_stats(HOD)
check("HOD extension/email pairs survive intact", out_hod == HOD,
      f"an extension was deleted:\n{out_hod}")

# A bare number as the very last line has no caption, so it is an orphan.
check("a trailing bare number is dropped",
      pf.drop_orphan_stats("Real content here.\n2000+") == "Real content here.")

# ── 6. strip_furniture ───────────────────────────────────────
print("\n6. strip_furniture — removes furniture and bare markup only")

page = ("##### \nEngr. Prof. Dr. Ehsanullah Kakar\nVice Chancellor\n"
        "Scholarships\nStudent Affairs\n"
        "University of Loralai, Zerh Karez, Quetta Road, Loralai")
stripped = pf.strip_furniture(page, {"scholarships", "student affairs"})
check("the Vice Chancellor's name survives",
      "Ehsanullah Kakar" in stripped and "Vice Chancellor" in stripped)
check("the bare '#####' marker is dropped", "#####" not in stripped)
check("the nav labels are dropped",
      "Scholarships" not in stripped and "Student Affairs" not in stripped)
check("the address survives (never furniture, whatever the frequency)",
      "Zerh Karez" in stripped)
check("empty furniture set still drops bare markup",
      "#####" not in pf.strip_furniture(page, set()))
check("idempotent", pf.strip_furniture(stripped, {"scholarships",
                                                  "student affairs"}) == stripped)

# ── 7. contentless pages ─────────────────────────────────────
print("\n7. find_contentless — a page with nothing of its own")

# Built to mirror the real corpus: a footer repeated everywhere, three pages that
# add only a nav label, and two that add a real sentence.
FOOT_LINES = ("University of Loralai, Zerh Karez, Quetta Road, Loralai\n"
              "+92 (824) 410051\ninfo@uoli.edu.pk\nuoli.edu.pk")
corpus = {f"https://uoli.edu.pk/filler{i}": FOOT_LINES + f"\nFiller page {i} "
                                                         f"describes something real."
          for i in range(pf.MIN_PAGES_FOR_FREQUENCY)}
corpus["https://uoli.edu.pk/offices"] = FOOT_LINES
corpus["https://uoli.edu.pk/tender-notice-nit"] = FOOT_LINES + "\nTenders"
corpus["https://uoli.edu.pk/advertisement-for-visiting-faculty"] = \
    FOOT_LINES + "\nCareers"
corpus["https://uoli.edu.pk/bus-routes"] = (
    FOOT_LINES + "\nBus Route\nAll Routes\nUniversity of Loralai provides free "
                 "of charge transportation services for its faculty and students.")

flagged = pf.find_contentless(corpus)
check("a footer-only page is contentless",
      "https://uoli.edu.pk/offices" in flagged)
check("a page whose only own line is a nav label is contentless",
      "https://uoli.edu.pk/tender-notice-nit" in flagged,
      "'Tenders' is a menu item, not a fact about a tender")
check("...and so is one whose only own line is 'Careers'",
      "https://uoli.edu.pk/advertisement-for-visiting-faculty" in flagged)
check("a page with ONE real sentence is NOT contentless",
      "https://uoli.edu.pk/bus-routes" not in flagged,
      "bus routes is a genuine topic; its own lines include a 95-char "
      "sentence, and an own-line COUNT cannot tell it from the tender pages "
      "(3 own vs 1 own) — only is_navigation_label can")
check("ordinary pages are never contentless",
      not any("filler" in u for u in flagged), f"flagged {sorted(flagged)}")

check("a small run yields NOTHING (frequency is not measurable)",
      pf.find_contentless({"https://uoli.edu.pk/offices": FOOT_LINES}) == set(),
      "a single-page re-scrape would delete the page it just fetched")

# ── 8. alias URLs ────────────────────────────────────────────
print("\n8. find_alias_groups — one page served at two URLs")

check("fewest path segments wins",
      pf.canonical_url({"https://uoli.edu.pk/contact",
                        "https://uoli.edu.pk/about/contact"})
      == "https://uoli.edu.pk/contact")
check("then the shorter URL wins",
      pf.canonical_url({"https://uoli.edu.pk/about",
                        "https://uoli.edu.pk/about-old-123"})
      == "https://uoli.edu.pk/about",
      "the obviously stale /about-old-123 was chosen as canonical")
check("the choice is order-independent",
      pf.canonical_url(["https://uoli.edu.pk/about-old-123",
                        "https://uoli.edu.pk/about"])
      == pf.canonical_url(["https://uoli.edu.pk/about",
                           "https://uoli.edu.pk/about-old-123"]),
      "a set-iteration-order dependency would make the scraper and the repair "
      "script disagree at random")

A = "Contact\nPhone: +92 (824) 410051\nEmail: info@uoli.edu.pk"
alias_pages = {
    "https://uoli.edu.pk/contact": A,
    "https://uoli.edu.pk/about/contact": A,
    # Same shape, but ONE digit different. Must not group.
    "https://uoli.edu.pk/office-of-treasurer":
        "Contact\nPhone: +92 (824) 410782\nEmail: info@uoli.edu.pk",
    "https://uoli.edu.pk/library": "Library\nThe library holds 12,000 volumes.",
}
groups = pf.find_alias_groups(alias_pages)
check("the identical pair is grouped",
      groups.get("https://uoli.edu.pk/contact")
      == {"https://uoli.edu.pk/about/contact"}, f"got {groups}")
check("a page differing by ONE DIGIT is not an alias",
      not any("treasurer" in u for u in groups)
      and not any("treasurer" in o for os_ in groups.values() for o in os_),
      f"got {groups} — 410051 and 410782 are different phone numbers and "
      f"collapsing them would delete one of the university's two published lines")
check("an unrelated page is not grouped",
      not any("library" in u for u in groups))
check("whitespace and heading markers are folded",
      pf.find_alias_groups({"https://uoli.edu.pk/a": "## Contact\nPhone:  1",
                            "https://uoli.edu.pk/b/c": "Contact\nPhone: 1"}),
      "two URLs serving the same words differently marked up are still aliases")
check("no aliases means an empty result", pf.find_alias_groups(
    {"https://uoli.edu.pk/x": "one", "https://uoli.edu.pk/y": "two"}) == {})
check("a single page is never its own alias",
      pf.find_alias_groups({"https://uoli.edu.pk/x": A}) == {})

# ── 9. against the live corpus (read-only) ───────────────────
PARENTS = "./data/parent_chunks.json"
print("\n9. the live corpus — read-only")
if not os.path.exists(PARENTS):
    print(f"  skip  {PARENTS} not present")
else:
    with open(PARENTS, encoding="utf-8") as f:
        parents = json.load(f)
    pages = {}
    for v in parents.values():
        src = (v.get("metadata") or {}).get("source", "")
        if src.startswith("http"):
            pages.setdefault(src, []).append(v.get("content", ""))
    pages = {s: "\n".join(c) for s, c in pages.items()}

    # The seven pages that were measured as content-free and then removed by
    # repair_contentless_pages.py. Asserted as an UPPER BOUND rather than an exact
    # set, so this section is correct both before the repair (7 flagged) and after
    # it (0 flagged, because the pages are gone from the store). What must never
    # happen either way is a page outside this set being flagged — that is the
    # failure mode that costs content, and it is what the subset test catches.
    KNOWN_EMPTY = {
        "https://uoli.edu.pk/alumni",
        "https://uoli.edu.pk/offices",
        "https://uoli.edu.pk/offices/faculty-and-staff",
        "https://uoli.edu.pk/tender-notice-nit",
        "https://uoli.edu.pk/tender-bidding-documents",
        "https://uoli.edu.pk/tender-cancellation-notice",
        "https://uoli.edu.pk/advertisement-for-visiting-faculty",
    }

    dead = {s for s, t in pages.items() if pf.looks_dead(t)}
    print(f"     {len(pages)} pages, {len(dead)} flagged dead")
    check("no page outside the known content-free set is called dead",
          dead <= KNOWN_EMPTY,
          f"false positives: {sorted(dead - KNOWN_EMPTY)}")

    # Zero false positives is the claim the ingest-time rejection rests on, so it
    # is asserted at the widest possible setting: the marker must not appear at
    # ANY offset in a page outside that set, not merely outside the window.
    stray = sorted(s for s, t in pages.items()
                   if pf.DEAD_PAGE.search(t or "") and s not in KNOWN_EMPTY)
    check("no real page contains an error marker at any offset",
          not stray, f"{len(stray)} pages would be at risk if the window "
                     f"widened: {stray[:5]}")

    # The parent store has already been cleaned, so this is a no-drift check:
    # running the repair again must find nothing left to remove.
    orphans = pf.find_orphan_stats(pages)
    check("no caption-less counter numbers remain in the parent store",
          not orphans, f"still present: {sorted(orphans)} — "
                       f"repair_parent_text.py --apply has not been run")

    empty = pf.find_contentless(pages)
    print(f"     {len(empty)} pages carry no content of their own"
          f"{' (repair_contentless_pages.py has been applied)' if not empty else ''}")
    check("nothing outside the known content-free set is called contentless",
          empty <= KNOWN_EMPTY,
          f"false positives: {sorted(empty - KNOWN_EMPTY)} — this rule is now "
          f"deleting real pages")

    # Named individually because each one is a page a university chatbot is asked
    # about, and each would be a visible regression.
    for url in ["/faculty", "/contact", "/about/contact", "/bus-routes",
                "/registrar-message", "/library", "/downloads", "/about-us",
                "/faculty/english", "/faculty/computer-science",
                "/undergraduate-programs", "/research", "/academics/research",
                "/about", "/vc-message"]:
        full = "https://uoli.edu.pk" + url
        if full in pages:
            check(f"real page kept: {url}",
                  full not in empty and full not in dead,
                  f"{len(pages[full])} chars of real content would be dropped")

    # Aliasing, measured. Asserted as an upper bound like the two rules above, so
    # the section stays correct after repair_alias_pages.py has been applied.
    live_groups = pf.find_alias_groups(pages)
    print(f"     {len(live_groups)} page(s) served at more than one URL")
    for keep, others in sorted(live_groups.items()):
        print(f"       keep {keep}")
        for o in sorted(others):
            print(f"         alias {o}")

    # The six measured alias groups, listed by the URL that is DROPPED. Note
    # /graduate-programs rather than /undergraduate: the two serve one page titled
    # "Undergraduate/ Graduate Programs", and canonical_url keeps the shorter URL.
    KNOWN_ALIASES = {
        "https://uoli.edu.pk/about/contact",
        "https://uoli.edu.pk/graduate-programs",
        "https://uoli.edu.pk/offices/directorate-of-it",
        "https://uoli.edu.pk/academics/research",
        "https://uoli.edu.pk/about-old-123",
        "https://uoli.edu.pk/academics/academic-rules",
    }
    flagged_aliases = {o for os_ in live_groups.values() for o in os_}
    check("no page outside the known alias set is called an alias",
          flagged_aliases <= KNOWN_ALIASES,
          f"false positives: {sorted(flagged_aliases - KNOWN_ALIASES)} — two "
          f"genuinely different pages are being collapsed into one")
    check("a canonical URL is never also listed as an alias",
          not (set(live_groups) & flagged_aliases),
          "the grouping is inconsistent; a page would be both kept and deleted")


# ── 6. redundant propositions ─────────────────────────────────
# find_redundant deletes rows from ChromaDB (repair_duplicate_children) and
# suppresses them before embedding (setup_propositions.drop_redundant). Both of
# its safety rules were added because the version without them lost data, so both
# are pinned here next to the case they were added for.
print("\n6. redundant propositions — the two safety rules")

# (id, text, category, parent_id)
PHONE = "The contact number for the University of Loralai is +92 (824) 410051."

drop = pf.find_redundant([
    (1, PHONE, "general", "pA"),
    (2, PHONE, "general", "pA"),
    (3, PHONE, "general", "pA"),
])
check("a sentence repeated in one category keeps exactly one copy",
      drop == [2, 3], f"dropped {drop}, expected [2, 3]")

drop = pf.find_redundant([
    (1, PHONE, "general", "pA"),
    (2, PHONE, "offices", "pB"),
    (3, PHONE, "faculty", "pC"),
])
check("the same sentence survives once in EVERY category",
      drop == [],
      "agents filter by category before searching; keeping one copy globally "
      "makes the fact unreachable from a filtered agent, which is the defect "
      "that scored the fee question 0 on all four metrics")

# The last-child guard. Parent pB's only child duplicates pA's, and pA has
# others. Dropping it would leave pB with no child, and a parent is reachable
# ONLY through a child, so pB's text would become unretrievable.
drop = pf.find_redundant([
    (1, PHONE, "general", "pA"),
    (2, "Admissions for Fall 2026 are open.", "general", "pA"),
    (3, PHONE, "general", "pB"),
])
check("a parent's LAST surviving child is never dropped",
      drop == [],
      "without this guard 52 parents lost every child on the live corpus, and "
      "their text — different text from the surviving twin's parent — became "
      "unreachable")

drop = pf.find_redundant([
    (1, PHONE, "general", "pA"),
    (2, "Admissions for Fall 2026 are open.", "general", "pA"),
    (3, PHONE, "general", "pB"),
    (4, "The library holds 12,000 volumes.", "general", "pB"),
])
check("but it IS dropped once its parent has another child",
      drop == [3], f"dropped {drop}, expected [3]")

check("punctuation variants of one fact fold together",
      pf.proposition_key("+92 (824) 410051.") ==
      pf.proposition_key("+92-824-410051"),
      "the extractor punctuates the same number inconsistently, so a strict "
      "key would leave the duplicates in place")
check("two DIFFERENT numbers never fold together",
      pf.proposition_key("The number is +92 (824) 410051.") !=
      pf.proposition_key("The number is +92 (824) 410782."),
      "410051 is the switchboard and 410782 the treasurer; folding them would "
      "silently delete one of two real facts")

before = [(i, PHONE, "general", f"p{i}") for i in range(40)]
kept = len(before) - len(pf.find_redundant(before))
check("40 copies on 40 single-child parents all survive",
      kept == 40,
      f"{kept} survived; every one is its parent's only child, so the guard "
      f"must protect all of them — this is why 24 pairs still repeat on the "
      f"live corpus after the repair, and that is correct, not a failure")


# ── 7. OCR blocks ─────────────────────────────────────────────
# strip_image_blocks replaces three scripts that each guessed where an OCR block
# ended and deleted the rest of the parent. The guessing is what these pin.
print("\n7. OCR blocks — removal must stop at the end of the block")

PROSE_A = "The Office of the Treasurer manages university finances."
PROSE_B = "The Treasurer is responsible for the annual budget."
OCR = "YOUTI\nSamfoWah\n39.4.01"

# The case every disabled script got wrong: prose AFTER the block, no blank line.
delim = (f"{PROSE_A}\n{pf.IMAGE_BLOCK_OPEN}\n{OCR}\n{pf.IMAGE_BLOCK_CLOSE}\n"
         f"{PROSE_B}")
out = pf.strip_image_blocks(delim)
check("prose after a delimited block survives",
      PROSE_B in out,
      "this is the exact case the blank-line loop deleted: the scraper emits no "
      "blank line after the block, so `skip` never cleared and everything to "
      "the end of the parent was lost — 88 parents, worst case 1515 chars -> 37")
check("prose before a delimited block survives", PROSE_A in out)
check("the OCR text itself is gone", "SamfoWah" not in out)
check("the markers themselves are gone",
      pf.IMAGE_BLOCK_OPEN not in out and pf.IMAGE_BLOCK_CLOSE not in out)

two = (f"{PROSE_A}\n{pf.IMAGE_BLOCK_OPEN}\n{OCR}\n{pf.IMAGE_BLOCK_CLOSE}\n"
       f"{PROSE_B}\n{pf.IMAGE_BLOCK_OPEN}\nmore junk\n{pf.IMAGE_BLOCK_CLOSE}")
out = pf.strip_image_blocks(two)
check("two blocks are both removed, and the prose between them kept",
      PROSE_A in out and PROSE_B in out and "junk" not in out,
      "a non-greedy match is required; a greedy one would swallow PROSE_B")

# Legacy unclosed format. There is genuinely no end delimiter, so the only sound
# reading is the emitter's guarantee: appended extras come last.
legacy = f"{PROSE_A}\n[IMAGE CONTENT from https://x/y.jpg]\n{OCR}"
out = pf.strip_image_blocks(legacy)
check("a legacy unclosed block is removed",
      "SamfoWah" not in out and "IMAGE CONTENT" not in out)
check("prose before a legacy block survives", PROSE_A in out)

check("text with no block is returned unchanged",
      pf.strip_image_blocks(PROSE_A) == PROSE_A)
check("empty input does not raise",
      pf.strip_image_blocks("") == "" and pf.strip_image_blocks(None) == "")


# ── 8. which images get read at all ───────────────────────────
# The gate is in two halves and this section tests each against the REAL code.
#
# WHAT THE PREVIOUS VERSION OF THIS SECTION DID WRONG, since the mistake is easy
# to repeat: it recovered the gate's pattern lists from the source text of
# _extract_images_text with inspect.getsource + a regex + exec, and wrapped the
# whole thing in try/except. When four of the six names it looked for were
# deleted, the regex matched nothing, .group(1) raised AttributeError, and the
# except branch printed ONE tidy failure. The section had stopped testing
# anything and still looked like a test. A test must not reconstruct the rule it
# is checking — it must call it. Both halves are now importable:
#
#   half 1, before download : web_scraper.image_passes_url_gate(src, is_homepage)
#   half 2, after reading   : ocr_quality.is_usable(text, confidence, is_table)
#
# The split is the whole design. Half 1 may only refuse what a URL can prove —
# site furniture and non-raster formats. Whether an image is a DOCUMENT is never
# decided from its filename, because a filename cannot know. Every URL and every
# OCR string below is one this site actually served.
print("\n8. image gate — decided by content, not by filename")

import web_scraper as _ws
import ocr_quality as _oq

U = "https://uoli.edu.pk/wp-content/uploads/2026/07/"


def url_ok(name, is_homepage=True):
    return _ws.image_passes_url_gate(U + name, is_homepage)


# ---- half 1: the pre-download gate refuses furniture and nothing else --------
check("the admissions banner passes the URL gate",
      url_ok("Advertisement.jpeg"),
      "this image is the whole reason OCR was switched on; its text is the only "
      "place the programs-offered list exists")
check("a course-table screenshot passes the URL gate",
      url_ok("Screenshot-2025-01-29-010311.png"),
      "16 of these hold the MPhil and BS semester tables, which exist in no "
      "other form on the site")
check("a WhatsApp-named notice now passes the URL gate",
      url_ok("WhatsApp-Image-2025-07-06-at-11.34.57-AM.jpeg"),
      "THIS IS THE NEW BEHAVIOUR. _PHOTO_PATTERNS blocked it for containing "
      "'whatsapp'. It is a screenshot of a real notice on /faculty/education — "
      "refused for the app it arrived through. Measured over 93 raster images on "
      "4 live pages, that 10-pattern list blocked 4 images and all 4 came from "
      "'whatsapp'; the other 9 patterns matched nothing at all")
check("a camera photograph passes the URL gate too",
      url_ok("DSC_1077-scaled.jpg"),
      "also new, and deliberate: it is refused later, on its text, by is_usable")
check("the footer logo is refused",
      not url_ok("university-of-loralai-1.png"),
      "on every page of the site")
check("a nav icon is refused",
      not url_ok("cropped-icon-192x192.png"))
check("an SVG is refused",
      not url_ok("research-vector.svg"),
      "every SVG on this site is vector furniture; OCR on a rasterised icon "
      "returns its decorative strokes as garbage")
check("the popup advertisement is refused away from the homepage",
      not url_ok("Advertisement.jpeg", is_homepage=False),
      "it fires as a JS popup on many pages; the byte-owner rule alone would "
      "keep it to one page, but WHICH page would depend on crawl order")
check("the same advertisement is kept on the homepage",
      url_ok("Advertisement.jpeg", is_homepage=True))

# ---- half 2: the post-read gate judges the text ------------------------------
# Real OCR output, with the confidence EasyOCR actually returned for it.
_BANNER = ("UNIVERSITY OF LORALAI ADMISSION OPEN FALL 2026 Programs Offered "
           "BS Computer Science BS English BS Education BS Zoology MPhil "
           "Education Last date to apply 30 September 2026 Apply online at "
           "uoli.edu.pk admission office contact 0824 410051 " * 4)
# 70 chars — inside the measured gap (real logo crops: 57-79; real documents: 281+).
# The previous string was only 42 chars, which was below MIN_CHARS for the right
# reason but did not sit inside the gap the comment describes.
_LOGO_CROP = ("UNIVERSITY OF LORALAI RELEVANCE EXCELLENCE "
              "Knowledge Character Building")
_PHOTO_JUNK = "YOUTI SamfoWah 39.4.01"
_FB_EXPORT = "7qgloit Conincr- South sian"
_FEE_TABLE = ("| S.No | Course Code | Credit Hours | Fee |\n"
              "| 1 | 311 | 3(3+0) | 12000 |\n| 2 | 321 | 2(2+0) | 8000 |\n"
              "| 3 | 331 | 3(3+0) | 12000 |\n| 5 | 341 | 3(3+0) | 12000 |\n"
              "| 6 | 351 | 2(2+0) | 8000 |\n| 7 | 361 | 3(3+0) | 12000 |")

# is_usable returns (keep, reason), not a bare bool. Every call below unpacks the
# tuple so a False keep is tested with `not _keep` rather than `not (False, ...)`,
# which is always False because a non-empty tuple is truthy.

_keep, _why = _oq.is_usable(_BANNER, 0.91, False)
check("the banner's TEXT is accepted", _keep, f"unexpected rejection: {_why}")

_keep, _why = _oq.is_usable(_LOGO_CROP, 0.88, False)
check("a logo crop is rejected on length", not _keep,
      f"{len(_LOGO_CROP)} chars, rejected as: {_why}; the three real logo crops "
      f"read 57-79 and every real document read 281+, so "
      f"MIN_CHARS={_oq.MIN_CHARS} sits in a measured gap. Note the confidence is "
      f"HIGH — the OCR is correct, there is simply nothing there. Length is the "
      f"only signal that catches this")

_keep, _why = _oq.is_usable(_PHOTO_JUNK, 0.62, False)
check("photograph garbage is rejected", not _keep,
      f"rejected as: {_why} — this is what a convocation photo yields; it is why "
      f"deleting the filename photo filter is safe")

_keep, _why = _oq.is_usable(_FB_EXPORT, 0.55, False)
check("a Facebook export is rejected", not _keep,
      f"rejected as: {_why} — all 50 numeric-filename images were event posters "
      f"and footer screenshots")

_keep, _why = _oq.is_usable(_FEE_TABLE, 0.72, True)
check("a fee table is accepted despite being mostly digits", _keep,
      f"unexpected rejection: {_why} — is_table=True skips the wordish test. "
      f"Without that exemption every fee schedule and every course table on the "
      f"site is discarded as garbage — and those tables exist in no other form. "
      f"This is the single assertion that protects 'never compromise on tables' "
      f"at the ingestion boundary")

# NOTE: the previous version asserted that _FEE_TABLE is rejected when
# is_table=False. That assertion was factually wrong: measured wordish=1.0
# because tokens like "Course", "Code", "311", "3(3+0)" all match _WORD,
# _NUMBER or _LABEL. The thresholds are measured and the assertion was a guess.
# Deleted per HANDOVER rule: keep the one that matters (accepted with
# is_table=True above) and do not bend thresholds to satisfy a wrong guess.

_keep, _why = _oq.is_usable(_BANNER, 0.20, False)
check("low OCR confidence is rejected even when the text looks fine", not _keep,
      f"rejected as: {_why}; "
      f"MIN_MEAN_CONFIDENCE={_oq.MIN_MEAN_CONFIDENCE}; a confident-looking "
      f"hallucination from a blurred scan is worse than no text")

_keep, _why = _oq.is_usable("", 0.0, False)
_keep2, _ = _oq.is_usable("", 0.9, True)
check("empty text does not raise", not _keep and not _keep2,
      f"rejected as: {_why}")

print("\n" + "-" * 68)
print("9. the parent-swap provenance gate (knowledge_base)")
print("-" * 68)
# The child->parent swap trades a matched proposition for the passage around it.
# Two size rules already reject swaps that buy nothing. They are not enough: 470
# live swaps clear both and still land in a parent under 200 characters, and 123
# of those land in one 119-character postal-address stub whatever the child was
# about. The worst case is the whole argument for rule 3 — "The University of
# Loralai was established in 2012" is replaced by the address, so the founding
# year leaves the context for the question that asked for it.
#
# Rule 3 tests PROVENANCE, not size: a child is a proposition written FROM its
# parent, so it restates the parent's content words. Measured over all 4857 live
# children, a child under a healthy >=200-char parent finds 82% of its content
# words there (median 88%, p10 0.55); a child under a parent damaged after the
# child was written finds 17%.
try:
    from knowledge_base import KnowledgeBase as _KB
    import config
    _kb = _KB.__new__(_KB)          # methods only — no model load, no API cost

    ADDR = ("University of Loralai, Quetta Road, Zerh Karez, Loralai, "
            "Balochistan. Phone: +92 (824) 410782. Email: info@uoli.edu.pk.")

    def upgrade(child, parent):
        return _kb._parent_is_upgrade(
            child, parent, _kb._child_adds_detail(child, parent))

    # The measured live failures, by shape rather than by row.
    check("a founding-year child is not swapped for the address stub",
          not upgrade("The University of Loralai was established in 2012.", ADDR),
          "123 live swaps replaced an unrelated child with this exact stub; the "
          "size rules pass it because 119/50 clears the 1.2x ratio and the child "
          "carries no email/phone/percent/fee for the adds_detail rule to catch")
    check("a library child is not swapped for the address stub",
          not upgrade("The University of Loralai has a library.", ADDR))
    check("a programs-count child is not swapped for the address stub",
          not upgrade("UOL offers more than 10 degree programs.", ADDR))
    check("a truncated continuation header is not swapped in",
          not upgrade("The acronym for the conference is SARCHE 2026.",
                      "[2-.15 JANUARY DHAKA INGLADESH South Asian Regional "
                      "Conference on Higher Education dignitaries attended]"))

    # The other direction matters just as much: the stub IS the right context for
    # an address question, and rule 3 must not reject that. 49 live swaps are of
    # this kind and all of them should survive.
    check("an address child IS still swapped for the address stub",
          upgrade("The University of Loralai is located on Quetta Road, "
                  "Zerh Karez, Loralai.", ADDR),
          "rejecting these would make the gate a size rule with extra steps")
    check("a contact-number child IS still swapped for the address stub",
          upgrade("The contact number for the Treasurer Office is "
                  "+92 (824) 410782.", ADDR))

    # A healthy, genuinely richer parent must always win.
    _healthy = ("The Department of Computer Science was established in 2013 and "
                "offers BS and MS degrees. The department has twelve faculty "
                "members and maintains two computer laboratories for students.")
    check("a healthy richer parent is still an upgrade",
          upgrade("The Department of Computer Science offers BS and MS degrees.",
                  _healthy))

    # The two original size rules must keep working — rule 3 is added, not
    # substituted.
    check("rule 1 still rejects a parent no bigger than its child",
          not upgrade("A" * 100 + " department faculty programs",
                      "department faculty programs"),
          "PARENT_MIN_GAIN_RATIO")
    check("rule 2 still rejects a small parent lacking the child's hard fact",
          not upgrade("80% of students are supported through financial aid.",
                      "Financial aid and scholarships at the university office."),
          "PARENT_MIN_CONTEXT_CHARS")

    # Abstention: a child with no topic words asserts nothing about the parent,
    # so rule 3 must not be the thing that decides.
    check("a child with no content words does not trip rule 3",
          _kb._parent_shares_topic("It is 5%.", _healthy),
          "abstains rather than voting; the size rules still apply")

    # Rule 3 does not count the institution's own name as shared subject matter.
    # This is what separates "has a library" (0.67 -> 0.00 against the address
    # stub) from a genuine address child. A sentence that is ONLY the name
    # therefore has nothing left to compare and must abstain, not reject.
    check("the university's own name is not evidence of shared topic",
          not _kb._content_words(config.UNIVERSITY_NAME),
          f"_ENTITY_STOP = {sorted(_kb._ENTITY_STOP)}")
    check("a child that says only the university's name abstains",
          _kb._parent_shares_topic(f"This is {config.UNIVERSITY_NAME}.", ADDR))

    # SCOPE. Rule 3 is bounded to parents below PARENT_MIN_CONTEXT_CHARS. Shipping
    # it unbounded also rejected 57 swaps into parents >=200 chars and cost 0.115
    # of measured context_recall for no precision gain, because rejecting a LONG
    # parent keeps one proposition where a passage may hold the answer in words the
    # child never reused. These two assertions are the bound: if someone widens the
    # rule again, the first fails, and they have to re-measure recall to justify it.
    _NAV = ("Home About Us Administration Academics Departments Admissions "
            "Downloads Contact Us Tenders Careers News Events Gallery Alumni "
            "Library Examination Results Notifications Prospectus Apply Online "
            "Convocation Scholarships Hostel Transport Sports Clubs Feedback")
    check("rule 3 does not adjudicate a parent >= PARENT_MIN_CONTEXT_CHARS",
          upgrade("The Department of Physics offers a BS degree.", _NAV),
          f"len={len(_NAV)} >= {config.PARENT_MIN_CONTEXT_CHARS}; zero topic "
          f"overlap, yet accepted — nav menus are removed at INGESTION, not "
          f"declined per query. Unbounded rejection here cost recall 0.115")
    check("rule 3 still adjudicates a parent below the bound",
          not upgrade("The Department of Physics offers a BS degree.", ADDR),
          f"len(ADDR)={len(ADDR)} < {config.PARENT_MIN_CONTEXT_CHARS}")

    # The threshold must stay inside the measured gap. Healthy p10 is 0.55 and
    # the damaged medians are 0.14 and 0.25, so anything from ~0.30 to ~0.50
    # works; a value outside that is either useless or expensive.
    check("PARENT_MIN_WORD_OVERLAP sits inside the measured gap",
          0.25 <= config.PARENT_MIN_WORD_OVERLAP <= 0.50,
          f"is {config.PARENT_MIN_WORD_OVERLAP}; healthy p10=0.55, damaged "
          f"medians 0.14/0.25 — 0.50 costs 6% of good swaps for the last fifth "
          f"of the benefit, below 0.25 stops catching the address stub")
except Exception as exc:                        # noqa: BLE001
    check("the parent-swap gate could be exercised", False, repr(exc))

print("\n" + "=" * 68)
if FAILURES:
    print(f"FAILED {len(FAILURES)}: " + "; ".join(FAILURES))
    sys.exit(1)
print("structure-vs-content rules behave: dead pages rejected, contact facts kept")
sys.exit(0)
