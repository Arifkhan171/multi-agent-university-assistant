# ═══════════════════════════════════════════════════════════════
#  graph_rag/entity_extractor.py
# ═══════════════════════════════════════════════════════════════
#
#  WHAT THIS FILE DOES:
#  Reads one KB chunk at a time and extracts:
#    - Entities: named things (people, departments, offices, policies)
#    - Relationships: how entities connect to each other
#
#  WHY WE NEED THIS:
#  ChromaDB stores text chunks. GraphRAG needs a GRAPH — nodes and edges.
#  This file is the bridge: it reads text and produces graph data.
#
#  HOW IT WORKS:
#  1. Send chunk text to GPT-4o-mini with extraction prompt
#  2. GPT-4o-mini returns JSON with entities + relationships
#  3. We parse and return the structured data
#
#  COST: ~$0.19 total for all 959 chunks (one-time only)
#
#  PROGRESS SAVING:
#  If the script crashes at chunk 500, it resumes from 500.
#  Every chunk result is saved to a progress file immediately.
#  WHY: Entity extraction takes 10-15 minutes. Without progress
#  saving, a crash means starting over and paying twice.
#
# ═══════════════════════════════════════════════════════════════

import json
import time
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

import config

logger = logging.getLogger(__name__)

# ── Extraction Prompt ─────────────────────────────────────────
# WHY so specific: Vague prompts produce vague entity lists.
# We define exactly what entity types exist for a UNIVERSITY.
# This ensures consistent, useful entities across all 959 chunks.

EXTRACT_PROMPT = """You are extracting a knowledge graph from a university document.

DOCUMENT TEXT:
{text}

Extract ALL entities and relationships from this text.

ENTITY TYPES (use these exactly):
- person      : named people (VC, HOD, Dean, Registrar, faculty, staff)
- department  : academic departments (Computer Science, Mathematics, etc.)
- office      : administrative offices (Registrar Office, ORIC, DQE, etc.)
- policy      : rules or policy documents (Attendance Policy, Drug Policy, etc.)
- program     : academic programs (BS Computer Science, MS Mathematics, etc.)
- university  : the university itself or its components

RELATIONSHIP TYPES (use these exactly):
- is_hod_of         : person IS HOD OF department
- works_in          : person WORKS IN department/office
- reports_to        : person/office REPORTS TO person/office
- oversees          : person OVERSEES department/office
- belongs_to        : department/program BELONGS TO university
- offers            : department OFFERS program
- governed_by       : department/program GOVERNED BY policy
- located_in        : office/department LOCATED IN building/campus
- collaborates_with : department/office COLLABORATES WITH department/office
- has_contact       : person/office HAS CONTACT email/phone

RULES:
- Only extract entities that are explicitly mentioned in the text
- Do NOT invent entities not present in the text
- Use the person's full name as entity name
- If text is too short or has no entities, return empty lists
- Keep entity names consistent (always "University of Loralai" not "UOLI" sometimes)

Return ONLY valid JSON, no explanation:
{{
  "entities": [
    {{"name": "Ismail Khan", "type": "person", "description": "HOD of Computer Science Department"}},
    {{"name": "Computer Science Department", "type": "department", "description": "CS department at UOLI"}}
  ],
  "relationships": [
    {{"source": "Ismail Khan", "relation": "is_hod_of", "target": "Computer Science Department"}}
  ]
}}"""


class EntityExtractor:
    """
    Extracts entities and relationships from text chunks using GPT-4o-mini.

    USAGE:
        extractor = EntityExtractor(progress_file="data/graph_rag/progress.json")
        result = extractor.extract_from_chunk(chunk_text, chunk_id="chunk_001")

    PROGRESS SAVING:
        Results are saved to progress_file after each chunk.
        On restart, already-processed chunks are skipped automatically.
    """

    def __init__(self, progress_file: str = "data/graph_rag/progress.json"):
        """
        WHY progress_file: So we can resume if extraction is interrupted.
        The file stores {chunk_id: {entities: [...], relationships: [...]}}
        """
        self.progress_file = Path(progress_file)
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing progress (if any)
        self.progress = self._load_progress()

        # Use cheapest model — extraction needs understanding, not creativity
        self.llm = ChatOpenAI(
            model          = "gpt-4o-mini",
            temperature    = 0,              # deterministic extraction
            openai_api_key = config.OPENAI_API_KEY,
        )

        print(f"EntityExtractor ready. Already extracted: {len(self.progress)} chunks")

    def _load_progress(self) -> Dict[str, Any]:
        """Load previously extracted data from progress file."""
        if self.progress_file.exists():
            with open(self.progress_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_progress(self):
        """Save current progress to file immediately after each chunk."""
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    def extract_from_chunk(self,
                           text: str,
                           chunk_id: str,
                           source: str = "") -> Dict[str, List]:
        """
        Extract entities and relationships from one chunk.

        RETURNS:
        {
            "entities": [{"name": ..., "type": ..., "description": ...}],
            "relationships": [{"source": ..., "relation": ..., "target": ...}]
        }

        SKIPS chunks already in progress file — resumable extraction.
        """
        # Skip if already extracted
        if chunk_id in self.progress:
            return self.progress[chunk_id]

        # Skip chunks that are too short to have meaningful entities
        # WHY: Very short chunks (< 50 chars) are usually navigation text
        # or headers with no entity information
        if len(text.strip()) < 50:
            result = {"entities": [], "relationships": [], "source": source}
            self.progress[chunk_id] = result
            self._save_progress()
            return result

        # Build prompt with chunk text
        prompt = EXTRACT_PROMPT.format(text=text[:2000])  # cap at 2000 chars

        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            raw = response.content.strip()

            # Clean up common LLM formatting issues
            # WHY: LLM sometimes wraps JSON in ```json ... ``` markdown fences
            if "```" in raw:
                for part in raw.split("```"):
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        raw = part
                        break

            # Parse JSON response
            data = json.loads(raw)

            result = {
                "entities":      data.get("entities", []),
                "relationships": data.get("relationships", []),
                "source":        source,
            }

        except json.JSONDecodeError:
            # LLM returned non-JSON — return empty result
            # WHY not crash: One bad chunk should not stop the whole pipeline
            logger.warning(f"JSON parse failed for chunk {chunk_id}. Skipping.")
            result = {"entities": [], "relationships": [], "source": source}

        except Exception as e:
            # API error — wait and the outer loop will retry
            logger.error(f"API error for chunk {chunk_id}: {e}")
            raise  # re-raise so build_graph_rag.py can handle retry

        # Save progress immediately
        self.progress[chunk_id] = result
        self._save_progress()

        return result

    def extract_all_chunks(self,
                           chunks: List[Dict],
                           delay: float = 0.2) -> List[Dict]:
        """
        Extract entities from ALL chunks with progress display.

        chunks: list of {"id": str, "text": str, "source": str}
        delay:  seconds between API calls (avoids rate limiting)

        RETURNS: list of extraction results (same order as input)

        WHY delay: GPT-4o-mini has rate limits. 0.2 seconds between
        calls means ~5 calls/second — safely under rate limits.
        """
        total    = len(chunks)
        results  = []
        skipped  = 0
        new_done = 0
        failed   = 0

        print(f"\nExtracting entities from {total} chunks...")
        print(f"Already done: {len(self.progress)} chunks\n")

        for i, chunk in enumerate(chunks):
            chunk_id = chunk["id"]
            text     = chunk["text"]
            source   = chunk.get("source", "")

            # Progress display every 50 chunks
            if (i + 1) % 50 == 0 or i == total - 1:
                print(f"  Progress: {i+1}/{total} "
                      f"(new: {new_done}, skipped: {skipped}, failed: {failed})")

            try:
                # Check if already done (no API call needed)
                already_done = chunk_id in self.progress
                result = self.extract_from_chunk(text, chunk_id, source)
                results.append(result)

                if already_done:
                    skipped += 1
                else:
                    new_done += 1
                    # Only delay for NEW extractions (not cached ones)
                    time.sleep(delay)

            except Exception as e:
                logger.error(f"Failed chunk {chunk_id}: {e}")
                failed += 1
                results.append({"entities": [], "relationships": [], "source": source})
                time.sleep(1)  # longer delay after failure

        print(f"\nExtraction complete!")
        print(f"  New chunks extracted : {new_done}")
        print(f"  Skipped (cached)     : {skipped}")
        print(f"  Failed               : {failed}")
        print(f"  Total results        : {len(results)}")

        return results

    def get_all_results(self) -> Dict[str, Any]:
        """Return all cached extraction results."""
        return self.progress