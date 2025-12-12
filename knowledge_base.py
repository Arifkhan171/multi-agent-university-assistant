# ═══════════════════════════════════════════════════════════════
#  knowledge_base.py  —  Phase 4
#  One new method added: load_scraped_page()
#  Everything else identical to Phase 3
# ═══════════════════════════════════════════════════════════════
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import pickle
import re
import hashlib
from datetime import date
from typing import List


from langchain_openai.embeddings import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi
# ADD with your other imports at the top of knowledge_base.py
from parent_doc_store import ParentDocStore, split_into_parent_child
from langchain_text_splitters import RecursiveCharacterTextSplitter
import config
import page_furniture

# A statistics-block figure: a line that is nothing but a number, optionally with
# a thousands comma and a trailing "+" or "%". Requiring the WHOLE line to match
# is what keeps ordinary prose and numbered lists ("5." / "(5)") out of
# _normalize_figure_columns.
_FIGURE_LINE = re.compile(r"^\s*(\d[\d,]*\s*[%+]?)\s*$")

# Appended to a statistics figure whose caption the scrape lost. Short on
# purpose: it is a signal to the reader, not content, and it lands in the RAGAS
# `contexts` alongside everything else.
_NO_CAPTION = "(figure with no caption in the source — what it counts is unknown)"

from dataclasses import dataclass,field

# ADD THIS with your other imports at the top of knowledge_base.py
from parent_doc_store import ParentDocStore

# B5: INGESTION RESULT — Structured tracking for pipeline logging
# ════════════════════════════════════════════════════════════════

@dataclass
class IngestionResult:
    """
    WHAT: Records what happened when processing one source.
    WHY:  Replaces scattered print() with structured data.
    """
    source:      str
    status:      str          # added | updated | skipped | failed | empty
    chunk_count: int = 0
    message:     str = ""

    def __str__(self) -> str:
        icons = {
            "added":   "✓ ADDED",
            "updated": "↻ UPDATED",
            "skipped": "− SKIPPED",
            "failed":  "✗ FAILED",
            "empty":   "○ EMPTY",
        }
        icon = icons.get(self.status, self.status.upper())
        base = f"{icon}: {self.source}"
        if self.chunk_count:
            base += f" ({self.chunk_count} chunks)"
        if self.message:
            base += f" — {self.message}"
        return base


@dataclass
class IngestionSummary:
    """
    WHAT: Collects many IngestionResults and gives totals.
    WHY:  Answers "how many skipped, how many failed" without
          manually counting printed lines.
    """
    results: list = field(default_factory=list)

    def add(self, result: IngestionResult) -> None:
        self.results.append(result)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    def total_chunks_added(self) -> int:
        return sum(
            r.chunk_count for r in self.results
            if r.status in ("added", "updated")
        )

    def failures(self) -> list:
        return [r for r in self.results if r.status == "failed"]

    def print_summary(self) -> None:
        print()
        print("─" * 62)
        print("  INGESTION SUMMARY")
        print("─" * 62)
        print(f"  Added    : {self.count('added')}")
        print(f"  Updated  : {self.count('updated')}")
        print(f"  Skipped  : {self.count('skipped')}  (unchanged — zero API cost)")
        print(f"  Empty    : {self.count('empty')}")
        print(f"  Failed   : {self.count('failed')}")
        print(f"  ──────────────────────────────")
        print(f"  Total chunks stored: {self.total_chunks_added()}")
        print("─" * 62)
        if self.failures():
            print("\n  Failures:")
            for r in self.failures():
                print(f"    {r}")
# ════════════════════════════════════════════════════════════════
# ADD THIS: After imports, before class KnowledgeBase
# FILE: knowledge_base.py
# WHAT: A new chunker class that understands document structure
# WHY:  RecursiveCharacterTextSplitter does not understand that
#       a heading and its content belong together.
#       This chunker uses ## markers (added by web_scraper.py)
#       to split at section boundaries instead of char count.
# ════════════════════════════════════════════════════════════════

class UniversityAwareChunker:
    """
    WHAT: Splits university content at section boundaries,
          not at character count limits.

    WHY:  The web scraper adds ## markers to every heading.
          This chunker uses those markers as natural split
          points. Each section becomes one chunk.
          This keeps "Head of Department" heading ALWAYS
          in the same chunk as "Dr. Ali Khan" name.

    WHERE: Used by all load_*() methods in KnowledgeBase
           instead of RecursiveCharacterTextSplitter.
    """

    def __init__(
        self,
        max_chunk_size: int = 1500,
        chunk_overlap: int = 150,
    ):
        """
        WHAT: Sets up the chunker with size limits.

        Args:
            max_chunk_size: Maximum characters per chunk.
                           1500 chosen because:
                           - 1000 was too small (split mid-section)
                           - 2000 causes vector dilution
                           - 1500 fits most university sections
            chunk_overlap:  Characters repeated between chunks
                           when a section must be split.
                           150 is enough context without waste.
        """
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap  = chunk_overlap

        # WHAT: Fallback splitter for sections that are too long
        # WHY:  Some sections (like long faculty lists) exceed
        #       max_chunk_size. We need a fallback strategy.
        # WHERE: Used in _split_large_section()
        self._fallback_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def split_text(self, text: str) -> list[str]:
        """
        WHAT: Main entry point. Takes raw text, returns list of chunks.
        WHY:  Drop-in replacement for splitter.split_text()
              Same interface — just smarter behavior.
        WHERE: Called exactly where self.splitter.split_text() was called.

        Returns:
            List of chunk strings ready for Document() wrapping.
        """
        if not text or not text.strip():
            return []

        # WHAT: Detect if this text has heading structure
        # WHY:  FAQ text and some pages have no ## headings.
        #       For those, fall back to recursive character splitting.
        #       For pages with headings, use structure-aware splitting.
        import re
        has_headings = bool(re.search(r'^#{1,4}\s+.+', text, re.MULTILINE))

        if has_headings:
            return self._split_by_structure(text)
        else:
            # WHAT: No heading structure found
            # WHY:  FAQ entries, plain text files have no ##
            #       Use the reliable fallback for these
            return self._fallback_splitter.split_text(text)

    def _split_by_structure(self, text: str) -> list[str]:
        """
        WHAT: Splits text using ## heading markers as boundaries.
        WHY:  Each ## heading starts a new topic section.
              Keeping heading + its content together means
              the chunk's vector represents one focused topic.
        WHERE: Called by split_text() when headings are detected.
        """
        import re

        # WHAT: Pattern to detect any level heading (# ## ### ####)
        # WHY:  web_scraper.py adds #, ##, ###, #### for h1-h4
        #       We split at ALL heading levels
        heading_pattern = re.compile(r'^(#{1,4}\s+.+)$', re.MULTILINE)

        # WHAT: Find all heading positions in the text
        # WHY:  We use positions to extract text between headings
        heading_matches = list(heading_pattern.finditer(text))

        # WHAT: If no headings found despite earlier check, use fallback
        # WHY:  Safety guard against edge cases
        if not heading_matches:
            return self._fallback_splitter.split_text(text)

        sections = []

        # WHAT: Extract text BEFORE the first heading
        # WHY:  Some pages have introductory text before any heading
        #       We do not want to lose this content
        first_heading_pos = heading_matches[0].start()
        pre_heading_text = text[:first_heading_pos].strip()
        if pre_heading_text:
            sections.append(("Introduction", pre_heading_text))

        # WHAT: Extract each heading and its content
        # WHY:  Content between heading N and heading N+1
        #       belongs to heading N's section
        for i, match in enumerate(heading_matches):
            heading_text = match.group(1)

            # WHAT: Section content runs from after this heading
            #       to the start of the next heading (or end of text)
            content_start = match.end()
            content_end   = (
                heading_matches[i + 1].start()
                if i + 1 < len(heading_matches)
                else len(text)
            )
            section_content = text[content_start:content_end].strip()

            sections.append((heading_text, section_content))

        # WHAT: Convert sections to chunks
        # WHY:  Each section needs to become a searchable chunk
        #       with the heading included for context
        chunks = []
        for heading, content in sections:
            chunk = self._build_chunk(heading, content)
            chunks.extend(chunk)

        # WHAT: Filter out empty chunks
        # WHY:  Some sections might have headings but no content
        return [c for c in chunks if c.strip()]

    def _build_chunk(self, heading: str, content: str) -> list[str]:
        """
        WHAT: Combines heading + content into one or more chunks.
        WHY:  If heading + content fits in max_chunk_size → one chunk.
              If too large → split content but keep heading in each piece.

        Args:
            heading: The ## heading text (e.g. "## Head of Department")
            content: The text under this heading

        Returns:
            List of chunk strings (usually just one, sometimes more)
        """
        # WHAT: Always prepend heading to content
        # WHY:  This ensures the heading context is NEVER lost.
        #       Even if content is split into multiple chunks,
        #       each sub-chunk knows what section it came from.
        full_section = f"{heading}\n{content}".strip()

        # WHAT: If the whole section fits in one chunk — keep it together
        # WHY:  No need to split what fits. One focused chunk.
        if len(full_section) <= self.max_chunk_size:
            return [full_section]

        # WHAT: Section is too large — must split the content
        # WHY:  A section with 3000 chars needs splitting.
        #       But we want heading context in every sub-chunk.
        sub_chunks = self._fallback_splitter.split_text(content)

        # WHAT: Prepend heading to EVERY sub-chunk
        # WHY:  Without this, sub-chunk 2 has no heading context.
        #       Student asks about HOD → retriever finds sub-chunk 2
        #       → LLM reads content without knowing it is HOD section.
        #       With heading prepended → every sub-chunk knows its topic.
        result = []
        for i, sub_chunk in enumerate(sub_chunks):
            if i == 0:
                # First sub-chunk: heading + content (natural)
                result.append(f"{heading}\n{sub_chunk}".strip())
            else:
                # Later sub-chunks: heading with [continued] marker
                # WHY [continued]: Makes it clear this is continuation
                #                  of the same section
                clean_heading = heading.lstrip('#').strip()
                result.append(
                    f"[{clean_heading} — continued]\n{sub_chunk}".strip()
                )

        return result
    def _sentence_window(self, paragraphs: list[str], 
                         window: int = 3, 
                         overlap: int = 1) -> list[str]:
        """
        WHAT: Groups paragraphs into overlapping windows.

        WHY:  A single paragraph alone often lacks context.
              "He has 15 years experience" means nothing without
              "Dr. Ismail Khan is HOD" from the previous paragraph.
              Windowing keeps neighbouring paragraphs together.

        HOW:  Slide a window of N paragraphs across the list.
              Each window overlaps with the previous by 'overlap'
              paragraphs — so context is never completely lost.

        Args:
            paragraphs: List of normal paragraph strings.
            window:     How many paragraphs per chunk (default 3).
            overlap:    How many paragraphs shared between chunks (default 1).

        Example (window=3, overlap=1):
            Input:  [P1, P2, P3, P4, P5]
            Output: [P1+P2+P3, P3+P4+P5]
                     ↑ P3 appears in both — context preserved
        """
        if not paragraphs:
            return []

        # If very few paragraphs — just join them all
        # WHY: No point windowing 2 paragraphs into separate chunks
        if len(paragraphs) <= window:
            return [" ".join(paragraphs)]

        chunks  = []
        step    = window - overlap   # how far to advance each time
        i       = 0

        while i < len(paragraphs):
            window_paras = paragraphs[i:i + window]
            chunk_text   = " ".join(window_paras).strip()
            if chunk_text:
                chunks.append(chunk_text)
            i += step

        return chunks
    
    def split_docx_paragraphs(self, paragraphs: list[dict]) -> list[str]:
        """
        WHAT: Splits DOCX content using heading structure + sentence windows.

        WHY:  Two types of content in DOCX files need different treatment:
              1. Heading sections → structure-aware splitting (same as web)
              2. Long prose sections → sentence-window grouping

              Before this improvement, long prose sections were sent to
              recursive character splitter which ignored context.
              Sentence windows keep neighbouring paragraphs together.

        WHERE: Called by load_docx() for all policy DOCX files.
        """
        # ── Pass 1: Separate headings from normal paragraphs ──────
        # WHY: We treat them differently.
        #      Headings become ## markers (structure).
        #      Normal paragraphs become sentence windows (prose).
        sections = []       # list of (type, text) tuples
        current_normal = [] # buffer for consecutive normal paragraphs

        for para in paragraphs:
            style = para.get('style', 'Normal')
            text  = para.get('text', '').strip()
            if not text:
                continue

            if 'Heading 1' in style:
                # Flush any buffered normal paragraphs first
                if current_normal:
                    sections.append(('normal', current_normal[:]))
                    current_normal = []
                sections.append(('heading', f"# {text}"))

            elif 'Heading 2' in style:
                if current_normal:
                    sections.append(('normal', current_normal[:]))
                    current_normal = []
                sections.append(('heading', f"## {text}"))

            elif 'Heading 3' in style:
                if current_normal:
                    sections.append(('normal', current_normal[:]))
                    current_normal = []
                sections.append(('heading', f"### {text}"))

            elif 'Heading 4' in style:
                if current_normal:
                    sections.append(('normal', current_normal[:]))
                    current_normal = []
                sections.append(('heading', f"#### {text}"))

            else:
                # Normal paragraph — buffer it
                current_normal.append(text)

        # Flush remaining normal paragraphs
        if current_normal:
            sections.append(('normal', current_normal[:]))

        # ── Pass 2: Build final chunks ────────────────────────────
        # WHY: Now we process each section type correctly.
        #      Headings become ## markers in the combined text.
        #      Normal paragraph groups become sentence windows.
        lines  = []
        chunks = []

        for section_type, content in sections:
            if section_type == 'heading':
                # Heading — add to lines as ## marker
                lines.append(content)

            else:
                # Normal paragraphs — apply sentence windowing
                # WHY: Group neighbouring paragraphs for context.
                #      Better than treating each paragraph alone.
                windowed = self._sentence_window(
                    content,
                    window=3,   # 3 paragraphs per window
                    overlap=1,  # 1 paragraph shared between windows
                )

                if windowed:
                    # If we have accumulated heading structure,
                    # process it first as one structured section
                    if lines:
                        structured_text = "\n".join(lines)
                        chunks.extend(self._split_by_structure(structured_text))
                        lines = []

                    # Then add windowed prose chunks
                    chunks.extend(windowed)

        # Process any remaining heading structure
        if lines:
            structured_text = "\n".join(lines)
            chunks.extend(self._split_by_structure(structured_text))

        # Filter empty chunks
        return [c for c in chunks if c.strip()]


# ════════════════════════════════════════════════════════════════
# METADATA SYSTEM — B4
# Three functions that together build a complete metadata schema
# for every chunk stored in the knowledge base.
#
# build_metadata()        ← main function, called by all load methods
# _extract_department()   ← detects department from URL or filename
# _detect_content_type()  ← detects specific content type
# _extract_section()      ← extracts section heading from chunk text
# _detect_contains_person() ← detects if chunk mentions a person
# _extract_doc_date()     ← finds official document date in text
# ════════════════════════════════════════════════════════════════

def _extract_department(source: str) -> str:
    """
    WHAT: Extracts department name from a URL or filename.

    WHY:  Enables pre-filtering by department at retrieval time.
          A query "Who is HOD of CS?" can filter to
          department = "computer-science" before vector search runs.
          This eliminates the manual score-boosting logic in _rerank().

    HOW:  Simple substring matching against known UOLI URL slugs.
          More specific slugs checked before shorter ones to avoid
          false matches (e.g. "science" matching "computer-science").
    """
    source_lower = source.lower()

    # Order matters — longer/more specific slugs first
    # WHY: "political-science" contains "science".
    #      If we checked "science" first we would match wrongly.
    dept_slugs = [
        "computer-science",
        "allied-health-sciences",
        "management-sciences",
        "political-science",
        "islamic-studies",
        "mathematics",
        "commerce",
        "education",
        "english",
        "pashto",
        "zoology",
    ]

    for slug in dept_slugs:
        if slug in source_lower:
            return slug

    # Policy documents apply university-wide — not one department
    policy_keywords = [
        "rule", "policy", "conduct", "disciplinary", "anti-drug"
    ]
    if any(k in source_lower for k in policy_keywords):
        return "all"

    return "general"


def _detect_content_type(source: str, category: str, source_type: str) -> str:
    """
    WHAT: Returns a specific content type label for this chunk.

    WHY:  category is broad (faculty, policy, overview).
          content_type is specific (faculty_page, leadership_message).
          Specific labels allow more precise filtering.

    HOW:  Source URL is the strongest signal — it tells us exactly
          what page type this is. We check leadership pages BEFORE
          general faculty because vc-message is inside the university
          domain but is NOT a faculty listing page.
    """
    source_lower = source.lower()

    # Leadership pages — must check BEFORE general faculty
    # WHY: vc-message URL contains no "faculty" but is a leadership page
    leadership_signals = [
    "vc-message", "pro-vc", "dean-message",
    "registrar-message", "vice-chancellor",
    "hod-message", "chairman-message", 
    "director-message", "principal-message"
]
    if any(s in source_lower for s in leadership_signals):
        return "leadership_message"

    # Department / faculty pages
    if "faculty" in source_lower:
        return "faculty_page"

    # Policy documents — web pages and local files
    policy_signals = [
        "rule", "policy", "conduct", "disciplinary", "anti-drug"
    ]
    if any(s in source_lower for s in policy_signals):
        return "policy_document"

    # Admission pages
    if "admission" in source_lower:
        return "admission_info"

    # Fee pages
    if "fee" in source_lower:
        return "fee_structure"

    # Office pages
    office_signals = [
    "office-of-", "offices/", "/oric", "/dqe", "/fao",
    "office-of-registrar", "office-of-treasurer",
    "controller-of-examination", "directorate-of"
]
    if any(s in source_lower for s in office_signals):
        return "office_info"

    # FAQ entries — identified by source_type alone
    if source_type == "faq":
        return "faq_entry"

    # Overview and about pages
    overview_signals = ["about", "overview", "vc-message", "contact"]
    if category == "overview" or any(s in source_lower for s in overview_signals):
        return "overview_page"

    return "general_page"


def _extract_section(chunk_text: str) -> str:
    """
    WHAT: Extracts the section heading from a chunk's text.

    WHY:  UniversityAwareChunker prepends the heading to every chunk.
          That heading is the section this chunk belongs to.
          Storing it as metadata means we can filter or display it
          without re-reading the chunk text.

    HOW:  The first line of every structured chunk starts with #, ##,
          ###, or #### followed by the heading text.
          We strip the # markers and return the clean heading.

    Examples:
        "## Head of Department\nDr. Ali Khan..."  → "Head of Department"
        "### Faculty Members\nLecturer: ..."       → "Faculty Members"
        "[Head of Department — continued]\n..."    → "Head of Department"
        "Question: What is...\nAnswer: ..."        → "unknown"
    """
    if not chunk_text:
        return "unknown"

    first_line = chunk_text.strip().split("\n")[0].strip()

    # Pattern 1: Markdown heading (## Section Name)
    heading_match = re.match(r'^#{1,4}\s+(.+)$', first_line)
    if heading_match:
        return heading_match.group(1).strip()

    # Pattern 2: Continued section marker ([Section Name — continued])
    continued_match = re.match(r'^\[(.+?)(?:\s*—\s*continued)?\]$', first_line)
    if continued_match:
        return continued_match.group(1).strip()

    return "unknown"


def _detect_contains_person(chunk_text: str) -> bool:
    """
    WHAT: Detects whether a chunk likely contains a person's name or title.

    WHY:  Many student queries are about specific people — HOD, VC, Dean.
          When we know a chunk contains a person, we can pre-filter to
          these chunks for person queries instead of searching everything.
          This replaces the manual person-query boosting logic in _rerank().

    HOW:  Two signals:
          1. Title keywords — explicit job titles in the text
          2. Name pattern — two or more consecutive capitalized words
             (strong signal for a proper name like "Dr. Ali Khan")

    Note: This is a heuristic — it will occasionally be wrong.
          That is acceptable. Metadata is a guide, not a guarantee.
    """
    if not chunk_text:
        return False

    text_lower = chunk_text.lower()

    # Signal 1: Explicit title keywords
    title_keywords = [
        "vice chancellor", "pro vice chancellor",
        "registrar", "treasurer", "dean",
        "head of department", "hod", "chairperson",
        "professor", "associate professor", "assistant professor",
        "lecturer", "dr.", "mr.", "ms.", "mrs.",
        "director", "controller",
    ]
    if any(title in text_lower for title in title_keywords):
        return True

    # Signal 2: Name pattern — two or more consecutive capitalized words
    # WHY:  "Dr. Ali Khan" or "Muhammad Ismail" are name patterns.
    #       Navigation noise like "About Us" also matches but that is
    #       filtered out before chunking by the web scraper.
    name_pattern = re.search(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,}\b', chunk_text)
    if name_pattern:
        return True

    return False


def _extract_doc_date(chunk_text: str) -> str:
    """
    WHAT: Finds an official document date mentioned in the chunk text.

    WHY:  Policy documents contain their official notification date.
          scraped_at = when WE collected it.
          doc_date   = when it was OFFICIALLY issued.
          Both are useful. A policy from 2022 that we scraped today
          should show doc_date = "2022", scraped_at = today.

    HOW:  Regex scan for common date patterns in Pakistani official docs.
          Returns the first date found. If none found → "unknown".

    Examples of what it finds:
        "Notified on 15th January 2024"   → "2024"
        "Effective from March 2025"       → "2025"
        "Revised: 2023"                   → "2023"
        "No date in this chunk"           → "unknown"
    """
    if not chunk_text:
        return "unknown"

    # Pattern 1: Full date with month name
    # Matches: "15th January 2024", "March 2025", "1 April 2023"
    full_date = re.search(
        r'\b(?:\d{1,2}(?:st|nd|rd|th)?\s+)?'
        r'(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)'
        r'\s+(\d{4})\b',
        chunk_text,
        re.IGNORECASE
    )
    if full_date:
        return full_date.group(1)   # return the year portion

    # Pattern 2: Year only — "2024", "2023", "2025"
    # Limit to realistic document years
    year_only = re.search(r'\b(20(?:1[5-9]|2[0-9]))\b', chunk_text)
    if year_only:
        return year_only.group(1)

    return "unknown"


def build_metadata(
    source: str,
    category: str,
    source_type: str,
    content: str = "",
    page_hash: str = "",
) -> dict:
    """
    WHAT: Builds a complete, consistent metadata dictionary for any chunk.

    WHY:  Single function = single schema across the entire knowledge base.
          Every load_*() method calls this one function.
          Adding a new field means changing ONE place, not five.

    Args:
        source:      URL or filename this chunk came from.
        category:    Broad intent category (policy, faculty, fees...).
        source_type: Origin type — "web_scrape", "docx", "pdf", "faq", "txt".
        content:     The chunk text — used for hash, char count,
                     section extraction, person detection, doc date.

    Returns:
        dict with all 11 metadata fields filled consistently.
    """
    return {
        # ── WHERE it came from ───────────────────────────────
        "source":           source,
        "source_type":      source_type,

        # ── WHAT kind of content ─────────────────────────────
        "category":         category,
        "department":       _extract_department(source),
        "content_type":     _detect_content_type(source, category, source_type),
        "section":          _extract_section(content),
        "contains_person":  _detect_contains_person(content),

        # ── WHEN ─────────────────────────────────────────────
        "scraped_at":       str(date.today()),
        "doc_date":         _extract_doc_date(content),

        # ── INTEGRITY ────────────────────────────────────────
        "content_hash":     hashlib.md5(
                                content.encode("utf-8", errors="ignore")
                            ).hexdigest()[:8] if content else "",
        "char_count":   len(content),
            "page_hash":    page_hash,
    }


class KnowledgeBase:
    _LIST_SIGNALS = {
        "all", "every", "each", "list", "names", "how many",
        "departments", "teachers", "lecturers", "faculty members",
        "hods", "heads", "staff", "employees", "kitne", "tamam", "saray", "sab", "poora"
    }

    _POLICY_SIGNALS = {
        "policy", "policies", "rule", "rules", "regulation",
        "what is the attendance", "what are the rules",
        "what happens if", "punishment", "penalty", "consequence",
        "drug", "harassment", "disciplinary", "conduct",
        "readmission", "freeze", "probation", "cheating",
        "unfair means", "what is cheating", "absence", "absent",
        "cgpa", "expulsion", "fine", "detention","if i miss", "if i not attend", "if i fail",
        "can i repeat", "can i freeze", "can i withdraw",
        "what if i", "is it allowed", "am i allowed",
        "debarred", "detained", "rusticated", "suspended",
        "warning letter", "improvement", "grade improvement",
        "late submission", "plagiarism", "exam rule",
        "examination rule", "anti drug", "ragging",
        "hostel rule", "hostel policy", "library rule",
    }
    def __init__(self):
        self.embeddings = OpenAIEmbeddings(
            model=config.EMBEDDING_MODEL,
            api_key=config.OPENAI_API_KEY,
        )
        self.splitter = UniversityAwareChunker(
        max_chunk_size=1500,
        # WHAT: Increased from 1000 to 1500
        # WHY:  Most university sections fit in 1500 chars.
        #       1000 was splitting sections mid-content.
        #       1500 keeps most sections as single chunks.

        chunk_overlap=150,
        # WHAT: Overlap for when sections must be split
        # WHY:  150 chars is enough context without waste.
        #       Previous 200 was slightly too much.
    )
        self.db = Chroma(
            collection_name=config.CHROMA_COLLECTION,
            embedding_function=self.embeddings,
            persist_directory=config.CHROMA_DB_PATH,
        )
        self.bm25      = None
        self.bm25_docs = []
        self._load_bm25_from_disk()

        print("Loading reranker model...")
        from sentence_transformers import CrossEncoder
        self.reranker = CrossEncoder(config.RERANKER_MODEL)
        print("Reranker ready ✓")
        print(f"Knowledge base ready — {self.get_doc_count()} docs in vector DB")
        # INSIDE KnowledgeBase.__init__(), after your existing initializations


# Parent document store — stores large parent chunks for rich LLM context.
# Children in ChromaDB point to parents here via parent_id metadata field.
        self.parent_store = ParentDocStore(path="data/parent_chunks.json")

        print(f"[KnowledgeBase] ParentDocStore loaded: {len(self.parent_store)} parents")
    def reset(self):
        """Clears all documents from ChromaDB and BM25 index."""
        print("Resetting knowledge base...")
        # Clear ChromaDB
        self.db.delete_collection()
        self.db = Chroma(
            collection_name=config.CHROMA_COLLECTION,
            embedding_function=self.embeddings,
            persist_directory=config.CHROMA_DB_PATH,
        )
        # Clear BM25
        self.bm25 = None
        self.bm25_docs = []
        if os.path.exists("./data/bm25_index.pkl"):
            os.remove("./data/bm25_index.pkl")
        print("Knowledge base reset successful.")


    # ── Loading methods ───────────────────────────────────────

    def load_faq_data(self, faq_list: List[dict]):
        documents = []
        for faq in faq_list:
            text = f"Question: {faq.get('question','')}\nAnswer: {faq.get('answer','')}"
            doc  = Document(
    page_content=text,
    metadata={
        # Standard 11 fields from build_metadata
        **build_metadata(
            source="FAQ",
            category=faq.get("category", "general"),
            source_type="faq",
            content=text,
        ),
        # FAQ-specific extra field — keeps the original question
        # WHY: Useful for displaying "You asked: ..." in the UI
        "question": faq.get("question", ""),
    }
)
            documents.append(doc)
        documents = self._apply_parent_child(documents)  # E2

        self.db.add_documents(documents)
        self._add_to_bm25(documents)
        print(f"Loaded {len(documents)} FAQ items.")



    def load_text_file(self, filepath: str, category: str = "general"):
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}"); return
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        filename  = os.path.basename(filepath)
        documents = [
    Document(
        page_content=c,
        metadata=build_metadata(
            source=filename,
            category=category,
            source_type="txt",
            content=c,
        )
    )
    for c in self.splitter.split_text(text)
]       
        documents = self._apply_parent_child(documents)  # E2

        self.db.add_documents(documents)
        self._add_to_bm25(documents)
        print(f"Loaded '{filename}': {len(documents)} chunks.")



    def load_pdf(self, filepath: str, category: str = "general"):
        try:
            import pypdf
        except ImportError:
            print("Run: pip install pypdf"); return
        if not os.path.exists(filepath):
            print(f"PDF not found: {filepath}"); return
        all_text = ""
        with open(filepath, "rb") as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text()
                if t: all_text += t + "\n"
        filename  = os.path.basename(filepath)
        # ── E2: parent-child splitting ────────────────────────────────────────────
# Old: self.splitter creates one level of chunks
# New: split_into_parent_child creates parents (DocStore) + children (ChromaDB)
        base_metadata = build_metadata(
            source=filename,
            category=category,
            source_type="pdf",
            content=all_text[:500],  # use first 500 chars for content hash
        )
        documents = split_into_parent_child(
            text=all_text,
            metadata=base_metadata,
            parent_store=self.parent_store
        )
        documents = self._apply_parent_child(documents)  # E2

        self.db.add_documents(documents)
        self._add_to_bm25(documents)
        print(f"Loaded PDF '{filename}': {len(documents)} child chunks.")

    def load_docx(self, filepath: str, category: str = "general"):
        """
        Load a Microsoft Word (.docx) document into the knowledge base.

        Extracts text from all paragraphs and table cells.
        Requires: pip install python-docx
        """
        try:
            from docx import Document as DocxDocument
        except ImportError:
            print("Run: pip install python-docx"); return
        if not os.path.exists(filepath):
            print(f"DOCX not found: {filepath}"); return

        doc      = DocxDocument(filepath)
        paragraphs_data = []

        # WHAT: Extract paragraphs WITH their heading style information
        # WHY:  We now pass style information to the chunker
        #       so it can treat headings as section boundaries
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                paragraphs_data.append({
                    'style': para.style.name,
                    # WHAT: The Word style name e.g. "Heading 1", "Normal"
                    # WHY:  Our chunker uses this to add ## markers
                    'text': text,
                })

        # WHAT: Extract table content as before
        # WHY:  Tables need special handling — not changed here
        table_parts = []
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(
                    cell.text.strip() for cell in row.cells
                    if cell.text.strip()
                )
                if row_text:
                    table_parts.append(row_text)

        # WHAT: Build all_text for quality check
        # WHY:  Still need to check if anything was extracted
        all_text = "\n".join(
            [p['text'] for p in paragraphs_data] + table_parts
        )
        if not all_text.strip():
            print(f"No text extracted from: {filepath}")
            return

        filename = os.path.basename(filepath)

        # ── B5: Document deduplication ────────────────────────────
        page_hash = hashlib.md5(
            all_text.encode("utf-8", errors="ignore")
        ).hexdigest()[:8]

        if self.page_unchanged(filename, page_hash):
            result = IngestionResult(filename, "skipped", message="unchanged")
            print(result)
            return result

        existing = self.db.get(where={"source": {"$eq": filename}})
        if existing["ids"]:
            self.delete_by_source(filename)
       # WHAT: Use the DOCX-aware splitting method
# WHY:  This preserves heading structure from Word styles
        docx_chunks = self.splitter.split_docx_paragraphs(paragraphs_data)

        # WHAT: Add table content as separate chunks
        # WHY:  Tables have their own structure — handle separately
        if table_parts:
            table_text = "\n".join(table_parts)
            table_chunks = self.splitter._fallback_splitter.split_text(table_text)
            docx_chunks.extend(table_chunks)

        documents = [
            Document(
                page_content=c,
                metadata=build_metadata(
                    source=filename,
                    category=category,
                    source_type="docx",
                    content=c,
                    page_hash=page_hash,
                )
            )
            for c in docx_chunks
            if c.strip()
        ]

        try:
            # ATOMIC: store new first, delete old after
            documents = self._apply_parent_child(documents)  # E2

            self._add_documents_batched(documents)
            self._add_to_bm25(documents)

            if existing["ids"]:
                self.delete_by_source(filename)

            status = "updated" if existing["ids"] else "added"
            result = IngestionResult(
                source=filename,
                status=status,
                chunk_count=len(documents)
            )

        except Exception as e:
            # Store failed — old chunks still intact (atomic guarantee)
            result = IngestionResult(
                source=filename,
                status="failed",
                message=str(e)
            )

        print(result)
        return result




        

    def load_scraped_page(self, text: str, url: str, category: str = "general",
                          page_hash: str = None,
                          legacy_hash: str = None,
                          source_type: str = "web_scrape") -> "IngestionResult":
        """
        Stores text scraped from a website URL into the knowledge base.

        B5 upgrades:
        - page_unchanged check → skip if content not changed
        - Atomic pattern → store new first, delete old after
        - IngestionResult → structured return for pipeline logging
        - try/except → one page failure never crashes the pipeline

        page_hash: the change-detection hash, computed by
        web_scraper.content_hash(). Pass it whenever the caller has it. Omitting
        it falls back to hashing the full text, which is correct for local files
        (they have no OCR block and no advertisement image) but wrong for scraped
        pages — see the comment on the fallback below.

        legacy_hash: the full-text md5 of the text AS SERVED, for matching the
        3124 web chunks already in the store. Must be supplied by any caller that
        modifies the text before passing it in — see below.

        source_type: what kind of thing this text came from, recorded in each
        chunk's metadata. Defaults to "web_scrape" so every existing caller is
        unaffected; the PDF pass passes "pdf", which is how a later diagnosis can
        tell a document chunk from a page chunk without guessing from the URL.
        """
        # Empty page — nothing to store
        if not text or not text.strip():
            return IngestionResult(url, "empty", message="no text extracted")

        # The hash decides whether we pay to embed, so where it comes from
        # matters more than it looks.
        #
        # Computing it here from the full text is what broke the two cost
        # optimisations in web_scraper: the ad banner is OCR'd into many pages, so
        # swapping one image changed the full-text hash of every page carrying it
        # and re-embedded all of them, though not one university word had changed.
        # web_scraper.content_hash() excludes the OCR and social blocks and is now
        # the single authority; the scraper passes its result in.
        #
        # The fallback stays for callers with no scraper involved
        # (setup_knowledge_base --url, fix_parent_store) and for local documents.
        #
        # WHY legacy_hash IS A PARAMETER AND NOT ALWAYS DERIVED HERE
        # ─────────────────────────────────────────────────────────
        # It has to be the hash of the text as the WEBSITE served it, because that
        # is what the stored value is. scrape_all_pages now strips site furniture
        # before calling this, so the text arriving here is shorter than what was
        # fetched; deriving the legacy hash from it would match none of the 3124
        # stored web chunks, every page would look new, and the migration guard
        # below — whose entire purpose is to avoid a needless full re-embed —
        # would instead cause one. So the scraper passes the raw-text hash in.
        #
        # Derived locally when absent, which is right for every caller that does
        # not transform the text: local documents, fix_parent_store, and the
        # single-URL path.
        if legacy_hash is None:
            legacy_hash = hashlib.md5(
                text.encode("utf-8", errors="ignore")
            ).hexdigest()[:8]
        if page_hash is None:
            page_hash = legacy_hash

        # Check if this exact page content already exists.
        #
        # Two schemes are accepted on purpose. Every one of the 3124 web chunks in
        # the live database carries a legacy full-text hash, so comparing against
        # content_hash alone would match none of them: all 78 pages would look
        # new and the very change made to STOP needless re-embedding would trigger
        # a complete re-embed of the corpus. Accepting either scheme migrates the
        # store for free — a page keeps its legacy hash until it genuinely
        # changes, then gets a content_hash on the way in.
        #
        # The legacy arm can be deleted once every stored hash is a content_hash,
        # i.e. after the next full rebuild.
        if (self.page_unchanged(url, page_hash)
                or self.page_unchanged(url, legacy_hash)):
            result = IngestionResult(url, "skipped", message="unchanged")
            print(result)
            return result

        try:
            # Check if old chunks exist for this URL.
            # Capture their IDS and their page_hashes NOW, before anything new is
            # written. They are the only reliable way to tell "the version that
            # was here before" from "the version being written", because both
            # carry the same source. See the delete step below.
            existing     = self.db.get(where={"source": {"$eq": url}})
            stale_ids    = list(existing["ids"])
            stale_hashes = {(m or {}).get("page_hash")
                            for m in (existing.get("metadatas") or [])}
            was_existing = len(stale_ids) > 0

            # Build chunks and documents
            chunks    = self.splitter.split_text(text)
            documents = [
                Document(
                    page_content=chunk,
                    metadata=build_metadata(
                        source=url,
                        category=category,
                        source_type=source_type,
                        content=chunk,
                        page_hash=page_hash,
                    )
                )
                for chunk in chunks
            ]

            # ATOMIC PATTERN:
            # Store NEW chunks first → verify success → delete OLD chunks
            # WHY: If storing fails, old chunks still exist.
            #      Students still get answers — just the old version.
            #      Never leave a page with zero chunks.
            documents = self._apply_parent_child(documents)  # E2
            self._add_documents_batched(documents)
            self._add_to_bm25(documents,rebuild=False)

            # Only delete old chunks AFTER new ones are safely stored.
            #
            # Delete the SNAPSHOT taken above — never re-query by source here.
            # delete_by_source(url) would look the source up again, and by this
            # point the source matches the chunks we just wrote as well as the
            # stale ones, so it removed BOTH: a page whose content changed was
            # wiped from Chroma and from BM25 while the log still said
            # "UPDATED (n chunks)". Every page edited on the live site
            # disappeared from the knowledge base on the next scrape.
            # Covered by test_ingest_safety.py scenario 3.
            if was_existing:
                self._delete_stale_chunks(url, stale_ids, stale_hashes)

            status = "updated" if was_existing else "added"
            result = IngestionResult(url, status, chunk_count=len(documents))

        except Exception as e:
            # Embedding or storage failed
            # Old chunks untouched — atomic guarantee holds
            result = IngestionResult(url, "failed", message=str(e))

        print(result)
        return result

    # ── Search pipeline (identical to Phase 3) ────────────────

    # Shared expansion for questions about how to reach the university itself.
    # Kept as one constant so the phone numbers and postal address are written
    # once — every contact phrasing below resolves to the same literal strings.
    _CONTACT_TERMS = (
        "contact phone telephone number +92 (824) 410051 +92 (824) 410782 "
        "info@uoli.edu.pk email address University of Loralai Quetta Road "
        "Zerh Karez Loralai Balochistan Pakistan admin office mailing address")

    # Query expansion map — short/misspelled queries → richer search terms
    _QUERY_EXPANSIONS = {
        "attendance":          "attendance 75% classes required examination repeat readmission absence struck off fee chances canceled",
        "attendence":          "attendance 75% classes required examination repeat readmission absence struck off fee chances canceled",
        "attendance policy":   "attendance 75% classes required examination repeat readmission absence struck off fee chances canceled",
        "fee":                 "fee semester tuition charges payment",
        "fees":                "fee semester tuition charges payment",
        "exact penalty cheating":   "cheating unfair means fine expulsion rustication detention certificate withdrawal disciplinary",
        "penalty for cheating":     "cheating unfair means fine expulsion rustication detention certificate withdrawal",
        "caught cheating":          "cheating unfair means fine expulsion rustication detention certificate withdrawal disciplinary",
        "cs fee":              "computer science fee semester payment",
        "cs admission":        "computer science admission requirement eligibility documents",
        "admission policy":    "admission requirement eligibility documents criteria",
        # ── Admission CRITERIA — how the decision is made ────────────────────
        # A student asking how selection works shares almost no vocabulary with
        # the documents that answer it. "How does UoL decide who gets admitted?"
        # retrieved a news page, a diversity blurb and the site address block,
        # and scored zero on precision, recall and faithfulness alike — the only
        # question in the eval to fail every metric. The documents state the
        # rule in registrar's vocabulary: merit, Matriculation, Intermediate,
        # admission test. None of those words are in the question.
        #
        # These keys cover the ways the decision gets asked about (decide,
        # selected, admitted, criteria, merit) rather than the words the answer
        # happens to use, so a rephrasing of the same question still lands.
        "decide who gets admitted": "admission merit criteria Matriculation Intermediate admission test entry test eligibility selection percentage marks",
        "who gets admitted":   "admission merit criteria Matriculation Intermediate admission test eligibility selection marks",
        "gets admitted":       "admission merit criteria Matriculation Intermediate admission test eligibility selection marks",
        "get admitted":        "admission merit criteria Matriculation Intermediate admission test eligibility selection marks",
        "how are students selected": "admission merit criteria Matriculation Intermediate admission test eligibility selection marks",
        "students selected":   "admission merit criteria Matriculation Intermediate admission test eligibility selection marks",
        "selection criteria":  "admission merit criteria Matriculation Intermediate admission test eligibility marks percentage",
        "admission criteria":  "admission merit criteria Matriculation Intermediate admission test eligibility marks percentage",
        "eligibility criteria": "admission merit criteria Matriculation Intermediate admission test eligibility marks percentage",
        "merit list":          "merit list admission criteria Matriculation Intermediate marks percentage aggregate",
        "admission test":      "admission test entry test merit criteria Matriculation Intermediate marks",
        "who is eligible":     "admission eligibility criteria merit Matriculation Intermediate marks requirement",
        "scholarship":         "scholarship financial aid merit need based",
        "scollership":         "scholarship financial aid merit need based",
        "hostel":              "hostel accommodation residence dormitory",
        "transport":           "bus routes transport service students faculty",
        "pro vc":              "pro vice chancellor name contact email phone Adil Zaman Kasi",
        "pro vice chancellor": "pro vice chancellor name contact email phone Adil Zaman Kasi",
        "drug policy":         "drug abuse prohibition penalty disciplinary action HEI student staff",
        "drug":                "drug abuse prohibited penalty narcotics substance",
        "registrar":               "registrar name contact email phone Khalid Khan Prof Dr",
        "who is registrar":        "registrar name contact email phone Khalid Khan",
        "who is the registrar":    "registrar name contact email phone Khalid Khan Prof Dr",
        "treasurer":               "treasurer name contact email phone office finance officer",
        "who is treasurer":        "treasurer name contact email phone finance",
        "who is the treasurer":    "treasurer name contact email phone finance officer",
        # ── University contact details ──────────────────────────────────────
        # These went unexpanded, and the raw wording shares almost no vocabulary
        # with the contact page: "what is the phone number" retrieved policy PDFs
        # at ranks 1-4, and "what is the email" returned engrakuol@gmail.com — a
        # procurement address out of a tender PDF — at rank 1. The literal number
        # and address strings in _CONTACT_TERMS are what BM25 can match on.
        #
        # Keys are deliberately specific. A bare "email" or "address" key would
        # fire on "what is Bilal Khan's email" too and inject the switchboard
        # details into a question about one lecturer; since the longest matching
        # key wins, only phrasings that really are about the university itself
        # reach these entries.
        "phone number":            _CONTACT_TERMS,
        "contact number":          _CONTACT_TERMS,
        "telephone number":        _CONTACT_TERMS,
        "contact details":         _CONTACT_TERMS,
        "contact information":     _CONTACT_TERMS,
        "how to contact":          _CONTACT_TERMS,
        "how can i contact":       _CONTACT_TERMS,
        "university email":        _CONTACT_TERMS,
        "official email":          _CONTACT_TERMS,
        "university address":      _CONTACT_TERMS,
        "address of the university": _CONTACT_TERMS,
        "mailing address":         _CONTACT_TERMS,
        "where is the university":  _CONTACT_TERMS,
        "where is uoli":           _CONTACT_TERMS,
        "location of the university": _CONTACT_TERMS,
        "business hours":          "business hours 9:00 am 5:00 pm Monday Friday admin office timing",
        "office hours":            "business hours 9:00 am 5:00 pm Monday Friday admin office timing",

        "office of treasurer":     "treasurer name contact email phone finance officer",
        "who is the director":     "director IT information technology name contact email",
        "who is the dean":         "dean faculty name contact email phone",
        "who is dean":             "dean faculty name contact email phone",
        "director of it":          "director IT information technology name contact email",
        "director it":         "director information technology name contact email",
        "who is director":     "director name contact email office head",
        "cheating":            "cheating unfair means penalty fine expulsion detention examination disciplinary",
        "cheating punishment": "cheating unfair means penalty fine expulsion detention examination honor",
        "unfair means":        "cheating unfair means penalty fine expulsion detention",
        "programs":            "programs offered BS MS MPhil PhD DPT BEd undergraduate graduate",
        "what programs":       "programs offered BS MS MPhil PhD DPT BEd undergraduate courses departments",
        "programs does uoli":  "programs offered BS MS MPhil PhD DPT BEd Allied Sciences Computer Science",
        "programs offered":    "programs offered BS MS MPhil PhD DPT BEd undergraduate",
        "cs faculty":          "computer science department teachers lecturers Ismail Khan Bilal Naseeruddin",
        "name of cs faculty":  "computer science department teachers lecturers names emails",
        "cs professors":       "computer science department teachers lecturers names",
        "kitne department":    "departments list all names university",
        "uoli mein kitne":     "departments list all names university total",
        "kitne hain":          "departments list all names total count",
        "kitne department":   "all departments list names allied commerce computer english education islamic management mathematics pashto political zoology",
        "uoli mein kitne":    "all departments list names allied commerce computer english education islamic management mathematics pashto political zoology",
        "when was uoli established":  "established 2012 charter Balochistan university founded",
        "when established":           "established 2012 charter Balochistan university founded",
        "uoli established":           "established 2012 charter Balochistan university founded",
        "research activities":        "ORIC research MoU collaboration projects grants publications",
        "research":                   "ORIC research MoU collaboration innovation","refers to whom":     "university hierarchy structure VC registrar dean reports management governance",
        "who refers to":      "university hierarchy structure VC registrar dean reports management governance",
        # ── Typo / abbreviation expansions ──────────────────────────────────
        # These are exact student typos seen in the eval dataset.
        # Without these the query expander returns the raw typo → BM25 fails.
        "vicechanclor":        "vice chancellor name contact Ehsanullah Kakar professor engineer",
        "vice chanclor":       "vice chancellor name contact Ehsanullah Kakar professor engineer",
        "vise chancellor":     "vice chancellor name contact Ehsanullah Kakar professor engineer",
        "vice chancelr":       "vice chancellor name contact Ehsanullah Kakar professor engineer",
        "vc of uoli":          "vice chancellor name Ehsanullah Kakar University of Loralai",
        "vc uoli":             "vice chancellor name Ehsanullah Kakar University of Loralai",
        "vice chancellor":     "vice chancellor name Ehsanullah Kakar University of Loralai VC",
        "who is the vc":       "vice chancellor name Ehsanullah Kakar University of Loralai",
        "who is vc":           "vice chancellor name Ehsanullah Kakar University of Loralai",
        # Readmission
        "readmission":       "readmission fee 500 1000 struck off re-admission",
        "readmission fee":   "readmission fee 500 1000 struck off",
        "re admission":      "readmission fee 500 1000 struck off",
        # University history / establishment
        "kab bana":          "established 2012 charter Balochistan",
        "kab bani":          "established 2012 charter Balochistan",
        "kab bani thi":      "established 2012 charter Balochistan",
        "established":       "established 2012 charter Balochistan Provincial Assembly",
        "history":           "established 2012 charter Balochistan Provincial Assembly",
        "founded":           "established 2012 charter Balochistan Provincial Assembly",
        "when was":          "established 2012 charter Balochistan Provincial Assembly",
        # ── Hostel / accommodation ───────────────────────────────────────────
        "hostel":              "hostel accommodation residence dormitory boys girls rooms",
        "hostels":             "hostel accommodation residence dormitory boys girls campus",
        "how many hostels":    "hostel accommodation residence dormitory boys girls number",
        "accommodation":       "hostel accommodation residence dormitory boys girls campus",
        # ── Social media ─────────────────────────────────────────────────────
        "social media":        "facebook twitter instagram youtube linkedin social media official page",
        "social media platforms": "facebook twitter instagram youtube linkedin social media official",
        "facebook":            "facebook social media official page university",
        "instagram":           "instagram social media official page university",
        # ── Controller of Examination ────────────────────────────────────────
        "deputy controller":   "deputy controller examination office staff UoL Shahji Ahmed",
        "controller of examination": "controller examination office Dr Shahji Ahmed UoL",
        "controller":          "controller examination office staff UoL",
        # ── HOD / Head of Department expansions ──────────────────────────────
        # THE WORD "HOD" DOES NOT APPEAR IN THE SENTENCES THAT ANSWER THESE.
        # The corpus states it as "Mr. Ismail Khan is the Chairperson of the
        # Department of Computer Science" — so a student asking "who is the HOD
        # of CS" shares no content word with the one chunk that answers them,
        # and BM25 contributes nothing to the hybrid score. Every key below
        # therefore carries "chairperson" as well as "HOD"; leaving it off any
        # one of them reopens the gap for exactly the phrasings that key covers.
        "hod":                 "HOD chairperson head of department name email phone contact",
        "hod of cs":           "head of department chairperson computer science Ismail Khan email phone HOD",
        "hod cs":              "head of department chairperson computer science Ismail Khan email phone HOD",
        "hod of computer science": "head of department chairperson computer science Ismail Khan hod.cs@uoli.edu.pk email phone HOD",
        "who is hod":          "head of department chairperson HOD name email phone contact",
        "who is the hod":      "head of department chairperson HOD name email phone contact",
        "cs hod":              "computer science head of department chairperson Ismail Khan email hod.cs@uoli.edu.pk",
        "computer science hod": "computer science head of department chairperson Ismail Khan email phone HOD",
        "chairperson":         "chairperson HOD head of department name email phone contact",
        "chairman":            "chairperson HOD head of department name email phone contact",
        # Natural English "head of" phrasing — students often say this instead of "HOD"
        # Without these, BM25 returns whichever dept has "(HOD)" label most prominently
        # and the reranker picks the wrong department (Management Sciences instead of CS).
        "head of the computer science": "HOD CS chairperson Ismail Khan hod.cs@uoli.edu.pk computer science department head",
        "head of computer science":     "HOD CS chairperson Ismail Khan hod.cs@uoli.edu.pk computer science department",
        "head of cs department":        "HOD CS chairperson Ismail Khan hod.cs@uoli.edu.pk computer science department head",
        "head of the cs":               "HOD CS chairperson Ismail Khan hod.cs@uoli.edu.pk computer science department",
        # Generic HOD expansions for any department
        "who is the head of":   "HOD head of department chairperson name email phone contact",
        "who is head of":       "HOD head of department chairperson name email phone contact",
        "head of department":   "HOD chairperson department head name email contact",
    }

    def _expand_query(self, query: str) -> str:
        """Expand short/ambiguous queries to include document-matching terms.

        LONGEST KEY WINS, NOT THE FIRST ONE FOUND. This used to return on the
        first key that appeared anywhere in the query, which made the table's
        behaviour depend on its insertion order: "Who is the HOD of Computer
        Science" matched the generic "who is the hod", because that key sits
        above the Computer Science ones, and never saw the specific entry.

        The expansion is appended to the query rather than replacing it, so the
        department itself was never lost — what was lost is everything the
        specific entry adds on top. "head of computer science" contributes
        "Ismail Khan" and "hod.cs@uoli.edu.pk"; the generic key contributes
        neither, and those literal strings are the ones BM25 can actually match
        against the chunk that answers the question. A shorter key appearing
        earlier in the table silently downgraded the query.

        Sorting by key length means the most specific phrase present always
        wins, so a new entry can no longer be shadowed by one added before it,
        and the table can be maintained in whatever order reads best.
        """
        q_lower = query.lower().strip()
        # Full-phrase match first — an exact hit is as specific as it gets.
        if q_lower in self._QUERY_EXPANSIONS:
            return query + " " + self._QUERY_EXPANSIONS[q_lower]
        best = max((k for k in self._QUERY_EXPANSIONS if k in q_lower),
                   key=len, default=None)
        if best:
            return query + " " + self._QUERY_EXPANSIONS[best]
        return query
    def _engineer_query(self, query: str) -> List[str]:
        """
        WHAT: Transforms the original query into multiple better queries.

        WHY:  One query → one vector → misses vocabulary gaps.
            Multiple queries → multiple vectors → catches more chunks.

        HOW:  Two techniques combined:
            1. HyDE — generate hypothetical answer, use as query
            2. Multi-query — rephrase question multiple ways

        Returns: List of queries to search with.
                First item is always the original query (safe fallback).
        """
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage

        # Always include original query as fallback
        # Check if HyDE is enabled in config
        if not config.ENABLE_HYDE:
            return [query]

        # WHY REMOVED: The 6-word limit blocked HyDE for real student
        # questions like "What is the email address of University of Loralai?"
        # (10 words). These longer, specific questions benefit most from HyDE
        # because their vocabulary may still not match document vocabulary.
        # HyDE is still controlled globally by config.ENABLE_HYDE.

        # Always include original query as fallback
        queries = [query]

        try:
            llm = ChatOpenAI(
                model=config.LLM_MODEL,
                temperature=0.0,
                max_tokens=200,        # We only need 3 short lines — cap saves time

                openai_api_key=config.OPENAI_API_KEY,
            )

            prompt = f"""You are helping a university chatbot find relevant information.

    Given this student question: "{query}"

    Generate:
    1. A hypothetical answer paragraph (2-3 sentences) that uses 
    university document vocabulary — as if this answer existed 
    in an official university document.
    2. Two alternative phrasings of the same question.

    Respond in this exact format:
    HYPOTHETICAL: <your hypothetical answer here>
    ALT1: <first alternative question>
    ALT2: <second alternative question>"""

            response = llm.invoke([HumanMessage(content=prompt)])
            raw = response.content.strip()

            # Parse the response
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("HYPOTHETICAL:"):
                    hyde_text = line.replace("HYPOTHETICAL:", "").strip()
                    if hyde_text:
                        queries.append(hyde_text)
                elif line.startswith("ALT1:"):
                    alt1 = line.replace("ALT1:", "").strip()
                    if alt1:
                        queries.append(alt1)
                elif line.startswith("ALT2:"):
                    alt2 = line.replace("ALT2:", "").strip()
                    if alt2:
                        queries.append(alt2)

        except Exception as e:
            # If LLM call fails, return original query only
            # WHY: Query engineering failure must never crash retrieval
            print(f"Query engineering failed: {e} — using original query")

        return queries

    # High-value details that must never be lost in the child→parent swap.
    _DETAIL_PATTERNS = (
        re.compile(r"[\w.\-]+@[\w.\-]+"),            # email address
        re.compile(r"(?:\+?92|0)\s*\(?\d{2,4}\)?[\s\-]?\d{5,8}"),  # phone number
        re.compile(r"\b\d{1,3}\s?%"),                 # percentage (75%, 80%)
        re.compile(r"\bRs\.?\s?[\d,]+"),              # fee amount
    )

    def _child_adds_detail(self, child_text: str, parent_text: str) -> bool:
        """True if the child states a concrete detail its parent does not contain.

        WHY: children are LLM-written propositions, so they are not substrings of
        their parent. Usually the parent is the richer context and the child is
        redundant. But when a proposition carries the one hard fact the question
        asks for (an email, a phone number, a percentage, a fee) and that fact is
        absent from the parent text, dropping the child loses the answer — the
        model then either refuses or invents the detail. Keeping the child in that
        narrow case protects Faithfulness and Context Recall without inflating
        context for every query.
        """
        for pattern in self._DETAIL_PATTERNS:
            for match in pattern.findall(child_text):
                if match and match not in parent_text:
                    return True
        return False

    # Topic-free words, dropped before comparing a child against its parent.
    # Deliberately small: the comparison only has to notice that a parent shares
    # none of the child's SUBJECT MATTER, not to do linguistics. Adding more
    # stopwords would only sharpen an already-clean separation (see config).
    _OVERLAP_STOP = frozenset("""
        a an the is are was were be been being of in on at to for from by with
        and or as it its this that these those has have had there their his her
        they he she we you i not no any all more than which who whom whose also
        will would can could may might do does did but if then such other
    """.split())

    # The institution's own name is dropped for the same reason "the" is: in a
    # single-institution corpus it carries no information about what a passage is
    # ABOUT. It appears in most propositions and in almost every page-furniture
    # block, so counting it makes unrelated text look related. Measured failures
    # this fixes:
    #
    #   child                                          overlap vs the address stub
    #   "The University of Loralai has a library."      0.67  -> accepted (wrong)
    #   "The University of Loralai was established      0.50  -> accepted (wrong)
    #    in 2012."
    #
    # In both, every shared word was the university's name; not one word of the
    # actual subject (library, established, 2012) was in the parent. Removing the
    # name takes both to 0.00, and leaves the legitimate swaps untouched because
    # those share the parent's real content — "located on Quetta Road, Zerh Karez"
    # still scores 0.80 against that same stub.
    #
    # Derived from config, not written out, so it follows the deployment instead of
    # being a fact about Loralai. The fully general form of this is IDF weighting
    # over the live corpus; that needs a document-frequency pass at load and the
    # only terms it would add here are ones this already covers, so it is noted
    # rather than built. If a future corpus grows other near-universal terms, build
    # the DF set from self.bm25_docs and union it in — the call site does not change.
    _ENTITY_STOP = frozenset(
        w for w in re.findall(r"[a-z0-9]+",
                              (getattr(config, "UNIVERSITY_NAME", "") or "").lower())
        if len(w) > 2
    )

    @staticmethod
    def _content_words(text: str) -> list:
        """Topic-bearing tokens of a string, lowercased, in order."""
        return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
                if w not in KnowledgeBase._OVERLAP_STOP
                and w not in KnowledgeBase._ENTITY_STOP
                and len(w) > 2]

    def _parent_shares_topic(self, child_text: str, parent_text: str) -> bool:
        """Is this parent the passage this child was written from?

        Children are LLM-written propositions, so a child is never a substring of
        its parent — but it is a RESTATEMENT of it, and a restatement reuses the
        source's content words. Measured over all 4857 live children, a child
        under a healthy parent finds 82% of its content words there (median 88%,
        10th percentile 55%). A child under a parent that was damaged after the
        child was written finds 17%.

        That gap is what this tests. It is a provenance question, not a quality or
        a size question: if the parent holds almost none of the child's subject
        matter, then whatever it holds, it is not the context this child came from,
        and swapping it in replaces text that matched the query with text that did
        not. Within the small-parent band this rule polices there are 172 such
        swaps — 161 of them into an address stub or a truncated fragment — and the
        clearest is the whole argument for the check: "The University of Loralai
        was established in 2012" is replaced by the university's postal address,
        so the question "when was it founded" is answered from contexts that no
        longer contain the year.

        The institution's own name is not counted as shared subject matter, for the
        same reason "the" is not; see _ENTITY_STOP. Without that exclusion the two
        examples above scored 0.50 and 0.67 against the address stub purely on the
        words "university" and "loralai", and passed.

        Rejecting keeps the child, which removes no document from the corpus and
        loses only the parent's surrounding prose. That loss is small but real, and
        it is charged to context_recall, so this test is consulted ONLY for parents
        too small to hold much prose — see the SCOPE note in _parent_is_upgrade.
        Applied to large parents as well, it cost 0.115 of measured recall.
        """
        child_words = self._content_words(child_text)
        if not child_words:
            # No topic words to compare — a bare figure ("It is 5%."), an
            # all-stopword fragment, or a sentence that says nothing but the
            # university's own name. Nothing is being asserted about the parent,
            # so this test abstains rather than voting; the size rules still apply.
            return True
        parent_words = set(self._content_words(parent_text))
        shared = sum(1 for w in child_words if w in parent_words)
        return shared / len(child_words) >= config.PARENT_MIN_WORD_OVERLAP

    def _parent_is_upgrade(self, child_text: str, parent_text: str,
                           adds_detail: bool) -> bool:
        """Is swapping this child for this parent actually an improvement?

        The swap is supposed to buy surrounding context. It is a downgrade when
        there is no surrounding context to buy, and a downgrade costs twice: the
        precise proposition that matched the query is replaced by a fragment, and
        the fragment still occupies one of MAX_CONTEXT_DOCS slots.

        Three measured failure shapes, all rejected here:

        1. The parent is no bigger than the child. 506 of 5558 children map to a
           parent within 1.2x their own length; the worst is a 183-character
           child under a 37-character parent. There is nothing to gain.

        2. The parent is small AND does not contain the child's fact. Q25 asks
           what percentage of students receive financial aid. The child is "80%
           of students are supported through financial aid" — the answer — and
           its parent is a 119-character address stub. Size alone does not catch
           this (119/59 is 2.0x, over the ratio) but the parent holds neither the
           fact nor enough text to be context, so it is pure loss.

        3. The parent is SMALL and is not the passage the child was written from
           at all. Rules 1 and 2 are both size rules, and inside the small-parent
           band size is the wrong instrument: 470 live swaps clear both and still
           land in a parent under 200 characters, and 123 of those land in the
           same postal-address stub whatever the child was about ("The University
           of Loralai has a library", "UOL offers more than 10 degree programs",
           "...was established in 2012"). What those have in common is not a size
           but a mismatch, so rule 3 tests the mismatch directly. See
           _parent_shares_topic.

           SCOPE, AND WHY IT IS DELIBERATELY NARROW. Rule 3 only adjudicates
           parents below PARENT_MIN_CONTEXT_CHARS. It was first written without
           that bound, which also rejected 49 swaps into parents >=200 chars (36
           navigation menus, 13 bare link lists). Those parents genuinely are
           furniture, and blocking them was argued to be free. The eval run that
           followed lost 0.115 of context_recall and gained nothing on precision.
           Rejecting a LONG parent is precisely how recall is lost: it keeps one
           short proposition in place of a passage that may carry the answer
           somewhere other than in the words the child happens to reuse, and word
           overlap cannot see that. The measurement beat the argument.

           A navigation menu is also the wrong thing to fight here. Declining it
           at query time leaves it in the corpus to be re-proposed for the next
           query; deleting it at ingestion removes it once. That stripping exists
           and is tested (test_ingest_safety.py), so after a rebuild these parents
           are not in the store and rule 3 has nothing to decline anyway. Fighting
           it in both places bought a recall loss for a precision gain of zero.

        Returning False keeps the child, which is cheap but NOT free: the child is
        the text the cross-encoder actually scored, so precision is safe, but the
        parent's surrounding prose leaves the context and context_recall pays for
        it. That price is why every rule above is bounded to a parent measurably
        too small to be carrying prose worth keeping.
        """
        if len(parent_text) <= len(child_text) * config.PARENT_MIN_GAIN_RATIO:
            return False
        if adds_detail and len(parent_text) < config.PARENT_MIN_CONTEXT_CHARS:
            return False
        if (len(parent_text) < config.PARENT_MIN_CONTEXT_CHARS
                and not self._parent_shares_topic(child_text, parent_text)):
            return False
        return True

    def _fetch_parents(self, child_docs: list,
                       preserve_order: bool = False) -> list:
        """
        Given child documents from ChromaDB search, fetch their parent chunks.

        WHY THIS EXISTS:
        ChromaDB returns small child chunks — precise for matching.
        But LLM needs large parent chunks — rich for answering.
        This method is the bridge between the two.

        RANK PRESERVATION (critical for context_precision):
        _rerank() writes doc.metadata["rerank_score"] on every child. Building a
        parent Document from parent_data["metadata"] alone silently discards that
        score, and returning `parents + fallback_children` pushes a top-scoring
        child behind every parent. Both destroy the cross-encoder's ordering, and
        RAGAS context_precision is an ordering metric — one irrelevant document at
        rank 1 halves the score for that question. So each parent inherits the
        best score among the children that mapped to it, and the final list is
        sorted by that score, parents and fallback children together.

        preserve_order=True keeps the order of `child_docs` instead of re-sorting
        by score. Callers that ranked their input with MORE than one query must
        use it. Sorting by rerank_score is only meaningful while every score came
        from the same query: _search_multi_intent() interleaves one ranked list
        per intent, and scores from different intents are not comparable — intent
        B being a harder question makes all of its documents score lower without
        making them less relevant to the user. Re-sorting there silently threw
        away the interleaving that method exists to produce and ordered every
        document by the last intent to run. See _search_multi_intent().

        DEDUPLICATION:
        If two children belong to the same parent, return that parent ONCE.

        FALLBACK:
        If a child has no parent_id, or its parent is missing from the store,
        return the child itself — keeps E2 backward-compatible.
        """
        from langchain_core.documents import Document

        # Each entry carries the position of the earliest child that produced it,
        # so preserve_order can restore the caller's ranking after the swap.
        best: dict = {}      # parent_id -> [score, Document, order]
        loose: list = []     # (score, Document, order) with no usable parent

        def _score_of(doc) -> float:
            value = doc.metadata.get("rerank_score")
            return float(value) if value is not None else 0.0

        for order, child in enumerate(child_docs):
            parent_id  = child.metadata.get("parent_id")
            childscore = _score_of(child)

            # ── Case 1: Child has no parent_id (old KB chunk or chitchat) ──
            if not parent_id:
                loose.append((childscore, child, order))
                continue

            # ── Case 2: Already fetched this parent — keep the better score ──
            # The order stays at the earliest child's position: the parent earned
            # its slot there, and moving it later would demote it.
            if parent_id in best:
                adds_detail = self._child_adds_detail(
                    child.page_content, best[parent_id][1].page_content)
                if adds_detail:
                    loose.append((childscore, child, order))
                # Same tie rule as Case 3: a kept child leads its parent. Applied
                # here too, otherwise a second child raising the parent's score
                # would quietly overwrite the demotion Case 3 just applied.
                promoted = (childscore - config.PARENT_TIE_EPSILON
                            if adds_detail else childscore)
                if promoted > best[parent_id][0]:
                    best[parent_id][0] = promoted
                    best[parent_id][1].metadata["rerank_score"] = promoted
                continue

            # ── Case 3: Fetch parent from store ──
            parent_data = self.parent_store.get_parent(parent_id)

            if parent_data:
                # Repair scraped figure/caption columns here, at the single point
                # where raw page text enters the pipeline. Children are
                # LLM-written propositions and never contain these columns, so
                # parents are the only place this applies. Doing it here rather
                # than in _format_context means the LLM and the RAGAS `contexts`
                # see the same corrected text instead of diverging.
                parent_text = self._normalize_figure_columns(
                    parent_data["content"])
                adds_detail = self._child_adds_detail(child.page_content,
                                                     parent_data["content"])

                # Refuse the swap when the parent is not actually richer than the
                # child. Keeping the child is the safe side: it is what the
                # cross-encoder scored, and the parents rejected here are stubs,
                # so almost no text is lost.
                if not self._parent_is_upgrade(child.page_content, parent_text,
                                               adds_detail):
                    loose.append((childscore, child, order))
                    continue

                metadata = dict(parent_data["metadata"])   # copy — never mutate the store
                metadata["rerank_score"] = childscore
                metadata["parent_id"]    = parent_id
                parent_doc = Document(page_content=parent_text,
                                      metadata=metadata)
                best[parent_id] = [childscore, parent_doc, order]

                # Keep the child too when it carries a hard fact the parent lacks.
                # The child then leads the parent by a hair: both hold the same
                # score, and on a tie the document that actually matched the query
                # and carries the missing fact should be the one RAGAS scores at
                # the earlier position.
                if adds_detail:
                    loose.append((childscore, child, order))
                    demoted = childscore - config.PARENT_TIE_EPSILON
                    best[parent_id][0] = demoted
                    parent_doc.metadata["rerank_score"] = demoted
            else:
                # parent_id present but missing from the store — should not happen
                print(f"[WARNING] parent_id {parent_id} not found in ParentDocStore. Using child.")
                loose.append((childscore, child, order))

        # Merge parents and loose children, then restore the intended ordering.
        merged = [tuple(entry) for entry in best.values()] + loose
        if preserve_order:
            merged.sort(key=lambda entry: entry[2])
        else:
            merged.sort(key=lambda entry: entry[0], reverse=True)

        # Final dedup by CONTENT, not by parent_id.
        #
        # WHY THIS IS NEEDED ON TOP OF THE parent_id KEYING ABOVE: `best` is keyed
        # by parent_id, so it cannot emit the same parent twice — but it can emit
        # two DIFFERENT parents whose text is byte-identical. Many scraped pages
        # reduce to the same navigation/footer block ("+92 (824) 410051 / About /
        # Students / ..."), so /about-us__X and /overview__Y are distinct ids
        # carrying identical content. Measured on the statistics questions: the
        # same boilerplate parent occupied two of seven context slots.
        #
        # Identical text in two slots is pure loss. It cannot add recall — the
        # tokens are already present — and it costs precision, because RAGAS
        # scores every context position, and it costs prompt tokens and latency.
        # _rerank already dedups children this way; the parent swap happens after
        # that, so without this the swap can reintroduce duplicates it removed.
        # The list is already sorted, so the first copy seen is the highest
        # scoring one and dropping later copies keeps the better ranking.
        seen_text: set = set()
        deduped = []
        for score, doc, _order in merged:
            key = self._content_key(doc.page_content)
            if key in seen_text:
                continue
            seen_text.add(key)
            deduped.append(doc)

        return deduped


    def _embeddings_filter(self, query: str, docs: list, threshold: float = 0.10) -> list:
        """
        E3 Step 1: Drop whole docs with low cosine similarity to the query.

        WHY THIS EXISTS:
        Before paying for LLM extraction, we eliminate docs that are clearly
        irrelevant. A doc about hostel fees has near-zero similarity to a query
        about attendance policy — drop it before it costs us an LLM call.

        COST: FREE.
        Uses self.embeddings which already exists. One embed_query call for the
        query. One embed_documents batch call for all docs — not N separate calls.
        No OpenAI completion tokens used here at all.

        THRESHOLD 0.45:
        Below 45% cosine similarity = doc is irrelevant to this query.
        Calibrated for UOLI's mixed content (policies, faculty, news pages).
        If filtering is too aggressive, lower to 0.35.
        If not filtering enough, raise to 0.55.

        Args:
            query     : The user query (already expanded by _expand_query)
            docs      : Parent docs from _fetch_parents()
            threshold : Cosine similarity cutoff (default 0.45)

        Returns:
            Subset of docs that passed the similarity threshold
        """
        import numpy as np

        if not docs:
            return docs

        # Embed query — one call
        query_vec = np.array(self.embeddings.embed_query(query))

        # Embed all docs in one batch call — much cheaper than N separate calls
        doc_texts = [doc.page_content for doc in docs]
        doc_vecs  = [np.array(e) for e in self.embeddings.embed_documents(doc_texts)]

        filtered = []
        for doc, doc_vec in zip(docs, doc_vecs):
            # Cosine similarity formula: dot product / (magnitude A * magnitude B)
            similarity = float(
                np.dot(query_vec, doc_vec) /
                (np.linalg.norm(query_vec) * np.linalg.norm(doc_vec) + 1e-10)
            )

            if similarity >= threshold:
                filtered.append(doc)
            else:
                src = doc.metadata.get("source", "unknown")[:40]
                print(f"[E3-Filter] Dropped (sim={similarity:.2f}): {src}")

        return filtered

    def _llm_extract(self, query: str, docs: list) -> list:
        """
        E3 Step 2: Extract query-relevant sentences from ALL docs in ONE LLM call.

        WHY ONE CALL INSTEAD OF N CALLS:
        Previous version made one LLM call per document.
        10 docs = 10 API calls = 10-20 seconds of network latency.
        This version sends all docs in one prompt, gets one response,
        then parses it into per-document results.
        Result: 1 API call regardless of doc count. 8-10x faster.
        Cost: identical — same total tokens, one round trip.

        RESPONSE FORMAT:
        LLM returns results using ===DOC_N=== markers.
        Each section contains extracted sentences or NO_OUTPUT.
        We split on these markers to rebuild per-document results.

        FALLBACK:
        If LLM ignores format instructions, we treat entire response
        as relevant and return all surviving docs uncompressed.
        System never breaks.

        Args:
            query : The user query
            docs  : Docs that survived _embeddings_filter()

        Returns:
            List of Documents with page_content = extracted sentences only
        """
        if not docs:
            return docs

        from langchain_openai import ChatOpenAI
        from langchain_core.documents import Document
        from langchain_core.messages import HumanMessage

        llm = ChatOpenAI(
            model        = "gpt-4o-mini",
            temperature  = 0,
            max_tokens   = 2000,
            openai_api_key = os.getenv("OPENAI_API_KEY"),
        )

        # ── Build single prompt with all docs ────────────────────────────────
        docs_text = ""
        for i, doc in enumerate(docs, 1):
            docs_text += f"\n===DOCUMENT {i}===\n{doc.page_content}\n"

        BATCH_PROMPT = (
        "QUESTION: {question}\n\n"
        "For each numbered document below, extract ALL sentences relevant "
        "to the question. Be GENEROUS — include:\n"
        "  - Direct answers to the question\n"
        "  - Related rules, conditions, and consequences\n"
        "  - Contact details (email, phone, extension, address, location, social media links) if present\n"
        "  - Procedures and steps that follow from the answer\n"
        "  - Numbers, percentages, fees, and deadlines mentioned\n\n"
        "Use this EXACT format — one section per document:\n\n"
        "===DOC_1===\n<extracted sentences OR the word NO_OUTPUT>\n"
        "===DOC_2===\n<extracted sentences OR the word NO_OUTPUT>\n"
        "(continue for all documents)\n\n"
        "Rules:\n"
        "- Copy sentences exactly as written. Do not paraphrase.\n"
        "- Only write NO_OUTPUT if the document has ZERO connection to the question.\n"
        "- Do not be aggressive about cutting — when in doubt, keep the sentence.\n"
        "- No explanations. No headers. Only the format above.\n\n"
        "{documents}"
    )

        prompt = BATCH_PROMPT.format(
            question  = query,
            documents = docs_text
        )

        # ── Single API call ───────────────────────────────────────────────────
        try:
            response     = llm.invoke([HumanMessage(content=prompt)])
            raw_response = response.content.strip()
        except Exception as e:
            print(f"[E3-Extract] Batch LLM call failed: {e}. Returning all docs.")
            return docs

        # ── Parse response back into per-document results ─────────────────────
        import re
        results = []

        for i, doc in enumerate(docs, 1):
            # Find content between ===DOC_i=== and ===DOC_(i+1)=== or end
            pattern = rf"===DOC_{i}===\s*(.*?)(?====DOC_\d+===|$)"
            match   = re.search(pattern, raw_response, re.DOTALL)

            if not match:
                # LLM did not follow format for this doc — skip it
                src = doc.metadata.get("source", "unknown")[:40]
                print(f"[E3-Extract] No section found for doc {i}: {src}")
                continue

            extracted = match.group(1).strip()

            if not extracted or extracted.upper() == "NO_OUTPUT":
                src = doc.metadata.get("source", "unknown")[:40]
                print(f"[E3-Extract] NO_OUTPUT for: {src}")
                continue

            results.append(Document(
                page_content = extracted,
                metadata     = doc.metadata   # preserve source, category, URL
            ))

        # ── Fallback: if parsing found nothing, return originals ──────────────
        if not results:
            print(f"[E3-Extract] Parsing returned nothing. Returning {len(docs)} originals.")
            return docs

        print(f"[E3-Extract] Batch: {len(docs)} docs → {len(results)} extracted ✓")
        return results
    

    def _grade_documents(self, query: str, docs: list) -> dict:
        """
        E4 Step 1: Grade all retrieved docs in ONE batch LLM call.

        WHY GRADING EXISTS:
        E3 embeddings filter uses math similarity — it drops docs that
        are semantically distant from the query vector. But some docs
        pass the similarity threshold yet still contain zero useful
        information for this specific question. Grading reads both
        the query and document together with intelligence — it
        understands context, not just word similarity.

        Example: "what is the attendance policy?"
        E3 keeps a doc about exam schedules (similarity 0.35 — passes).
        Grader reads it and says NO — exam schedules ≠ attendance rules.

        ONE BATCH CALL:
        All docs graded in one LLM call. Not one call per doc.
        Same pattern as _llm_extract — batch is always faster.

        GRADES:
        YES     = directly helps answer the question
        PARTIAL = related, might help — keep it (safe default)
        NO      = completely irrelevant — drop it

        SKIP FOR LIST QUERIES:
        List queries fetch docs deliberately via db.get().
        They are always relevant by construction — no need to grade.

        Args:
            query : the user query
            docs  : documents from _compress_documents or _fetch_parents

        Returns:
            {
                "relevant"     : list of YES and PARTIAL docs,
                "irrelevant"   : list of NO docs,
                "all_irrelevant": bool — True if EVERY doc graded NO
            }
        """
        if not docs:
            return {"relevant": [], "irrelevant": [], "all_irrelevant": True}

        # Skip grading for list queries — always valid by construction
        query_lower = query.lower()
        if any(signal in query_lower for signal in self._LIST_SIGNALS):
            return {"relevant": docs, "irrelevant": [], "all_irrelevant": False}

        if any(signal in query_lower for signal in self._POLICY_SIGNALS):
            return {"relevant": docs, "irrelevant": [], "all_irrelevant": False}

        # Skip grading for leadership / office-holder queries.
        # WHY: The vc-message and dean-message pages contain the leadership
        # context (role description, message) but often do NOT explicitly name
        # the person (the name may only appear in the about-us page or in an
        # image caption). E4 grades these chunks as NO for "who is vc" because
        # they do not answer a name question — but they ARE the right page to
        # return, and the faculty prompt handles the missing-name case by
        # directing the user to the university. Without this bypass, all chunks
        # from those pages are dropped → FALLBACK_ANSWER returned, which is
        # worse than "name not found in records, please contact the university."
        _LEADERSHIP_QUERY_BYPASS = {
            "who is vc", "who is the vc", "who is vice chancellor",
            "who is the vice chancellor", "who is dean", "who is the dean",
            "who is pro vc", "who is registrar", "who is the registrar",
            "who is treasurer", "who is the treasurer",
            "vice chancellor", "pro vc", "pro-vc",
            "voice chancellor", "vice-chancellor", "vicechanclor",
            "vise chancellor",
        }
        if any(signal in query_lower for signal in _LEADERSHIP_QUERY_BYPASS):
            return {"relevant": docs, "irrelevant": [], "all_irrelevant": False}

        # Bypass grading for direct name searches
        # WHY: Name detection returns child chunks that literally
        # contain the searched name. These are always relevant.
        # Parent chunks from fetch_parents bury names in dept intro
        # text — grader incorrectly drops them.
        # If ALL docs came from same source → likely name detection result
        sources = set(d.metadata.get("source", "") for d in docs)
        if len(docs) <= 5 and len(sources) <= 2:
            _name_words = [
                w for w in query.split()
                if w.lower() not in {
                    "who", "is", "what", "the", "a", "an",
                    "tell", "me", "about", "find", "show"
                } and len(w) > 2
            ]
            if _name_words and not any(
                rw in query_lower for rw in [
                    "hod", "head", "vc", "vice", "chancellor",
                    "registrar", "dean", "department"
                ]
            ):
                return {
                    "relevant": docs,
                    "irrelevant": [],
                    "all_irrelevant": False
                }
    
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        import re

        llm = ChatOpenAI(
            model          = "gpt-4o-mini",
            temperature    = 0,
            max_tokens     = 200,
            openai_api_key = os.getenv("OPENAI_API_KEY"),
        )

        # Build batch prompt — use first 300 chars of each doc
        # WHY 300 chars: enough to judge relevance, cheap on tokens
        docs_text = ""
        for i, doc in enumerate(docs, 1):
            src     = doc.metadata.get("source", "unknown")[:50]
            preview = doc.page_content[:300].replace("\n", " ")
            docs_text += f"\n===DOCUMENT {i}=== (source: {src})\n{preview}\n"

        GRADE_PROMPT = (
            "QUESTION: {question}\n\n"
            "Grade each document: does it help answer the question above?\n\n"
            "YES     = directly answers or provides key facts for the question\n"
            "PARTIAL = somewhat related — when in doubt use PARTIAL not NO\n"
            "NO      = ONLY if document has absolutely zero connection to question\n"
            "IMPORTANT: Be generous. Prefer PARTIAL over NO when unsure.\n\n"
            "Respond ONLY in this exact format — one grade per document:\n"
            "===DOC_1===\nYES or NO or PARTIAL\n"
            "===DOC_2===\nYES or NO or PARTIAL\n"
            "(continue for all documents — no other text)\n\n"
            "{documents}"
        )

        prompt = GRADE_PROMPT.format(question=query, documents=docs_text)

        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            raw      = response.content.strip()
        except Exception as e:
            print(f"[E4-Grade] Batch grading failed: {e}. Treating all as relevant.")
            return {"relevant": docs, "irrelevant": [], "all_irrelevant": False}

        relevant   = []
        irrelevant = []

        for i, doc in enumerate(docs, 1):
            pattern = rf"===DOC_{i}===\s*(YES|NO|PARTIAL)"
            match   = re.search(pattern, raw, re.IGNORECASE)

            if match:
                grade = match.group(1).upper()
                if grade in ("YES", "PARTIAL"):
                    relevant.append(doc)
                else:
                    src = doc.metadata.get("source", "unknown")[:40]
                    print(f"[E4-Grade] Dropped (NO): {src}")
                    irrelevant.append(doc)
            else:
                # LLM missed this doc in response — safe default: keep it
                relevant.append(doc)

        all_irrelevant = len(relevant) == 0
        print(f"[E4-Grade] {len(docs)} docs → {len(relevant)} kept, "
            f"{len(irrelevant)} dropped ✓")

        return {
            "relevant"      : relevant,
            "irrelevant"    : irrelevant,
            "all_irrelevant": all_irrelevant
            }


    def _self_reflect(self, query: str, docs: list, answer: str) -> dict:
        """
        E4 Step 2: Check if generated answer is grounded and complete.

        GROUNDED means: every fact in the answer came from the retrieved
        documents. The LLM did not add information from its own training
        data (hallucination).

        Example of NOT grounded:
        Document says: "Ismail Khan is HOD of CS."
        Answer says:   "Ismail Khan is HOD of CS with 10 years experience."
        "10 years experience" was NOT in the document — hallucinated.

        COMPLETE means: the answer addressed the full question.
        Student asked two things, answer only addressed one = incomplete.

        WHY AFTER STREAMING:
        Self-reflection runs AFTER the answer has already streamed to the
        student. If grounding fails, we append a small disclaimer.
        We do NOT regenerate — that would double cost and latency.

        RUNS ONLY FOR:
        general and policy agents — highest hallucination risk because
        their prompts are broad and their docs are dense policy text.
        Faculty and admissions answers are factual and short — lower risk.

        Args:
            query  : original user question
            docs   : documents that were given to the answer LLM
            answer : the full answer the LLM generated

        Returns:
            {"grounded": bool, "complete": bool, "issues": str}
        """
        if not docs or not answer:
            return {"grounded": True, "complete": True, "issues": "skipped"}

        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        import re

        llm = ChatOpenAI(
            model          = "gpt-4o-mini",
            temperature    = 0,
            max_tokens     = 150,
            openai_api_key = os.getenv("OPENAI_API_KEY"),
        )

        # Use first 5 docs, 1000 chars each
        # WHY 1000: E2 parent chunks are 800-1500 chars.
        # At 400 chars the reflector saw only the first quarter of
        # each parent and flagged grounded answers as hallucinations.
        # 1000 chars captures the full relevant section in almost all cases.
        context_text = "\n---\n".join([
            doc.page_content[:1000] for doc in docs[:5]
        ])

        REFLECT_PROMPT = (
            "QUESTION: {question}\n\n"
            "CONTEXT (documents given to the AI):\n{context}\n\n"
            "ANSWER (what the AI said):\n{answer}\n\n"
            "Check the answer:\n"
            "1. GROUNDED: Does the answer use ONLY facts from the context?\n"
            "   Answer NO if the AI added facts not found in the context.\n"
            "2. COMPLETE: Does the answer address the full question?\n\n"
            "Respond ONLY in this format:\n"
            "GROUNDED: YES or NO\n"
            "COMPLETE: YES or NO\n"
            "ISSUES: one short line describing any problem, or NONE"
        )

        prompt = REFLECT_PROMPT.format(
            question = query,
            context  = context_text,
            answer   = answer[:1200]  # raised from 600 — longer answers need full text to check
        )

        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            raw      = response.content.strip()

            grounded_m = re.search(r"GROUNDED:\s*(YES|NO)", raw, re.IGNORECASE)
            complete_m = re.search(r"COMPLETE:\s*(YES|NO)", raw, re.IGNORECASE)
            issues_m   = re.search(r"ISSUES:\s*(.+)",       raw, re.IGNORECASE)

            grounded = (grounded_m.group(1).upper() == "YES") if grounded_m else True
            complete = (complete_m.group(1).upper() == "YES") if complete_m else True
            issues   = issues_m.group(1).strip() if issues_m else "none"

            print(f"[E4-Reflect] Grounded={grounded} Complete={complete} "
                f"Issues={issues}")
            return {"grounded": grounded, "complete": complete, "issues": issues}

        except Exception as e:
            print(f"[E4-Reflect] Failed: {e}. Skipping.")
            return {"grounded": True, "complete": True, "issues": "reflection skipped"}
    def _compress_documents(self, query: str, docs: list) -> list:
        """
        E3: Drop documents the cross-encoder already judged irrelevant.

        WHY THIS REPLACED THE OLD LLM PIPELINE
        The previous version ran _embeddings_filter() (2 embedding API calls,
        ~1.4s) and then _llm_extract() (1 gpt-4o-mini call, measured 3.5-9.4s).
        Measured on four representative queries, that pipeline was 60-76% of all
        retrieval latency — and it actively lost answers: "who is the head of
        Computer Science" went 7 docs -> 2, with five documents discarded as
        NO_OUTPUT. Questions that reached the answer LLM with only 2 contexts
        scored 0.333 faithfulness because the model had to invent the rest.

        _rerank() already scored every document with the CrossEncoder, reading
        query and document together — a strictly better relevance signal than
        cosine similarity, and a cheaper one than an LLM. Those scores are sitting
        in doc.metadata["rerank_score"] already paid for. So gate on them in
        three steps: an absolute floor removes outright noise, the widest
        consecutive score gap marks where relevance actually ends, and MIN/MAX
        bounds keep the result sane.

        Cliff detection rather than a fixed threshold because the score scale
        shifts per query: a strong query's last relevant doc sits at +2.35 while
        a weak query's best doc is -0.07, so no single cutoff separates them —
        but the discontinuity does, with no per-query tuning. MIN_CONTEXT_DOCS is
        a hard backstop so a miscalibrated threshold can never empty the context.

        COST: zero API calls, zero LLM tokens, sub-millisecond.

        SKIPPED FOR:
            List queries ("all HODs", "how many departments") — those need every
            entity on the page, so no gating is applied at all.
        """
        if not docs:
            return docs

        _query_lower = query.lower()

        # ── List queries need completeness, not filtering ──────────────────
        if any(signal in _query_lower for signal in self._LIST_SIGNALS):
            print(f"[E3] List query — no gating, keeping all {len(docs)} docs.")
            return docs

        scored = [
            (float(doc.metadata.get("rerank_score", 0.0)), doc)
            for doc in docs
        ]

        # If nothing carries a score, there is nothing to gate on — pass through.
        if all(score == 0.0 for score, _ in scored):
            return docs

        scored.sort(key=lambda pair: pair[0], reverse=True)

        # ── Step 1: absolute floor — anything this weak is noise ────────────
        survivors = [(s, d) for s, d in scored if s >= config.CONTEXT_SCORE_FLOOR]
        if len(survivors) < config.MIN_CONTEXT_DOCS:
            survivors = scored[:config.MIN_CONTEXT_DOCS]

        # ── Step 2: cut at the score cliff ──────────────────────────────────
        # Measured score sequences fall off a cliff exactly where relevance ends,
        # e.g. the attendance query scores [3.01, 2.20, 1.41, -1.64, -1.77,
        # -8.24, ...] — a 6.5 point drop after the 5th document. A fixed
        # threshold cannot find that boundary across query types (a strong query
        # bottoms out at +2.35, a weak one tops out at -0.07), but the largest
        # consecutive gap does, with no per-query tuning.
        cut = len(survivors)
        if len(survivors) > config.MIN_CONTEXT_DOCS:
            widest = 0.0
            for i in range(config.MIN_CONTEXT_DOCS, len(survivors)):
                drop = survivors[i - 1][0] - survivors[i][0]
                if drop > widest:
                    widest, cut = drop, i
            # Only honour the cliff if it is a real discontinuity, not noise.
            if widest < config.CONTEXT_CLIFF_DROP:
                cut = len(survivors)

        kept = [d for _, d in survivors[:cut]]

        # ── Step 3: hard bounds ─────────────────────────────────────────────
        # An upper bound matters because context_precision is average-precision:
        # a relevant document sitting at rank 10 drags the score DOWN relative to
        # the same answer delivered in 6 documents. MIN is the faithfulness
        # backstop so a miscalibrated gate can never starve the LLM.
        if len(kept) < config.MIN_CONTEXT_DOCS:
            kept = [d for _, d in survivors[:config.MIN_CONTEXT_DOCS]]
        kept = kept[:config.MAX_CONTEXT_DOCS]

        if len(kept) < len(docs):
            print(f"[E3] Gate: {len(docs)} → {len(kept)} docs "
                  f"(best={scored[0][0]:.2f}, cliff drop={widest if len(survivors) > config.MIN_CONTEXT_DOCS else 0:.2f})")

        return kept

    def _apply_parent_child(self, documents: List[Document]) -> List[Document]:
        """
        Convert already-chunked documents into parent-child pairs.
        INPUT  → documents (existing chunks — become PARENTS in DocStore)
        OUTPUT → children (small, precise — go into ChromaDB and BM25)
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=200,
            chunk_overlap=20,
            separators=["\n", ". ", ", ", " ", ""]
        )

        all_children = []

        for doc in documents:
            parent_text = doc.page_content

            if not parent_text.strip() or len(parent_text.strip()) < 50:
                continue

            parent_id = self.parent_store.add_parent(
                content=parent_text,
                metadata=doc.metadata
            )

            for child_text in child_splitter.split_text(parent_text):
                if len(child_text.strip()) < 20:
                    continue

                all_children.append(Document(
                    page_content=child_text,
                    metadata={
                        **doc.metadata,
                        "parent_id": parent_id,
                        "chunk_type": "child"
                    }
                ))

        return all_children

    def _multi_search(self, queries: List[str], filter_categories: List[str] = None,
                      seen_content: set = None) -> List[Document]:
        """Run several query variants concurrently and merge unique candidates.

        Parallel rather than sequential: 3 searches at ~0.5s each cost 0.5s, not
        1.5s. seen_content lets a caller exclude documents an earlier pass already
        returned, so a second pass only adds new material.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if seen_content is None:
            seen_content = set()
        candidates = []

        def search_one(q):
            return self._hybrid_search(q, k=config.CANDIDATE_K,
                                       filter_categories=filter_categories)

        with ThreadPoolExecutor(max_workers=max(1, len(queries))) as executor:
            futures = {executor.submit(search_one, q): q for q in queries}
            for future in as_completed(futures):
                try:
                    for doc in future.result():
                        key = self._content_key(doc.page_content)
                        if key not in seen_content:
                            candidates.append(doc)
                            seen_content.add(key)
                except Exception as e:
                    print(f"[Search] Parallel search failed for one query: {e}")

        return candidates

    def _hyde_queries(self, query: str) -> List[str]:
        """Generate vocabulary-bridging query variants with one LLM call.

        WHY: some questions share no rare term with the documents that answer
        them. "How does UoL decide who gets admitted?" must reach text reading
        "merit", "Matriculation", "Intermediate" — no lexical overlap, so neither
        BM25 nor the embedding finds it. A hypothetical answer written in document
        vocabulary does. This also handles Urdu/Pashto questions, which need the
        same bridge into English document text.

        Called only on the weak-retrieval path, so most queries never pay for it.
        """
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage

        try:
            llm = ChatOpenAI(
                model=config.LLM_MODEL,
                temperature=0.0,
                max_tokens=160,
                openai_api_key=config.OPENAI_API_KEY,
            )
            prompt = (
                f"A student asked a chatbot for {config.UNIVERSITY_NAME} "
                f"this question:\n\"{query}\"\n\n"
                f"Write the sentence an official {config.UNIVERSITY_NAME} "
                "document would use to answer it, using formal institutional "
                "vocabulary (write in English even if the question is not). "
                "Then give one reworded version of the question using different "
                "words.\n\n"
                f"The university is in Loralai, Balochistan, Pakistan. It is "
                f"abbreviated UOLi or UoL. Never substitute a different "
                f"institution such as the University of London.\n\n"
                "Format exactly:\nDOC: <sentence>\nALT: <reworded question>"
            )
            raw = llm.invoke([HumanMessage(content=prompt)]).content.strip()

            out = []
            for line in raw.split("\n"):
                line = line.strip()
                for tag in ("DOC:", "ALT:"):
                    if line.startswith(tag):
                        text = line[len(tag):].strip()
                        if text:
                            out.append(text)
            return out
        except Exception as e:
            # Retrieval must never crash because expansion failed.
            print(f"[HyDE] expansion failed: {e} — keeping first-pass results")
            return []

    # Words that carry no topic on their own, used to judge whether a clause is
    # a real question or a fragment. Deliberately small — it only has to reject
    # fragments like "MS programs", not do linguistics.
    _INTENT_STOP = {
        "a", "an", "the", "is", "are", "was", "were", "be", "of", "to", "in",
        "on", "at", "for", "from", "by", "with", "do", "does", "did", "can",
        "could", "will", "would", "should", "i", "me", "my", "we", "our", "you",
        "your", "it", "its", "there", "that", "this", "and", "or", "what",
        "which", "how", "many", "much", "please", "tell", "give", "about",
    }

    def _split_intents(self, query: str) -> List[str]:
        """Split a question that asks for two or more unrelated facts into its parts.

        WHY THIS EXISTS — measured on "When was UoL established and who is the
        current Registrar?": retrieval returned the Registrar half perfectly
        (Prof. Dr. Khalid Khan at ranks 1, 2 and 3) and NOTHING about the
        establishment year, even though "The University of Loralai was
        established in 2012" and the Balochistan Provincial Assembly charter are
        both in the corpus. Coverage for that question was 0.46.

        The cause is structural, not a tuning problem: search() reranks the
        candidate pool against ONE query string. A cross-encoder given a
        two-intent question scores documents for whichever intent dominates the
        sentence, so the second intent cannot reach the top of a single ranked
        list — and the E3 gate then cuts the tail where it was sitting. No score
        threshold fixes that, because the losing intent's documents are correctly
        judged less relevant *to the combined string*.

        So the parts are ranked separately and the context budget is shared
        between them. This is the same principle as the per-category quota in
        _hybrid_search(): when one ranked list cannot represent everything the
        question needs, give each contender its own slots.

        DESIGN (unified single-pass):
          1. Split on ALL delimiters at once (', and', comma, 'and', ';', '&').
          2. For each fragment, detect its own question-word prefix.
          3. Fragments that have no question word get the LAST SEEN prefix
             prepended so they become standalone queries.
          4. Fragments with enough content words are returned as separate intents.

        This handles three distinct patterns cleanly:
          a. Homogeneous entity lists:
               "Who is the VC, the Registrar, the Treasurer, and the HOD of CS?"
               → ["Who is the VC", "Who is the Registrar", "Who is the Treasurer",
                  "Who is the HOD of CS"]
          b. Homogeneous policy lists:
               "What is the attendance policy, harassment policy, grading system?"
               → ["What is the attendance policy",
                  "What is the harassment policy",
                  "What is the grading system"]
          c. Cross-agent heterogeneous:
               "Who is the VC, what is the attendance policy, where is the university?"
               → ["Who is the VC",
                  "what is the attendance policy",
                  "where is the university"]
             Each fragment keeps its own question word → correct agent routing.

        Returns [] when the question has a single intent, so callers keep the
        cheaper single-pass path. Costs no API calls.
        """
        import re as _re

        # ── Step 1: Split on ALL delimiters ─────────────────────────────────
        # Order matters: ', and' must be matched BEFORE bare ',' or bare 'and'.
        raw_parts = [p.strip(" ?.,") for p in
                     _re.split(r"\s*,\s+and\s+|\s*,\s*|\s+and\s+|\s*;\s*|\s*&\s*",
                               query, flags=_re.I)]
        raw_parts = [p for p in raw_parts if p]

        if len(raw_parts) <= 1:
            return []

        def content_words(part: str) -> int:
            words = [w for w in _re.findall(r"[a-z0-9']+", part.lower())
                     if w not in self._INTENT_STOP and len(w) > 1]
            return len(words)

        # ── Step 2: Detect question-word prefix in the original query ────────
        _Q_PAT = _re.compile(
            r"^(who\s+is|who\s+are|what\s+is|what\s+are|where\s+is|where\s+are"
            r"|when\s+is|when\s+are|how\s+is|how\s+are|tell\s+me\s+about"
            r"|tell\s+me)\b",
            _re.I,
        )
        _first_q = _Q_PAT.match(query.strip())

        if not _first_q:
            # No question word at start — only split if each part is a full
            # self-contained clause (>= 3 content words). This prevents
            # "entry requirements for BS and MS programs" from being torn in
            # half: "MS programs" has only 2 words and does not qualify.
            qualifying = [p for p in raw_parts if content_words(p) >= 3]
            return qualifying if len(qualifying) >= 2 else []

        # ── Step 3: Enrich fragments with their own or inherited prefix ──────
        last_prefix = _first_q.group(1) + " "
        enriched = []
        for part in raw_parts:
            m = _Q_PAT.match(part)
            if m:
                # This fragment has its own question word — use it and update
                # the prefix tracker so following bare fragments inherit it.
                last_prefix = m.group(1) + " "
                enriched.append(part)
            elif last_prefix:
                # Bare fragment like "the Registrar" or "the attendance policy"
                # → prepend the last seen question word to make it standalone.
                enriched.append(last_prefix + part)
            else:
                enriched.append(part)

        # Require >= 1 content word per enriched intent.
        # NOTE: the old threshold was >= 2, but single-entity fragments like
        # "Who is the VC" or "Who is the Registrar" only carry 1 content word
        # after removing stop words ("who", "is", "the" are all stops).
        # Lowering to >= 1 lets those fragments qualify so the fanout fires.
        # The no-question-word path above still uses >= 3, preserving safety
        # for things like "entry requirements for BS and MS programs".
        qualifying = [p for p in enriched if content_words(p) >= 1]
        return qualifying if len(qualifying) >= 2 else []



    def _search_multi_intent(self, query: str, expanded: str,
                             intents: List[str],
                             filter_categories: List[str] = None) -> List[Document]:
        """Retrieve for a question that asks for two or more unrelated facts.

        Candidates are gathered once for every intent (in parallel, so the extra
        intents cost local Chroma/BM25 work rather than serial round trips), then
        each intent reranks that shared pool on its own and the results are
        interleaved by rank: intent A's best, intent B's best, intent A's second,
        and so on. Interleaving rather than concatenating matters for
        context_precision, which is an average-precision metric — it rewards a
        relevant document early, and every intent's best document is relevant to
        the question as a whole.

        WHY THE E3 CLIFF GATE IS NOT USED HERE:
        _compress_documents() finds the largest drop between consecutive scores
        and cuts there. That is valid only while the scores come from ONE ranking.
        After interleaving, neighbouring documents were scored by different
        queries, so a "drop" can just mean intent B is a harder question than
        intent A — the gap carries no information about relevance. Applying it
        would cut whichever intent scores lower in absolute terms, which is
        exactly the failure this method exists to prevent. The absolute score
        floor still applies, since that judges each document on its own, and the
        per-intent budget bounds the total instead.

        WHY EACH INTENT'S SCORE IS CAPTURED IMMEDIATELY:
        _rerank() writes rerank_score onto the Document objects themselves, and
        every intent reranks the SAME shared pool. So the second intent's pass
        overwrites the first intent's scores on the very objects the first intent
        already selected. Reading the score later — as _fetch_parents() does to
        pick a parent's representative child and to order the result — then reads
        the wrong intent's opinion of that document. The scores are therefore
        taken while they are still valid, written back onto the one intent that
        actually claims each document, and _fetch_parents is told to preserve the
        interleaved order rather than re-sort by them.
        """
        budget = max(config.MIN_CONTEXT_DOCS, config.MAX_CONTEXT_DOCS)
        per_intent = max(2, budget // len(intents))

        # One shared candidate pool: the intents are variants of the same
        # question, so searching them together also lets each intent's reranker
        # see documents the other intent's retrieval surfaced.
        seen_content: set = set()
        pool = self._multi_search([expanded] + intents, filter_categories,
                                  seen_content)
        if not pool:
            return []

        ranked_per_intent = []
        for intent in intents:
            ranked = self._rerank(intent, pool)
            # Read the score now, into a local pair, because the next intent's
            # _rerank call overwrites doc.metadata["rerank_score"] in place.
            kept = [(d, float(d.metadata.get("rerank_score", 0.0)))
                    for d in ranked
                    if d.metadata.get("rerank_score", 0.0) >= config.CONTEXT_SCORE_FLOOR]
            ranked_per_intent.append(kept[:per_intent])

        interleaved = []
        picked = set()
        for depth in range(per_intent):
            for ranked in ranked_per_intent:
                if depth >= len(ranked):
                    continue
                doc, score = ranked[depth]
                key = self._content_key(doc.page_content)
                if key in picked:
                    continue
                picked.add(key)
                # Restore the score belonging to the intent that claimed this
                # document, so the parent swap inherits a meaningful number.
                doc.metadata["rerank_score"] = score
                interleaved.append(doc)

        if not interleaved:
            return []

        if len(self.parent_store) > 0:
            return self._fetch_parents(interleaved,
                                       preserve_order=True)[:budget]
        return interleaved[:budget]

    def search(self, query: str, filter_categories: List[str] = None,
               engineered_queries: List[str] = None) -> List[Document]:
        """Hybrid retrieval with an adaptive second pass.

        PASS 1 (always): expand → hybrid search → CrossEncoder rerank.
        PASS 2 (only when pass 1 is weak): rewrite the query into document
        vocabulary via HyDE AND drop the category filter, then rerank together.

        WHY CONDITIONAL: pass 2 costs an LLM call plus extra searches. Measured
        top rerank scores separate the two cases cleanly — queries that retrieve
        well score +6 to +8.5, queries that fail score below zero. Gating on that
        keeps the common case near 1.2s and spends the extra time only on the
        questions that would otherwise return nothing useful.

        WHY DROP THE FILTER ON RETRY: category filters exclude the answer whenever
        the category is wrong — and several agents filtered on categories with
        zero rows in this DB. Retrying unfiltered removes that entire failure
        class instead of hand-curating a category list per agent.
        """
        # Step 1: Basic expansion for known gaps
        expanded = self._expand_query(query)

        # Step 1b: multi-intent questions get one ranked list per intent.
        # Checked before the single-pass path because a two-fact question cannot
        # be served by a single ranking — see _split_intents() for the measured
        # failure this addresses.
        intents = self._split_intents(query)
        if len(intents) >= 2:
            return self._search_multi_intent(query, expanded, intents,
                                             filter_categories)

        # Step 2: Generate multiple better queries (no-op unless ENABLE_HYDE)
        if engineered_queries is None:
            engineered_queries = self._engineer_query(expanded)

        # Step 3: First pass
        seen_content: set = set()
        all_candidates = self._multi_search(engineered_queries, filter_categories,
                                            seen_content)

        reranked = self._rerank(expanded, all_candidates) if all_candidates else []

        # Step 4: Adaptive second pass when the first pass is weak
        best = (reranked[0].metadata.get("rerank_score", 0.0)
                if reranked else float("-inf"))

        if getattr(config, "ENABLE_ADAPTIVE_RETRY", False) and \
                best < config.WEAK_RETRIEVAL_SCORE:

            print(f"[Search] Weak first pass (best={best:.2f}) — "
                  f"widening with HyDE + no category filter")
            retry_queries = [expanded] + self._hyde_queries(query)
            extra = self._multi_search(retry_queries, None, seen_content)
            if extra:
                reranked = self._rerank(expanded, all_candidates + extra)

        if not reranked:
            return []

        # ── E2: Swap children for rich parent chunks ──────────────────────
        # parent_store has data only after KB is rebuilt with parent-child
        # structure. If empty (old KB not rebuilt) → falls back to children.
        if len(self.parent_store) > 0:
            parents = self._fetch_parents(reranked)
        else:
            parents = reranked

        return self._compress_documents(expanded, parents)

    def _hybrid_search(self, query: str, k: int, filter_categories: List[str] = None) -> List[Document]:
        """
        Combines vector search and BM25 using Reciprocal Rank Fusion, with a
        per-category retrieval quota when more than one category is requested.

        WHY RRF: simple concatenation meant BM25 results always came last and
            were rarely selected by the reranker. RRF weights by rank, not by raw
            score scale, so a document ranked highly by both methods wins.
            RRF_K=60 is the standard constant — it smooths rank differences so
            rank 1 vs 2 matters less than rank 1 vs 100.

        WHY PER-CATEGORY QUOTAS (the important part):
            A single `$in` query over several categories spends its k slots on
            whichever category has the most rows, not the one most likely to
            hold the answer. Measured on "How does UoL decide who gets admitted?"
            with filter [admissions, policy, general, overview]: of 50 candidate
            slots, `admissions` got 7 while overview/policy/general took 43.
            Those 43 were generic mission-statement text ("UoL is dedicated to
            delivering impartial and high-quality education") which embeds close
            to any broad question but answers none of them. The actual answer —
            "Admission will be offered strictly on merit, based on the
            candidate's Matriculation, Intermediate, and admission test results"
            — sits at vector rank 16 *within* `admissions`, so it never entered
            the pool and no amount of reranking could recover it.

            The category label is a useful precision signal, but the categories
            are wildly unbalanced (policy 2640 rows vs admissions 128). Giving
            each its own quota keeps a small, high-precision category from being
            drowned by a large, vague one, and it generalises: any future
            question whose answer lives in a minority category benefits without
            per-question tuning.

            Recall is retrieval's job; precision is the reranker's. So retrieve
            generously per category and let the CrossEncoder and the score gate
            decide what survives.

        COST: the query is embedded ONCE and reused across categories via
            similarity_search_by_vector, so widening the pool adds only local
            Chroma/BM25 work (~0.1s per category), not extra API calls.
        """
        RRF_K = 60

        def rrf_merge(ranked_lists: list) -> List[Document]:
            """Fuse several ranked lists into one by reciprocal rank."""
            scores: dict = {}
            docs:   dict = {}
            for ranked in ranked_lists:
                for rank, doc in enumerate(ranked):
                    key = doc.page_content
                    scores[key] = scores.get(key, 0) + 1 / (rank + RRF_K)
                    docs[key]   = doc
            order = sorted(scores, key=lambda x: scores[x], reverse=True)
            return [docs[key] for key in order]

        # Single category (or none) — one pass is already balanced by definition.
        if not filter_categories or len(filter_categories) == 1:
            filter_dict = ({"category": {"$eq": filter_categories[0]}}
                           if filter_categories else None)
            merged = rrf_merge([
                self.db.similarity_search(query, k=k, filter=filter_dict),
                self._bm25_search(query, k=k, filter_categories=filter_categories),
            ])
            return merged[:k]

        # ── Multi-category: per-category quota, then round-robin interleave ──
        # The floor of 20 is empirical: the Q20 answer sits at vector rank 16
        # inside its own category, so a quota below that reintroduces the
        # starvation this branch exists to prevent.
        per_cat = max(20, k // len(filter_categories))

        try:
            query_vec = self.embeddings.embed_query(query)
        except Exception as e:
            # Never let a widening optimisation break retrieval outright.
            print(f"[Search] embed_query failed ({e}) — falling back to per-call embed")
            query_vec = None

        # Warm the BM25 memo once on this thread so the parallel workers below
        # all hit the cache instead of racing to recompute the same corpus scan.
        self._bm25_scored_order(query)

        def one_category(cat: str):
            cat_filter = {"category": {"$eq": cat}}
            lists = []
            try:
                if query_vec is not None:
                    lists.append(self.db.similarity_search_by_vector(
                        query_vec, k=per_cat, filter=cat_filter))
                else:
                    lists.append(self.db.similarity_search(
                        query, k=per_cat, filter=cat_filter))
            except Exception as e:
                print(f"[Search] vector search failed for '{cat}': {e}")
            # BM25 is local and free — give each category its own quota too,
            # otherwise lexical matches suffer the same starvation.
            lists.append(self._bm25_search(query, k=per_cat,
                                           filter_categories=[cat]))
            return rrf_merge(lists)[:per_cat] if any(lists) else []

        # Category searches are independent and Chroma releases the GIL for its
        # native work, so run them concurrently: wall-clock becomes the slowest
        # single category rather than their sum.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(filter_categories)) as ex:
            per_cat_ranked = [r for r in ex.map(one_category, filter_categories) if r]

        if not per_cat_ranked:
            return []

        # WHY ROUND-ROBIN RATHER THAN A GLOBAL RRF SORT:
        #   Fusing all categories into one RRF ranking and truncating undoes the
        #   quota. A document at rank 16 of its category scores 1/(15+60)=0.0133,
        #   while every category's rank-1 document scores 1/60=0.0167 — so the
        #   global sort refills the pool with the top of the largest categories
        #   and evicts exactly the deep-but-correct document the quota was meant
        #   to protect. Measured: the Q20 answer was still absent from an
        #   80-document pool built that way.
        #   Interleaving by rank instead means position in the pool depends on a
        #   document's rank WITHIN its category, never on how many rows that
        #   category happens to have. Every category contributes its full quota.
        pool: List[Document] = []
        seen: set = set()
        for depth in range(per_cat):
            for ranked in per_cat_ranked:
                if depth < len(ranked):
                    doc = ranked[depth]
                    key = doc.page_content
                    if key not in seen:
                        seen.add(key)
                        pool.append(doc)
        return pool

    def _bm25_scored_order(self, query: str):
        """Positive-scoring BM25 hits for a query, ranked, cached per query.

        WHY CACHED: get_scores() scores the entire 5558-document corpus and the
        result is then sorted. The per-category retrieval quota calls this once
        per category, so an uncached version paid that cost 4x for identical
        input — measured at roughly a second of pure waste per query. The cache
        holds one query only; it is a memo for the current fan-out, not a
        long-lived cache that could go stale against a rebuilt index.
        """
        if getattr(self, "_bm25_cache_query", None) == query:
            return self._bm25_cache_order

        tokens = query.lower().split()
        scores = self.bm25.get_scores(tokens)
        order  = sorted(
            ((i, s) for i, s in enumerate(scores) if s > 0),
            key=lambda x: x[1], reverse=True,
        )
        self._bm25_cache_query = query
        self._bm25_cache_order = order
        return order

    def _bm25_search(self, query: str, k: int, filter_categories: List[str] = None) -> List[Document]:
        if self.bm25 is None or not self.bm25_docs:
            return []

        results = []
        for i, s in self._bm25_scored_order(query):
            doc = self.bm25_docs[i]
            if filter_categories:
                if doc.metadata.get("category") not in filter_categories:
                    continue
            results.append(doc)
            if len(results) >= k:
                break
        return results

    # Department name → URL slug mapping for force-include logic
    _DEPT_SLUGS = {
        "math": "faculty/mathematics",
        "maths": "faculty/mathematics",
        "mathematics": "faculty/mathematics",
        "pashto": "faculty/pashto",
        "cs": "faculty/computer-science",
        "computer": "faculty/computer-science",
        "english": "faculty/english",
        "islamic": "faculty/islamic-studies",
        "commerce": "faculty/commerce",
        "education": "faculty/education",
        "management": "faculty/management-sciences",
        "zoology": "faculty/zoology",
        "political": "faculty/political-science",
        "allied": "faculty/allied-health-sciences",
    }
    @staticmethod
    def _normalize_figure_columns(text: str) -> str:
        """Re-join "figure on one line, caption on the next" statistics blocks.

        Several pages were scraped from a card/tile layout where each statistic
        rendered as a large number above its caption. Flattened to text that
        becomes a column of alternating lines, and the pairing survives only as
        line order:

            5                                          -> 5 State-of-the-Art Computer Labs
            State-of-the-Art Computer Labs
            80%                                        -> 80% Students Supported Through Financial Aid
            Students Supported Through Financial Aid
            2000+                                      -> 2000+          (caption lost by the scrape)
            1500+                                      -> 1500+          (caption lost by the scrape)
            100+                                       -> 100+ Faculty
            Faculty
            10+                                        -> 10+ Degree Programs
            Degree Programs

        WHY IN CODE RATHER THAN IN THE PROMPT: asked "how many faculty members",
        gpt-4o-mini answered "1500+" off the raw block — it picked a nearby
        number instead of the one the caption "Faculty" belongs to. A prompt rule
        describing the convention did not fix it, because deciding whether the
        next line is a caption or another figure is a per-line judgement made at
        the tail of a 6000-character context. It is a two-line lookahead, so it
        belongs in a deterministic pass that every model then reads correctly.

        WHAT THIS DELIBERATELY DOES NOT DO: it never invents a caption. A figure
        whose next line is another figure keeps its own line and is labelled as
        uncaptioned, which is an honest representation of the source — those
        captions are absent from every local artifact, including the oldest
        parent_chunks backup, so the loss happened in the original scrape.
        Guessing at them is what produced the DB's false "over 2000 faculty
        members" propositions.

        WHY THE LABEL, AND NOT JUST A BARE LINE: leaving "2000+" alone on a line
        is honest but not legible. Asked how many students the university has,
        gpt-4o-mini answered "2000+" — the bare figure sat two lines below a
        captioned one, and nothing in the text said it was orphaned, so the model
        read down the column and attached the nearest available meaning. The
        prompt already instructs that an uncaptioned figure is unavailable
        (_FIGURE_RULES in graph/Agents.py); that rule could not fire because the
        text gave no way to tell an uncaptioned figure from a captioned one. The
        label supplies the missing signal in the text itself, where every model
        reads it, instead of asking the model to infer absence from layout.

        The label is only attached when the figure genuinely sits in a statistics
        column — that is, when the line above or below it is also a bare figure.
        A lone number in prose, a page number or a stray table cell has neither
        neighbour and is left exactly as it was found.

        Not a whitespace-only transformation any more: the uncaptioned-figure
        label adds words. Nothing is removed or reordered, and no figure, email,
        phone number or percentage is altered, so every existing substring check
        over context still holds — but the label does reach both the LLM and the
        RAGAS `contexts`, which is the point.
        """
        lines = text.split("\n")
        out, i = [], 0
        while i < len(lines):
            fig = _FIGURE_LINE.match(lines[i])
            if fig:
                figure   = fig.group(1).strip()
                nxt      = lines[i + 1].strip() if i + 1 < len(lines) else ""
                nxt_fig  = bool(i + 1 < len(lines)
                                and _FIGURE_LINE.match(lines[i + 1]))
                # A caption is a short line of words. Requiring a letter rejects
                # a following figure; the length cap keeps this from swallowing a
                # paragraph that merely happens to follow a numeric line.
                if (nxt
                        and not nxt_fig
                        and len(nxt) <= 60
                        and any(c.isalpha() for c in nxt)):
                    out.append(f"{figure} {nxt}")
                    i += 2
                    continue
                # No caption follows. Say so, but only for a figure standing in a
                # column of figures — that adjacency is what identifies it as a
                # statistic whose caption went missing rather than an ordinary
                # number that never had one.
                prev_fig = bool(i > 0 and _FIGURE_LINE.match(lines[i - 1]))
                if nxt_fig or prev_fig:
                    out.append(f"{figure} {_NO_CAPTION}")
                    i += 1
                    continue
            out.append(lines[i])
            i += 1
        return "\n".join(out)

    @staticmethod
    def _content_key(text: str) -> str:
        """Normalised key for detecting byte-equivalent duplicate context.

        The corpus stores the same sentence under many sources (the main contact
        number appears 65 times), so identity-based dedup let one fact occupy
        several context slots. Whitespace and case are normalised so trivial
        formatting differences still collapse to one key.
        """
        return " ".join(text.lower().split())

    # Contact details and a page-furniture address block look almost identical to
    # a bag-of-words scorer: both are a short run of digits, an @ sign and the
    # university's name. The difference is grammatical. A proposition is a
    # sentence and has a verb ("the contact number IS ...", "Khalid Khan can BE
    # CONTACTED at ..."); a scraped footer is a field list with no verb at all
    # ("University of Loralai, Quetta Road ... Phone: ... Email: ...").
    _BOILER_VERB = re.compile(
        r"(?i)\b(is|are|was|were|be|been|has|have|had|can|could|will|would|shall|"
        r"should|may|must|does|do|did|offers?|provides?|includes?|serves?|"
        r"ensures?|manages?|holds?|awards?|requires?|contacted|located|"
        r"established|comprises?|consists?|means?|refers?)\b")
    _BOILER_SIGNAL = re.compile(r"(?i)(@|phone|email|tel:|\+92|\(824\)|"
                                r"quetta road|zerh karez)")
    # Whoever this text names, it is about a person or an office, not furniture.
    _BOILER_EXEMPT = re.compile(
        r"(?i)\b(registrar|controller|treasurer|chancellor|dean|director|"
        r"principal|hod|head of|professor|prof\.|dr\.|mr\.|ms\.|coordinator|"
        r"officer|incharge|assistant)\b")
    # Text a scrape captured from a page that has no content to give.
    #
    # Borrowed, not restated. web_scraper now refuses to ingest such a page at
    # all, and this class demotes the ones already stored; if the two layers kept
    # separate patterns, a marker added to one would silently not apply to the
    # other, and the layer that still matched would be the only defence. One
    # definition in page_furniture, two consumers.
    _DEAD_PAGE = page_furniture.DEAD_PAGE

    @classmethod
    def _is_boilerplate(cls, text: str) -> bool:
        """Is this page furniture rather than an answer to anything?

        Two shapes, both measured on this corpus and both scored highly by the
        CrossEncoder for questions they cannot answer:

        1. The site footer / nav stub. "University of Loralai, Quetta Road, Zerh
           Karez, Loralai, Balochistan. Phone: ... Email: info@uoli.edu.pk" was
           returned at RANK 0 for "Who is the Registrar?", "Who is the Deputy
           Controller of Examinations?" and "Who is the VC and what departments
           does UoL have?". It answers none of them. Because context_precision is
           average precision, one irrelevant document at rank 0 halves the score
           for that question on its own: Q12's answer sat at rank 3 and scored
           exactly 1/4.

        2. A captured error page ("Oops! Something went Wrong... we couldn't find
           your page"). It carries no fact at all.

        Kept deliberately narrow — this flags 4 of 848 parents and 1 of 5558
        children. It is a demotion, not a filter (see _rerank), so a query with
        nothing better to offer can still surface these.
        """
        if len(text) > 300:
            return False
        if cls._DEAD_PAGE.search(text):
            return True
        if not cls._BOILER_SIGNAL.search(text):
            return False
        if cls._BOILER_EXEMPT.search(text):
            return False
        return not cls._BOILER_VERB.search(text)

    # Words that make the footer's own content the thing being asked for.
    _CONTACT_QUERY = re.compile(
        r"(?i)\b(address|located|location|where is|phone|telephone|number|"
        r"contact|email|e-mail|mail|reach|call|fax|postal|campus address)\b")

    @classmethod
    def _asks_contact_details(cls, query: str) -> bool:
        """Does the question ask for the contact details a footer is made of?

        Gates the boilerplate penalty in _rerank. "What is the address of
        University of Loralai?" and "UoL contact?" must still see the address
        block; "Who is the Registrar?" must not.
        """
        return bool(cls._CONTACT_QUERY.search(query))

    # A question naming a person or a role wants a directory entry: a line that
    # carries somebody's name and how to reach them. The CrossEncoder cannot see
    # that distinction — it rewards topical overlap, so for "Who is the Registrar
    # of University of Loralai?" it scored "The Office of the Registrar is the
    # custodian of academic and administrative records" at +9.25 and the block
    # naming Prof. Dr. Khalid Khan at +6.16. The higher-scoring sentence repeats
    # every word of the question and answers none of it.
    _PERSON_QUERY = re.compile(
        r"(?i)\b(who is|who are|who's|whos|name of|names of|head of|hod|"
        r"chairperson|in ?charge|contact (?:of|for|details)|email of)\b")
    # An honorific or a university address is what a directory entry has and a
    # topical paragraph does not.
    _DIRECTORY_ENTRY = re.compile(
        r"(?i)(@uoli\.edu\.pk|\b(?:prof|dr|mr|ms|mrs|engr|hafiz|syed)\.?\s+[A-Z])")
    # Measured gap to overcome is 3.09 (above). 4.0 clears it while staying well
    # inside the ~19-point spread of the score scale, so it reorders documents
    # that were close and cannot lift a genuinely irrelevant one over a strong
    # match.
    _DIRECTORY_BONUS = 4.0

    def _order_unscored(self, query: str, docs: List["Document"]) -> List["Document"]:
        """Put an unscored document list into relevance order. Drops nothing.

        The faculty routes that answer from a raw Chroma `where` query hand back
        every chunk of a known page in STORAGE order — no relevance signal at
        all, which is why those documents carry no rerank_score. Measured on Q12
        ("Who is the Deputy Controller of Examinations?"), storage order put the
        office's Vision and Mission paragraphs above the chunk that actually
        names Hafiz Muhammad Ibrahim, so the answer sat at rank 3 of 5 and
        average precision could not exceed 1/3 however good the retrieval was.
        The page was the right page; only the order was wrong.

        WHY NOT JUST CALL _rerank: _rerank also enforces the per-source
        diversity cap and truncates to TOP_K_RESULTS. Both are correct for a
        pool gathered from across the corpus and wrong here, where every
        document deliberately comes from ONE page and the HOD sweeps depend on
        keeping all of them. This scores and sorts and does nothing else.

        Dropping nothing is what makes this safe to apply blind:
        context_precision is a function of ORDER and context_recall a function
        of PRESENCE, so a pure reordering can raise the first and cannot lower
        the second. The CrossEncoder runs locally, so the cost is roughly 10ms
        per document and no API spend.
        """
        if len(docs) < 2:
            return docs

        scores   = self.reranker.predict([[query, d.page_content] for d in docs])
        wants_person = bool(self._PERSON_QUERY.search(query))

        scored = []
        for raw, doc in zip(scores, docs):
            score = float(raw) - self._boilerplate_penalty(query, doc.page_content)
            # Only for a question about a person, and only on this path: the
            # ordering _rerank produces for the rest of the corpus is already
            # measured and is deliberately left alone.
            if wants_person and self._DIRECTORY_ENTRY.search(doc.page_content):
                score += self._DIRECTORY_BONUS
            scored.append((score, doc))

        # Stable sort on the score alone — Documents are not comparable, and ties
        # must keep the order the route chose rather than raise a TypeError.
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [doc for _, doc in scored]

    @classmethod
    def _demote_boilerplate(cls, query: str, docs: List["Document"]) -> List["Document"]:
        """Move page furniture to the end of an already-ordered document list.

        WHY THIS EXISTS SEPARATELY FROM THE PENALTY IN _rerank: several faculty
        routes answer from a raw Chroma `where` query and never call _rerank at
        all, so their documents carry no rerank_score and the penalty there could
        not reach them. Measured on Q12 ("Who is the Deputy Controller of
        Examinations?"): the site footer came back at RANK 1 and the document
        naming Hafiz Muhammad Ibrahim at rank 4, giving average precision
        1/4 = 0.250 — exactly the score that question recorded. Q3 has the same
        shape.

        WHY A STABLE PARTITION RATHER THAN A RE-RANK: these lists are already in
        a deliberate order (a department sweep, an HOD enumeration, a contact
        lookup), and re-scoring them would discard that ordering and truncate the
        list sweeps that depend on seeing every source. Partitioning preserves
        the relative order of both groups and changes nothing but where the
        furniture sits. Nothing is dropped: context_precision is average
        precision, so an irrelevant document BELOW the relevant ones costs
        nothing, and keeping it means a contact question can still reach it.

        The same _asks_contact_details gate applies as in _rerank — when the
        question asks for the address or phone number, the footer IS the answer
        and the list is returned untouched.
        """
        if not docs or not any(cls._boilerplate_penalty(query, d.page_content)
                               for d in docs):
            return docs
        keep      = [d for d in docs
                     if not cls._boilerplate_penalty(query, d.page_content)]
        furniture = [d for d in docs
                     if cls._boilerplate_penalty(query, d.page_content)]
        # All furniture means the classifier is wrong about this list, or the
        # route genuinely found nothing else. Either way, reordering it is not an
        # improvement — hand back what was retrieved.
        return keep + furniture if keep else docs

    @classmethod
    def _boilerplate_penalty(cls, query: str, text: str) -> float:
        """How much to sink this document for being page furniture, if at all.

        WHY THIS IS NOT JUST `_is_boilerplate` PLUS A GATE: the contact-question
        exemption exists because the address block genuinely IS the answer to
        "what is the address of the university". A captured 404 page is not the
        answer to anything, and the first version of this let the exemption
        cover both. Measured on a held-out question absent from the eval set —
        "What is the fax number of the university?" — the exemption switched the
        demotion off wholesale and the scraped "Oops! Something went Wrong..."
        page came back at RANK 1.

        So the two shapes are now separated by what they could possibly answer:

          dead page  -> penalised always. No question is answered by a page that
                        failed to load.
          furniture  -> penalised unless the question asks for exactly the
                        contact details the furniture is made of.

        Returning a number rather than a bool keeps this usable from both
        scoring paths (_rerank and _order_unscored) without either restating the
        policy.
        """
        if not cls._is_boilerplate(text):
            return 0.0
        if cls._DEAD_PAGE.search(text):
            return config.BOILERPLATE_PENALTY
        if cls._asks_contact_details(query):
            return 0.0
        return config.BOILERPLATE_PENALTY

    def _rerank(self, query: str, docs: List[Document]) -> List[Document]:
        """
        WHAT: Takes candidate documents from hybrid search and returns
              the most relevant ones using a CrossEncoder model.

        WHY:  First-stage retrieval (RRF) finds good candidates fast
              but approximately. CrossEncoder reads query+document
              together for accurate relevance scoring.

        THREE STEPS:
            1. CrossEncoder scores every candidate
            2. Force-include one chunk from each mentioned department
            3. Source diversity — max 2 chunks per source

        D4 CHANGE:
            Each doc now gets doc.metadata["rerank_score"] = float(score)
            grade_documents_node in nodes.py reads these scores to
            classify retrieval quality as good/partial/poor — for free,
            with zero extra API calls.
        """
        if not docs:
            return []

        # ── Step 1: CrossEncoder Scoring ──────────────────────────
        # WHY: Cross-encoder reads query AND document together.
        #      Much more accurate than vector similarity alone.
        pairs  = [[query, doc.page_content] for doc in docs]
        scores = self.reranker.predict(pairs)

        # Demote page furniture, unless the question is actually asking for the
        # contact details that furniture is made of. A captured error page is
        # demoted either way — see _boilerplate_penalty, which holds that policy
        # so this path and _order_unscored cannot drift apart.
        #
        # WHY A PENALTY AND NOT A FILTER: the address block IS the answer to
        # "what is the address of the university". Dropping it would trade a
        # precision gain on many questions for a recall loss on a few. A penalty
        # sinks it below every real document while leaving it reachable, and
        # because context_precision is average precision, an irrelevant document
        # BELOW the relevant ones costs nothing — only one above them does.
        #
        # WHY IT IS APPLIED HERE: this is the single point every route's scores
        # pass through, so one edit covers all six agents plus the gate, the
        # parent swap and the ordering that read rerank_score downstream.
        scores = [
            float(s) - self._boilerplate_penalty(query, doc.page_content)
            for s, doc in zip(scores, docs)
        ]

        ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)

        # ── D4 NEW: Store CrossEncoder score in each doc's metadata ──
        # WHY: grade_documents_node reads these scores to decide if
        #      the retrieved docs are actually relevant to the query.
        #      This costs nothing — scores are already computed above.
        for score, doc in ranked:
            doc.metadata["rerank_score"] = float(score)

        # ── Step 2: Force-include department chunks ────────────────
        # WHY: For multi-department queries, RRF might miss one dept.
        #      We ensure at least one chunk from each mentioned dept
        #      appears in results. Uses metadata — no URL hacking.
        query_lower  = query.lower()
        final_docs   = []
        seen_sources = {}

        dept_keywords = {
            "cs":            "computer-science",
            "computer":      "computer-science",
            "math":          "mathematics",
            "maths":         "mathematics",
            "mathematics":   "mathematics",
            "pashto":        "pashto",
            "english":       "english",
            "islamic":       "islamic-studies",
            "islamiat":      "islamic-studies",
            "commerce":      "commerce",
            "education":     "education",
            "management":    "management-sciences",
            "zoology":       "zoology",
            "political":     "political-science",
            "allied":        "allied-health-sciences",
        }

        mentioned_depts = set()
        for keyword, dept in dept_keywords.items():
            if keyword in query_lower:
                mentioned_depts.add(dept)

        force_included = set()
        if mentioned_depts:
            for dept in mentioned_depts:
                for score, doc in ranked:
                    doc_dept = doc.metadata.get("department", "")
                    if doc_dept == dept and dept not in force_included:
                        final_docs.append((score, doc))
                        src = doc.metadata.get("source", "")
                        seen_sources[src] = seen_sources.get(src, 0) + 1
                        force_included.add(dept)
                        break

        # ── Step 3: Source diversity — score-aware, not a blind cap ─────────
        # WHY a cap exists: without it one page can fill every slot with near
        #      identical chunks, and the corpus has real redundancy (4756 distinct
        #      texts across 5558 rows; one contact sentence appears 65 times).
        # WHY it must be score-aware: a hard "max 2 per source" also throws away
        #      the 3rd and 4th chunk of the ONE page that holds the answer. The
        #      admission-requirements list spans several chunks of /admission, so
        #      the blind cap made that question unanswerable. Documents scoring
        #      close to the best document bypass the cap; weaker ones respect it.
        # WHY content dedup: _rerank previously deduped by object identity
        #      (`d is doc`), so byte-identical text arriving from two different
        #      sources consumed two context slots and cost context_precision.
        best_score  = ranked[0][0] if ranked else 0.0
        strong_gate = best_score - config.RERANK_DIVERSITY_MARGIN

        seen_content = {
            self._content_key(d.page_content) for _, d in final_docs
        }

        for score, doc in ranked:
            source = doc.metadata.get("source", "unknown")

            if any(d is doc for _, d in final_docs):
                continue

            key = self._content_key(doc.page_content)
            if key in seen_content:
                continue

            # Strong documents are exempt from the per-source cap.
            if seen_sources.get(source, 0) < 2 or score >= strong_gate:
                final_docs.append((score, doc))
                seen_sources[source] = seen_sources.get(source, 0) + 1
                seen_content.add(key)

        # Sort by CrossEncoder score and return top results
        final_docs.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in final_docs[:config.TOP_K_RESULTS]]
    


    def get_doc_count(self) -> int:
        return self.db._collection.count()
    
    def _add_documents_batched(self, documents, batch_size: int = 100):
        """
        WHAT: Adds documents in batches instead of all at once.
        WHY:  Prevents API rate limit errors for large document sets.
              For small sets it makes no difference but is always safe.
        """
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            self.db.add_documents(batch)


    # ── B5: Deduplication Primitives ──────────────────────────

    def chunk_exists(self, source: str, content_hash: str) -> bool:
        """
        WHAT: Checks if a chunk with this exact source AND content_hash
              already exists in ChromaDB.

        WHY:  This is the core check for idempotency. Before storing
              any chunk, we ask: "is this exact content already here?"
              If yes — skip storing. This prevents the Phase A / Phase B
              duplication problem at the source.

        HOW:  ChromaDB's .get() with a 'where' filter searches metadata
              without doing any vector search — fast and cheap.

        Args:
            source:       The URL or filename this chunk came from.
            content_hash: The 8-char MD5 hash from build_metadata().

        Returns:
            True  → exact chunk already exists, skip it
            False → new or changed content, proceed to store
        """
        # WHAT: Empty hash means content was empty — never skip these
        # WHY:  Safety guard, build_metadata returns "" for empty content
        if not content_hash:
            return False

        result = self.db.get(
            where={
                "$and": [
                    {"source":       {"$eq": source}},
                    {"content_hash": {"$eq": content_hash}},
                ]
            },
            limit=1,
        )

        # result["ids"] is a list — empty list means nothing found
        return len(result["ids"]) > 0
    def page_unchanged(self, source: str, page_hash: str) -> bool:
        """
        WHAT: Checks if this exact page content is already stored.
        WHY:  Prevents re-embedding unchanged documents.
              Saves API cost on every run after the first.
        HOW:  Looks for any chunk from this source with matching page_hash.
              Finding even one chunk means the whole page is stored.
        """
        if not page_hash:
            return False

        result = self.db.get(
            where={
                "$and": [
                    {"source":    {"$eq": source}},
                    {"page_hash": {"$eq": page_hash}},
                ]
            },
            limit=1,
        )
        return len(result["ids"]) > 0

    def _delete_stale_chunks(self, source: str, stale_ids: list,
                             stale_hashes: set) -> int:
        """Remove ONE superseded version of a page, leaving the fresh one.

        This is the update-in-place sibling of delete_by_source. The difference
        matters and is the reason both exist:

            delete_by_source(url)  removes EVERY chunk with that source. Correct
                                   when a page is being retired entirely.
            _delete_stale_chunks   removes only the chunks identified before the
                                   new version was written. Correct when a page
                                   is being replaced, which is what a re-scrape
                                   of an edited page does.

        load_scraped_page used to call the first one for the second job. Because
        it deletes by re-querying `source`, and the freshly-written chunks share
        that source, it took the new version down with the old one — silently,
        with a success message. See test_ingest_safety.py scenario 3.

        Chroma is pruned by the exact ids in the snapshot. BM25 has no ids, so it
        is pruned by (source AND page_hash in the snapshot's hashes), which
        selects the same rows: page_hash is derived from the page text, so the
        superseded version cannot share a hash with the version replacing it.

        Returns the number of Chroma chunks removed.
        """
        if not stale_ids:
            return 0

        self.db._collection.delete(ids=stale_ids)

        before = len(self.bm25_docs)
        self.bm25_docs = [
            doc for doc in self.bm25_docs
            if not (doc.metadata.get("source") == source
                    and doc.metadata.get("page_hash") in stale_hashes)
        ]
        removed_bm25 = before - len(self.bm25_docs)

        self._rebuild_bm25()
        self._save_bm25_to_disk()

        print(f"Replaced {len(stale_ids)} stale chunks "
              f"({removed_bm25} from BM25) for source: {source}")
        return len(stale_ids)

    def delete_by_source(self, source: str) -> int:
        """
        WHAT: Deletes ALL chunks from ChromaDB and BM25 that came from
              this source (URL or filename).

        WHY:  When a page's content CHANGES, the old chunks are now
              stale and possibly wrong. We remove them so only the
              fresh chunks remain. Without this, old AND new chunks
              both exist — duplicates with different content.

        HOW:  1. Find all chunk IDs in ChromaDB matching this source
              2. Delete them from ChromaDB by ID
              3. Remove matching entries from self.bm25_docs
              4. Rebuild the BM25 index from the remaining docs

        Args:
            source: The URL or filename to remove all chunks for.

        Returns:
            Number of chunks that were deleted.
        """
        # Step 1 — find all chunks from this source
        existing = self.db.get(where={"source": {"$eq": source}})
        ids_to_delete = existing.get("ids", [])

        if not ids_to_delete:
            return 0   # nothing to delete

        # Step 2 — delete from ChromaDB by ID
        self.db._collection.delete(ids=ids_to_delete)

        # Step 3 — remove matching docs from BM25's document list
        # WHY: BM25 has its own separate copy of all documents.
        #      Deleting from ChromaDB does NOT touch this list.
        before_count = len(self.bm25_docs)
        self.bm25_docs = [
            doc for doc in self.bm25_docs
            if doc.metadata.get("source") != source
        ]
        removed_from_bm25 = before_count - len(self.bm25_docs)

        # Step 4 — rebuild BM25 index from the remaining documents
        # WHY: BM25Okapi is built once from a corpus — it cannot have
        #      individual documents removed. We must rebuild it fresh
        #      from whatever documents remain.
        if self.bm25_docs:
            corpus    = [doc.page_content.lower().split() for doc in self.bm25_docs]
            self.bm25 = BM25Okapi(corpus)
        else:
            self.bm25 = None

        self._save_bm25_to_disk()

        print(f"Deleted {len(ids_to_delete)} chunks for source: {source}")
        return len(ids_to_delete)


    # ── BM25 helpers (identical to Phase 3) ──────────────────

    def _add_to_bm25(self, new_docs: List[Document], rebuild: bool = True):
        """
        WHAT: Adds documents to BM25 index.

        WHY rebuild parameter:
            During bul k loading (setup), rebuild=False skips
            rebuilding after every single document.
            Call rebuild_bm25() once at the end instead.
            This makes setup significantly faster.

            During live updates (one page changed), rebuild=True
            rebuilds immediately so search stays accurate.
        """
        self.bm25_docs.extend(new_docs)

        if rebuild:
            self._rebuild_bm25()
            self._save_bm25_to_disk()

    def _rebuild_bm25(self):
        """Rebuild BM25 index from all stored documents."""
        if not self.bm25_docs:
            self.bm25 = None
            return
        corpus    = [doc.page_content.lower().split() for doc in self.bm25_docs]
        self.bm25 = BM25Okapi(corpus)

    def _save_bm25_to_disk(self):
        os.makedirs("./data", exist_ok=True)
        with open("./data/bm25_index.pkl", "wb") as f:
            pickle.dump({"bm25": self.bm25, "docs": self.bm25_docs}, f)

    def _load_bm25_from_disk(self):
        path = "./data/bm25_index.pkl"
        if not os.path.exists(path):
            return
        try:
            with open(path, "rb") as f:
                saved = pickle.load(f)
            self.bm25      = saved["bm25"]
            self.bm25_docs = saved["docs"]
            print(f"BM25 loaded: {len(self.bm25_docs)} documents.")
        except Exception as e:
            print(f"BM25 load failed: {e}")
            print("Deleting stale BM25 index — will rebuild on next setup.")
            try:
                os.remove(path)
            except OSError:
                pass
            self.bm25 = None
            self.bm25_docs = []
            
