"""page_furniture.py — tell site-wide navigation furniture from real content.

Pure functions, no heavy imports, no network, no API calls. Imported by
web_scraper (to keep furniture out of future scrapes) and by
repair_parent_text.py (to clean the furniture already in the parent store).

THE PROBLEM
───────────
Every page of uoli.edu.pk carries a header bar and a footer widget. The scraper
keeps them, so one line of markup becomes 70-odd copies in the corpus:

    Scholarships                                              72 of 78 pages
    Student Affairs                                           72
    Hostels                                                   72
    University of Loralai, Zerh Karez, Quetta Road, Loralai   72
    uoli.edu.pk                                               72
    info@uoli.edu.pk                                          51
    +92 (824) 410782                                          40

Measured consequence, and the reason this module exists: the parent chunk holding
the single most important fact in the corpus reads

    Engr. Prof. Dr. Ehsanullah Kakar / Vice Chancellor / University of Loralai
    / ##### / Scholarships / Student Affairs / Hostels / University of Loralai,
    Zerh Karez, Quetta Road, Loralai / uoli.edu.pk

Parents are what the answering model is shown after the parent swap, so the model
reads the Vice Chancellor's name welded to a navigation menu. That same menu was
measured ranking 5th for "What departments does UoL have?".

WHY FREQUENCY AND NOT A LIST OF STRINGS
───────────────────────────────────────
The obvious fix is a hand-written set of furniture strings. This file does not do
that, because the old category detector already proved how that ends: it needed a
maintained list of ~30 news slugs, and every page the university published was
wrong until somebody edited the list. A frequency rule needs no maintenance. If
the university redesigns its footer, the new footer is still on every page and is
still caught; a hand-written list would silently stop working.

WHY FREQUENCY ALONE IS NOT ENOUGH
─────────────────────────────────
Four of the high-frequency lines are not furniture at all — they are the
university's address, phone and email. Strip by frequency alone and the answer to
"what is the university's phone number" is deleted from the knowledge base.

    CORRECTION, measured 2026-09-13 against the live site (65 pages fetched).
    The paragraph above used to end "...the main switchboard number appears ONLY
    in that repeated header line", and that is FALSE. /contact's own body carries
    it, in its own format:

        Admin Office:    | +92 824-410051
        Mailing Address: | University of Loralai, Zerh Karez, Quetta road,
                         |   Loralai, Balochistan Pakistan
        Business Hours:  | 9:00 am - 5:00 pm, Monday through Friday.

    plus the full office directory (VC 410569, PSO 410185, Pro VC 410034,
    Registrar 410021, Treasurer 410393, Controller 410076, Dean 410046).

    So the facts do NOT depend on the repeated chrome line, and removing that
    line from the other 64 pages loses nothing. See find_site_contact_lines.

So frequency selects candidates and a second, content-based test decides. A line
is furniture only if it is ALSO short and carries no contact token:

    line                                                      digits @ domain len  verdict
    Scholarships                                                 -   -   -    12  furniture
    Student Affairs                                              -   -   -    15  furniture
    Hostels                                                      -   -   -     7  furniture
    News & Events                                                -   -   -    13  furniture
    Follow Us                                                    -   -   -     9  furniture
    We are committed to building trust                           -   -   -    33  furniture
    ###                                                          -   -   -     3  furniture
    University of Loralai, Zerh Karez, Quetta Road, Loralai      -   -   -    54  KEPT (too long)
    uoli.edu.pk                                                  -   -   y    11  KEPT (domain)
    info@uoli.edu.pk                                             -   y   y    16  KEPT (email)
    +92 (824) 410782                                             y   -   -    16  KEPT (digits)
    +92 (824) 410051                                             y   -   -    16  KEPT (digits)
    University of Loralai, Quetta Road, ... Balochistan.         -   -   -    69  KEPT (too long)
    University of Loralai, ... Phone: ... Email: ...             y   y   y   118  KEPT

A nav label is a short noun phrase. An address is a long one. A phone number has
digits. That is the whole discriminator, and it is the same kind of structural
test already used elsewhere in this project to separate a proposition (has a
finite verb) from scraped page furniture (a field list).

DELIBERATELY CONSERVATIVE
─────────────────────────
Anything carrying a digit, an "@" or a domain is kept even when it appears on
every page. Keeping a duplicate costs a little context precision. Deleting the
university's phone number costs a correct answer. Those are not symmetric, so
this module always errs toward keeping.

That rule is unchanged and still governs find_furniture. What it did NOT
anticipate is the cost of keeping a line on all 65 pages at once:
find_site_contact_lines, at the end of this file, keeps exactly the same facts
but on one page instead of sixty-five.
"""
import re

# A line must appear on at least this FRACTION of the run's pages to be a
# candidate. 0.25 sits inside a wide, clean gap measured on the live corpus:
# 14 distinct lines land at >=25% of pages, the next band (10-25%) holds 21
# lines, and below that it is 597 lines on 2-10% and 1508 lines unique to one
# page. Nothing real lives near the boundary, so the exact value is not delicate.
DEFAULT_THRESHOLD = 0.25

# Below this many pages, document frequency means nothing: on a 3-page run every
# line looks like it is on "33% of pages". A single-page re-scrape via
# setup_knowledge_base.py --url must therefore change nothing at all.
MIN_PAGES_FOR_FREQUENCY = 20

# A nav label is a short noun phrase; an address is a long one. Measured: the
# longest true furniture line is 33 chars, the shortest kept fact is 54.
#
# WHY THIS IS NOT RAISED, THOUGH ONE NAV LABEL ESCAPES IT
# ───────────────────────────────────────────────────────
# The measurement above was taken on a corpus whose nav header had already been
# stripped by an earlier script, so it never saw the full header. Re-measured
# against the pre-damage backup, the nav bar is 13 lines and this cap catches 10
# of them. Of the three it misses, two are the contact lines it is meant to miss.
# The third is a genuine escape:
#
#     pages  len  ends-in-period
#        73   55  no    university of loralai, zerh karez, quetta road, loralai
#        62   69  yes   university of loralai, quetta road, zerh karez, ... balochistan.
#        61   53  no    office of research, innovation, and commercialisation
#
# That is the ENTIRE population of lines above this cap that also clear the
# frequency threshold — three lines, two of which are the campus address. So
# every rule that would strip the ORIC label also strips an address variant:
#
#     raise the cap to 60      strips the 55-char address. Deletes the answer to
#                              "where is the university located".
#     require a terminal stop  the 55-char address has none either.
#     add a comma test         both address variants and the ORIC label have
#                              commas.
#
# With three data points, any rule that separates them is fitted to those three
# points, which is the definition of the overfitting this project is trying to
# get away from. The cost of leaving the label in is 61 repeated lines, and those
# become repeated propositions, which find_redundant already reduces to one copy
# per category. Paying a small known cost to the layer built for it beats adding
# a rule whose failure mode is deleting the university's address.
MAX_FURNITURE_CHARS = 40

# Tokens that mark a line as carrying a contact fact rather than being a label.
_CONTACT_HINT = re.compile(
    r"""\d               # any digit: phone, extension, year, fee, count
      | @                # email
      | \b\w+\.(?:edu|com|org|net|pk)\b   # bare domain such as uoli.edu.pk
    """,
    re.VERBOSE | re.IGNORECASE,
)


def normalise(line: str) -> str:
    """Comparison key for one line: case and whitespace folded, markers stripped.

    Strips the markdown markers _extract_text inserts ("#", "•", "|") so that a
    heading and a list item with the same words count as the same line.
    """
    return re.sub(r"^[#•|\s]+", "", " ".join(line.split()).lower()).strip()


def is_navigation_label(line: str) -> bool:
    """True if the line looks like a nav/menu label rather than a fact.

    Content-based half of the test. Applied only to lines that frequency has
    already flagged, so a short page heading on a single page is never affected.
    """
    text = " ".join(line.split()).strip(" #•|")
    if not text:
        return True
    if len(text) > MAX_FURNITURE_CHARS:
        return False
    return not _CONTACT_HINT.search(text)


def document_frequency(pages: dict) -> dict:
    """How many DISTINCT pages each normalised line appears on.

    Counts pages, not occurrences: a line repeated twice inside one page must not
    look site-wide on the strength of that page alone.
    """
    freq = {}
    for text in pages.values():
        for key in {normalise(ln) for ln in (text or "").splitlines() if ln.strip()}:
            if key:
                freq[key] = freq.get(key, 0) + 1
    return freq


def find_furniture(pages: dict,
                   threshold: float = DEFAULT_THRESHOLD,
                   min_pages: int = MIN_PAGES_FOR_FREQUENCY) -> set:
    """The set of normalised lines that are site-wide navigation furniture.

    Args:
        pages:     {url: full page text} for every page in this ingest run.
        threshold: fraction of pages a line must appear on to be a candidate.
        min_pages: below this many pages, returns an empty set — frequency is
                   not measurable and guessing would corrupt a small re-scrape.

    Returns:
        Normalised line keys. Compare with normalise() before removing.
    """
    if len(pages) < min_pages:
        return set()

    cutoff = max(2, int(len(pages) * threshold))
    freq = document_frequency(pages)
    return {
        key for key, count in freq.items()
        if count >= cutoff and is_navigation_label(key)
    }


# ── the site-wide contact block ───────────────────────────────
# find_furniture above KEEPS the phone, email, domain and address lines, and that
# is the right call for the reason its docstring gives. But the consequence was
# never measured until now: those lines sit on EVERY page, the proposition writer
# reads every page, and it writes the same six sentences sixty-odd times.
#
# Measured on the live site, 2026-09-13, 65 pages fetched and run through the real
# _extract_text — the seven high-frequency lines find_furniture keeps:
#
#     pages  line                                                      verdict
#        65  +92 (824) 410051                                          contact
#        63  info@uoli.edu.pk                                          contact
#        63  uoli.edu.pk                                               contact
#        63  university of loralai, zerh karez, quetta road, loralai   contact
#        55  university of loralai, quetta road, ... balochistan.      contact
#        55  +92 (824) 410782                                          contact
#        54  office of research, innovation, and commercialisation     NOT contact
#
# In the database that is 245 near-duplicate contact children across 46 of 65
# pages — 8.1% of all web chunks. They cost more than their storage: search looks
# at CANDIDATE_K=50 candidates, so every slot one of these takes is a distinct
# document that never reaches the reranker.
#
# THE SEVENTH LINE IS WHY THIS IS NOT JUST "STRIP HIGH-FREQUENCY LINES"
# ─────────────────────────────────────────────────────────────────────
# "Office of Research, Innovation, and Commercialisation" is on 54 pages because
# it is in the mega-menu — but it is also the NAME OF THE OFFICE, and /oric is the
# page that answers questions about it. Strip it site-wide and /oric no longer
# says what it is. So frequency cannot be the whole test here either, exactly as
# it could not be for find_furniture.
#
# TWO CONDITIONS, AND THE SECOND IS THE SAFETY NET
# ────────────────────────────────────────────────
# A line is site-wide contact chrome only if BOTH hold:
#
#   1. it looks like a contact fact — a phone number, an email, a bare domain, or
#      the university's own name followed by address-shaped comma segments.
#   2. it also appears on the contact page itself.
#
# Condition 2 is what makes this safe to get wrong. Whatever condition 1 matches
# by accident, a copy of it survives on /contact, because that is the only page
# the strip never touches. The failure mode is therefore "a fact ends up stated
# once instead of many times", never "a fact is deleted".
#
# WHY NOT HARD-CODE THE STRINGS
# ─────────────────────────────
# Same reason find_furniture does not: a list of phone numbers is wrong the day
# the university changes one, and nothing would report it. Both conditions here
# are structural — they re-measure themselves on every scrape.

CONTACT_PATH = "contact"

# Enough commas to be an address rather than a title. "University of Loralai,
# Zerh Karez, Quetta Road, Loralai" has three; the bare nav label "University of
# Loralai" has none.
_ADDRESS_MIN_COMMAS = 2

# Narrower than _CONTACT_HINT on purpose. _CONTACT_HINT matches ANY digit, which
# is correct for deciding "do not treat this as a nav label" but far too loose to
# decide "delete this from 64 pages" — it would match "BS Computer Science (4
# years)". A contact fact is a run of digits long enough to be a phone number, an
# email, or a domain.
_CONTACT_FACT = re.compile(
    r"""  \+?\d[\d\s()\-]{7,}\d          # phone: +92 (824) 410051, 824-410051
        | \b[\w.\-]+@[\w.\-]+\.\w+\b     # email
        | \b\w+\.(?:edu|com|org|net|pk)\b  # bare domain such as uoli.edu.pk
    """,
    re.VERBOSE,
)


def is_contact_page(url: str) -> bool:
    """True if this URL is the university's contact page.

    Matches on the LAST path segment, not the whole URL, so both members of the
    alias pair /contact and /about/contact qualify. That matters: find_alias_groups
    decides two URLs are the same page by comparing their text, so exempting one
    and stripping the other would make them differ, break the grouping, and ingest
    a second copy of the contact page — the opposite of the intent here.
    """
    return (url or "").rstrip("/").rsplit("/", 1)[-1].lower() == CONTACT_PATH


def looks_like_contact_fact(line: str, university_name: str = "") -> bool:
    """True if the line states a contact fact: phone, email, domain, or address.

    university_name is passed in rather than imported from config, so this module
    keeps its single dependency on `re` and stays testable without the app's
    settings. An empty name simply disables the address arm.
    """
    text = " ".join((line or "").split()).strip(" #•|")
    if not text:
        return False
    if _CONTACT_FACT.search(text):
        return True

    # The address arm. Two of the six lines — the ones that matter most, since
    # they are the longest — carry no digit, no "@" and no domain. What they do
    # carry is the university's own name followed by comma-separated place names.
    bare = re.sub(r"\s*\(.*?\)\s*", " ", university_name or "").strip()
    if not bare:
        return False
    return (bare.lower() in text.lower()
            and text.count(",") >= _ADDRESS_MIN_COMMAS)


def find_site_contact_lines(pages: dict,
                            university_name: str = "",
                            threshold: float = DEFAULT_THRESHOLD,
                            min_pages: int = MIN_PAGES_FOR_FREQUENCY) -> set:
    """Normalised lines that are the site-wide contact block repeated as chrome.

    Strip these from every page EXCEPT the contact page. Returns an empty set if
    the run has no contact page in it, because then there is nowhere for the
    surviving copy to live and stripping would genuinely delete the facts.

    Args:
        pages:           {url: full page text} for every page in this run.
        university_name: config.UNIVERSITY_NAME; enables the address arm.
        threshold:       fraction of pages a line must appear on.
        min_pages:       below this, returns empty — frequency is not measurable.

    Returns:
        Normalised line keys, disjoint from find_furniture's set by construction:
        a line here carries a contact token, and find_furniture only accepts lines
        that carry none.
    """
    if len(pages) < min_pages:
        return set()

    contact_urls = [u for u in pages if is_contact_page(u)]
    if not contact_urls:
        return set()

    # Condition 2's evidence: every line the contact page states in its own right.
    on_contact = set()
    for url in contact_urls:
        on_contact |= {normalise(ln) for ln in (pages[url] or "").splitlines()
                       if ln.strip()}

    cutoff = max(2, int(len(pages) * threshold))
    freq = document_frequency(pages)
    return {
        key for key, count in freq.items()
        if count >= cutoff
        and key in on_contact
        and looks_like_contact_fact(key, university_name)
    }



# ── dead pages ────────────────────────────────────────────────
# The text a WordPress theme serves when a URL does not resolve. Defined HERE,
# once, because two different layers act on it and they must never disagree:
#
#   web_scraper.scrape_page   refuses to ingest the page at all (prevention)
#   KnowledgeBase._is_boilerplate  sinks it in the ranking (remediation, for
#                                  anything already in the database)
#
# config.py's module docstring has promised a web_scraper._looks_dead() since the
# category rewrite; it never existed, so until now the only defence was the
# ranking penalty, and a 404 page still cost an embedding call and six database
# rows. Measured: /offices/faculty-and-staff is exactly this page, and it produced
# six children — four duplicate footer facts, plus "The document indicates that
# there was an error in finding the requested page" and "The document includes
# sections for Scholarships, Student Affairs, and Hostels". Neither is an answer
# to anything, and the URL is what a query about faculty and staff matches first.
DEAD_PAGE = re.compile(
    r"(?i)(something went wrong|couldn'?t find your page|page not found|"
    r"\b404\b|back to home|oops)")

# A 404 template leads with its error. A real page that happened to discuss HTTP
# errors in prose would mention them further down — and none in this corpus
# mention them at all: across all 78 pages the only match for any of these
# markers, at any offset, is the one dead page, at offset 0. The window is
# therefore generous without being risky, and it is what keeps the rule from
# rejecting a genuine page that says "page not found" in a paragraph about the
# library catalogue.
DEAD_PAGE_WINDOW = 300


def looks_dead(text: str) -> bool:
    """True if this page is a server error template rather than a page.

    Deliberately independent of any query, and deliberately separate from
    is_navigation_label. Those two tests answer different questions and mixing
    them has already cost a measurable regression: an earlier version let the
    "this question asks for contact details" exemption cover dead pages as well
    as footers, and the held-out question "What is the fax number of the
    university?" returned the "Oops! Something went Wrong..." page at RANK 1.

    A footer can be the answer to "what is the university's phone number". A page
    that failed to load is the answer to nothing, so there is no query for which
    it should be exempt, and no reason to wait until ranking to deal with it.
    """
    if not text:
        return False
    return bool(DEAD_PAGE.search(text[:DEAD_PAGE_WINDOW]))


# ── contentless pages ─────────────────────────────────────────
# A line is treated as belonging to the SITE rather than to a page once it
# appears on this many pages. Not a tuning knob — it is bounded from below by the
# site's alias groups. uoli.edu.pk serves several pages at two URLs each
# (/contact = /about/contact, /research = /academics/research, /about =
# /about-old-123) and three near-identical tender pages. At min_pages=2 all of
# those hide each other and 19 pages look contentless, /contact included; at 4,
# only the genuinely empty ones do. 4 is the smallest value larger than the
# biggest alias group.
SITE_WIDE_PAGES = 4


def page_has_own_content(text: str, freq: dict,
                         site_wide: int = SITE_WIDE_PAGES) -> bool:
    """True if the page says anything that is both its own and not a label.

    Two conditions, and a line must fail BOTH to be dismissed:

        appears on >= site_wide pages  -> belongs to the site, not this page
        is a navigation label          -> its own, but still not a fact

    WHY BOTH TESTS ARE NEEDED
    ─────────────────────────
    Measured on the live corpus, counting each page's lines that appear on fewer
    than 4 pages ("own lines"):

        /alumni                              0 own
        /offices                             0 own
        /tender-notice-nit                   1 own : "Tenders"
        /advertisement-for-visiting-faculty  1 own : "Careers"
        /registrar-message                   2 own : "Registrar's Message" +
                                                     "Welcome to the University
                                                      of Loralai, where our
                                                      pursuit of excellence..."
        /bus-routes                          3 own : "Bus Route", "All Routes" +
                                                     "University of Loralai
                                                      provides free of charge
                                                      transportation services..."

    A count alone cannot separate those: the last content-free page has 1 own line
    and the first real page has 2, and /bus-routes is a genuine topic. What
    separates them is WHAT the own line is — "Tenders" is a nav label, the
    transportation sentence is prose. is_navigation_label already draws that line
    and is already validated, so this composes the two rather than inventing a
    third threshold.

    WHY NOT A SINGLE-PAGE TEST
    ──────────────────────────
    Two were tried against all 78 pages and both would have deleted real content:

      "no finite verb"   17 pages have none, including /faculty (4001 chars, 191
                         faculty names), /contact, /about-us and /library. A name
                         directory is a field list, and on this site that is
                         content.
      "longest line"     the content-free pages top out at 55-69 chars, but so
                         does /faculty/english (425 chars, real).

    Having nothing of one's own is inherently a corpus-level property, so it
    cannot be decided from one page. That is why this takes a freq map.
    """
    for line in (text or "").splitlines():
        key = normalise(line)
        if not key:
            continue
        if freq.get(key, 0) >= site_wide:
            continue
        if is_navigation_label(key):
            continue
        return True
    return False


def find_contentless(pages: dict,
                     min_pages: int = MIN_PAGES_FOR_FREQUENCY) -> set:
    """URLs in this run that carry no content of their own.

    Measured: exactly 7 of 78 — /alumni, /offices, the three tender pages,
    /advertisement-for-visiting-faculty and the 404 page
    /offices/faculty-and-staff. Between them they produced 63 child propositions,
    and not one is about alumni, offices, a tender or a job. They are all
    restatements of the footer: "The contact number for the University of Loralai
    is +92 (824) 410051" and so on. The informative versions of those same topics
    live on the homepage, which says "A tender notice was issued on July 8, 2026"
    and "An advertisement for visiting faculty was published on July 16, 2026" —
    dated, specific, and retrieved by the same queries.

    The 404 page is caught here as well as by looks_dead, and by a different
    route: its own lines are "Oops!", "Something went Wrong...", "Sorry, we
    couldn't find your page." and "Back To Home", all four short enough to read as
    labels, so nothing survives as prose. That overlap is welcome but it does not
    make looks_dead redundant, because the two tests fail in opposite conditions:

        looks_dead        needs one page. Catches ONLY the error template.
        find_contentless  needs the corpus. Catches all seven, but returns an
                          empty set below min_pages.

    So a single-page re-scrape via setup_knowledge_base.py --url is protected by
    looks_dead alone, and a page that is empty without saying so is caught by
    find_contentless alone. Neither covers the other's case.

    Returns URLs, not lines — the caller decides. THE CALLER MUST LOG THEM. A
    page can be empty for two reasons that look identical from here: the page
    really is empty, or _extract_text's selector list missed its content. The
    second is a scraper bug, and silently dropping the page would hide it. Which
    of the two applies cannot be settled without re-fetching the page.

    Below min_pages this returns an empty set, like find_furniture: on a
    single-page re-scrape no line looks site-wide, so every line counts as the
    page's own and the test is meaningless. Erring toward keeping, as everywhere
    else in this module.
    """
    if len(pages) < min_pages:
        return set()
    freq = document_frequency(pages)
    return {url for url, text in pages.items()
            if not page_has_own_content(text, freq)}


# ── alias URLs ────────────────────────────────────────────────
# uoli.edu.pk serves six pages at two URLs each. Measured, byte-identical text,
# with the number of child propositions each URL produced:
#
#     /contact                   29  ==  /about/contact              29
#     /graduate-programs         25  ==  /undergraduate              24
#     /directorate-of-it         43  ==  /offices/directorate-of-it  46
#     /academics/research         4  ==  /research                    4
#     /about                     37  ==  /about-old-123              40
#     /academics/academic-rules 130  ==  /academic-rules              0
#
# 144 of 5486 children — 2.6% of the corpus — are a second copy of a page that is
# already indexed. This is the concrete form of the duplication that damaged the
# scores: the same page competes with itself for the CANDIDATE_K retrieval slots,
# and both copies inflate the document frequency of every term they contain, which
# lowers those terms' IDF for every BM25 query in the corpus, not just these.
# Query-time dedup collapses the copies, but only AFTER they have displaced
# distinct documents that dedup cannot bring back.
#
# WHY THIS CANNOT BE FIXED BY EDITING A URL LIST
# ──────────────────────────────────────────────
# web_scraper.GUARANTEED_PAGES lists BOTH URLs for four of the six groups, so the
# curated list is where the duplication comes from. But it cannot be cleaned by
# inspection either: whether two URLs serve the same page is a fact about the
# website, discoverable only by fetching both and comparing. A hardcoded pairing
# would also rot the moment the site adds or retires an alias. So the grouping is
# computed from content, every run.


def content_key(text: str) -> str:
    """Grouping key for a whole page: its normalised lines, in order.

    Uses normalise() per line rather than the raw bytes, so two URLs that differ
    only in whitespace or heading markers still group. Not looser than that:
    normalise folds case and collapses spaces but keeps digits, "@" and
    punctuation, so a page differing by one phone number or one programme name
    has a different key and will never be treated as an alias.
    """
    return "\n".join(k for k in (normalise(ln) for ln in (text or "").splitlines())
                     if k)


def canonical_url(urls) -> str:
    """The one URL to keep out of a set that all serve the same page.

    Ordered, total, and deliberately independent of the database:

        1. fewest path segments   /contact          beats /about/contact
        2. shortest URL           /about            beats /about-old-123
        3. lexicographic          a tiebreak that always terminates

    It is a rule about URL shape, not about which URL happens to hold rows today,
    because the scraper applies it on a fresh build where no rows exist. That
    means it can disagree with what is already indexed — /academic-rules wins on
    segment count although all 130 children currently sit under
    /academics/academic-rules — so anything acting on an EXISTING database must
    re-point those rows rather than delete them. Metadata updates cost nothing;
    re-embedding 130 children does.
    """
    return min(urls, key=lambda u: (u.rstrip("/").count("/"), len(u), u))


def find_alias_groups(pages: dict) -> dict:
    """{canonical_url: {the other URLs serving the same page}}.

    Only groups of two or more are returned, so the result is empty for a corpus
    with no aliases. Needs no minimum page count — unlike frequency, "these two
    URLs served the same bytes" is decidable from the two pages alone, so this
    works on a two-page re-scrape as well as a full one.
    """
    groups = {}
    for url, text in (pages or {}).items():
        groups.setdefault(content_key(text), set()).add(url)
    out = {}
    for urls in groups.values():
        if len(urls) > 1:
            keep = canonical_url(urls)
            out[keep] = urls - {keep}
    return out


# ── stat counters ─────────────────────────────────────────────
# A line that is nothing but a number, optionally with a "%" or "+" suffix.
# These only occur inside the site's "University of Loralai at a Glance" counter
# widget, where each number is a separate element from its caption.
_BARE_NUMBER = re.compile(r"^[\d,]+\s*[%+]?$")


def find_orphan_stats(pages: dict) -> set:
    """Numbers from the counter widget whose caption was lost in scraping.

    THE DEFECT THIS CATCHES
    ───────────────────────
    The "at a Glance" widget renders as alternating number/caption lines:

        5   / State-of-the-Art Computer Labs
        5   / Hostels Accomodation
        80% / Students Supported Through Financial Aid
        2000+                                 <- caption never made it into the text
        1500+                                 <- caption never made it into the text
        100+ / Faculty
        10+  / Degree Programs

    Two captions are missing. The propositions pipeline then had to write a
    statement about a bare "2000+" sitting next to the word "Faculty", and
    produced three claims that are flatly false:

        "The University of Loralai has over 2000 faculty members."
        "The University of Loralai offers more than 1500 scholarships."
        "UOL has over 2000 faculty members."

    The corpus's own labelled figure is "100+ Faculty". An unlabelled number is
    not a fact, it is an invitation to invent one, so it is removed before the
    text is ever chunked. Measured on the live corpus: exactly 2 orphans, both on
    /about-us, against 18 correctly-paired number lines that are all preserved —
    including the faculty extension/email pairs ("1054" / "hod.cs@uoli.edu.pk"),
    which must survive because they are the only record of those extensions.

    A number is an orphan when the next non-empty line is also a bare number, or
    when it is the last line: in either case no caption follows it.
    """
    orphans = set()
    for text in pages.values():
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            key = normalise(line)
            if not _BARE_NUMBER.match(key):
                continue
            following = lines[i + 1] if i + 1 < len(lines) else ""
            if not following or _BARE_NUMBER.match(normalise(following)):
                orphans.add(key)
    return orphans


def drop_orphan_stats(text: str) -> str:
    """Remove caption-less counter numbers from one page's text.

    Positional, not key-based, and deliberately so. find_orphan_stats returns
    bare keys like "2000+", but removing lines by key would be wrong: if the
    counter widget on one page loses a caption while another page pairs the same
    number correctly, a key-based strip would delete the good line too. Deciding
    per line, from the line that follows it, cannot make that mistake.

    Runs before any frequency test, so it works on a single-page re-scrape.
    """
    if not text:
        return text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    kept = []
    for i, line in enumerate(lines):
        if _BARE_NUMBER.match(normalise(line)):
            following = lines[i + 1] if i + 1 < len(lines) else ""
            if not following or _BARE_NUMBER.match(normalise(following)):
                continue                 # no caption follows — not a fact
        kept.append(line)
    return "\n".join(kept)


def strip_furniture(text: str, furniture: set) -> str:
    """Drop furniture lines from one page's text, preserving everything else.

    Also drops lines that are nothing but markup. _extract_text inserts "#" for
    headings and "•" for list items; when the underlying tag is empty it emits a
    bare "###" or "#####" with no words after it. 52 of 78 pages carry one, and
    one of them lands in the middle of the parent chunk holding the Vice
    Chancellor's name. A line with no alphanumeric character left after markers
    are stripped cannot be content, whatever its frequency, so this test needs no
    threshold and applies even to a single-page re-scrape.

    Blank-line structure is not preserved: _extract_text already emits one
    non-empty line per element, and the propositions pipeline splits on content,
    not on layout.
    """
    if not text:
        return text
    kept = []
    for line in text.splitlines():
        if not line.strip():
            continue
        key = normalise(line)
        if not key:                      # bare "###" / "•" / "|" — pure markup
            continue
        if key in furniture:
            continue
        kept.append(line)
    return "\n".join(kept)


# ── redundant propositions ────────────────────────────────────
# Measured on the live corpus: 5486 children hold only 4727 distinct texts.
# 759 rows — 13.8% — are a second copy of a sentence already stored. The worst:
#
#     57x  The contact number for the University of Loralai is +92 (824) 410051.
#     54x  The University of Loralai is located on Quetta Road, Zerh Karez ...
#     50x  The University of Loralai is located at Zerh Karez, Quetta Road ...
#     44x  The University of Loralai is committed to building trust.
#     42x  The website for the University of Loralai is uoli.edu.pk.
#     40x  The email address for general inquiries is info@uoli.edu.pk.
#
# WHY THIS IS NOT THE SAME PROBLEM AS FURNITURE, AND NOT FIXED BY IT
# ──────────────────────────────────────────────────────────────────
# find_furniture DELIBERATELY KEEPS those address, phone and email lines — see
# "WHY FREQUENCY ALONE IS NOT ENOUGH" at the top of this file. They are the only
# statement of the university's switchboard number anywhere in the corpus, so
# stripping them by frequency would delete the answer to "what is the phone
# number". That decision is correct and stands.
#
# But it has a consequence. The line survives on all 69 pages that carry it, the
# proposition writer reads all 69 pages, and it writes the same sentence 69 times.
# So the duplication is created AFTER furniture stripping, by the extractor, and
# it will be recreated by every rebuild until it is stopped here.
#
# WHY QUERY-TIME DEDUP DOES NOT ALREADY SOLVE IT
# ──────────────────────────────────────────────
# knowledge_base dedups by content in five places, but all five run at the END of
# retrieval. Search looks at CANDIDATE_K=50 candidates; if 20 of those 50 slots
# are the same address sentence, they have already displaced 19 distinct
# documents, and collapsing them afterwards cannot bring those documents back.
# The duplicates also inflate the document frequency of every term they contain,
# which lowers that term's IDF for every BM25 query in the corpus — so the harm
# is not confined to questions about the address.
#
# WHY THE KEY INCLUDES THE CATEGORY
# ─────────────────────────────────
# Agents filter by category before searching. Keeping one copy globally would put
# the phone number in, say, "general" only, and make it unreachable from an agent
# filtered to "offices" — which is exactly the defect that made the fee question
# score zero on all four metrics. One copy PER CATEGORY keeps every filter able to
# find the fact and still removes the redundancy inside each filter. Measured
# cost of that safety: 681 droppable instead of 759.

_REDUNDANT_KEY = re.compile(r"[^a-z0-9]+")


def proposition_key(text: str) -> str:
    """Comparison key for one proposition: lowercase, punctuation folded to space.

    Looser than normalise() on purpose — this compares whole generated sentences,
    not source lines, and the extractor punctuates the same fact inconsistently
    ("+92 (824) 410051" / "+92-824-410051"). Digits are preserved, so 410051 and
    410782 never collapse; two different phone numbers stay two facts.
    """
    return _REDUNDANT_KEY.sub(" ", (text or "").lower()).strip()


def find_redundant(rows) -> list:
    """Which rows are redundant copies. Returns their ids, in input order.

    rows: an iterable of (row_id, text, category, parent_id). Deliberately plain
    tuples rather than Documents or Chroma dicts, so setup_propositions (which
    holds Documents, before anything is embedded) and repair_duplicate_children
    (which holds Chroma rows, after) can both call it and cannot drift apart.

    Two safety rules, both measured as necessary:

      1. one copy per (text, category) — see the note above on category filters.

      2. never drop a row that is its parent's LAST surviving child. A parent's
         full text is only reachable through one of its children; if every child
         of parent P is a duplicate of a child of parent Q, dropping them all
         makes P's text unretrievable, and P and Q hold different text. Measured:
         without this rule 52 parents go dark. With it, 0 do, and 629 rows are
         still droppable.

    First occurrence wins, so the result is deterministic for a given input order.
    """
    rows  = list(rows)
    live  = {}
    for _, _, _, parent_id in rows:
        live[parent_id] = live.get(parent_id, 0) + 1

    seen, drop = set(), []
    for row_id, text, category, parent_id in rows:
        key = (proposition_key(text), category or "")
        if key not in seen:
            seen.add(key)
            continue
        if live.get(parent_id, 0) <= 1:      # last child standing — keep it
            continue
        live[parent_id] -= 1
        drop.append(row_id)
    return drop


# ── OCR blocks ────────────────────────────────────────────────
# web_scraper._extract_images_text appends the text it reads out of images to the
# end of a page's text. These two constants are the delimiters it wraps that text
# in, and strip_image_blocks below is the ONLY sanctioned way to remove it again.
#
# WHY THE BLOCK IS DELIMITED AT ALL
# ─────────────────────────────────
# It used to be opened and never closed:
#
#     [IMAGE CONTENT from https://uoli.edu.pk/wp-content/uploads/.../615805019.jpg]
#     <ocr text>
#
# so anything wanting to remove it had to guess where it ended. clean_ocr_parents
# guessed "at the next blank line". _extract_text emits one non-empty line per
# element, so in most pages there IS no blank line after the marker, and that
# script deleted everything from the marker to the end of the parent. Measured
# damage: 88 parents left holding under half their recorded char_count, 56,527
# characters gone, 461 children pointing at what was left. The receipts survived —
# the Office of Treasurer parent still carries "char_count": 1502 next to 119
# characters of content — which is how the loss was found at all.
#
# A closing delimiter makes the boundary a fact instead of a guess. Any future
# cleanup calls strip_image_blocks and cannot repeat that.
#
# WHY THE URL IS GONE FROM THE MARKER
# ───────────────────────────────────
# It was 100+ characters of upload path per image, embedded in page text and then
# in the propositions written from it — retrievable, matchable by BM25, and
# useful to nobody. The src is logged instead, where provenance belongs.
#
# WHY content_hash STILL SPLITS ON THE OPENING MARKER
# ──────────────────────────────────────────────────
# The OCR text is deliberately excluded from a page's change-detection hash: the
# university swapping one banner image must not make every page carrying it look
# changed and bill a re-embed of all of them. That split relies on the opening
# marker being exactly this string, so it is defined here and imported there
# rather than written out twice.
IMAGE_BLOCK_OPEN = "[IMAGE TEXT]"
IMAGE_BLOCK_CLOSE = "[/IMAGE TEXT]"

_IMAGE_BLOCK = re.compile(
    re.escape(IMAGE_BLOCK_OPEN) + r".*?" + re.escape(IMAGE_BLOCK_CLOSE),
    re.DOTALL,
)
# Unclosed legacy blocks, from the old "[IMAGE CONTENT from <url>]" format. These
# genuinely have no end delimiter, so the only safe reading is the one the emitter
# guaranteed: the marker started a block that ran to the end of the appended
# extras, and the extras are always last. Everything from the first legacy marker
# onward therefore goes, and nothing before it is touched — which is the opposite
# of guessing at a blank line somewhere in the middle.
_LEGACY_IMAGE_OPEN = "[IMAGE CONTENT from"


def strip_image_blocks(text: str) -> str:
    """Remove OCR text from a page's text. Never removes anything else.

    Handles both the delimited current format and the unclosed legacy one. Safe to
    call on text that contains neither — it returns it unchanged.
    """
    if not text:
        return text or ""
    out = _IMAGE_BLOCK.sub("", text)
    if _LEGACY_IMAGE_OPEN in out:
        out = out.split(_LEGACY_IMAGE_OPEN)[0]
    return out.strip()
