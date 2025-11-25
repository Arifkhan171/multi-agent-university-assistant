"""
E5 — Proposition-Based Chunking
=================================
Decomposes parent chunks into atomic self-contained facts.
Each proposition = one clear standalone searchable fact.

WHY:
Fixed-size chunks mix multiple facts. The embedding represents
the average meaning of all facts combined. Retrieval for any
specific fact is imprecise because the vector is diluted.

Proposition chunks give each fact its own precise vector.
"What is Ismail Khan's email?" retrieves exactly that fact.
Nothing else dilutes the embedding.

USAGE:
Called only by setup_propositions.py during KB rebuild.
Never called at query time. Zero query latency impact.
"""

import os
import re
import time
from typing import List
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage


class PropositionChunker:
    """
    Extracts atomic propositions from parent documents using gpt-4o-mini.

    Each proposition is:
    - One complete self-contained fact (10-60 words)
    - Independently searchable without surrounding context
    - Linked to its parent via parent_id metadata (E2 still works)
    """

    PROPOSITION_PROMPT = (
        "You are decomposing a university document into atomic facts.\n\n"
        "Extract every distinct fact from the document below as a numbered list.\n\n"
        "Rules:\n"
        "- Each fact must be COMPLETE and SELF-CONTAINED\n"
        "  (must make full sense when read alone, without any other context)\n"
        "- Each fact must be SHORT — one sentence, 10-60 words maximum\n"
        "- If a person is mentioned, always include their role and department\n"
        "- If a number or percentage is mentioned, include what it refers to\n"
        "- If a fee is mentioned, include what it is for\n"
        "- Do NOT merge multiple facts into one line\n"
        "- Do NOT include vague facts like 'the university has many programs'\n"
        "- Do NOT add any fact not found in the document\n\n"
        "Example good output:\n"
        "1. Ismail Khan is the Head of the Computer Science Department at UOLI.\n"
        "2. Ismail Khan can be contacted at hod.cs@uoli.edu.pk with extension 1054.\n"
        "3. Students must maintain 75% attendance to sit in final examinations.\n"
        "4. Students below 75% attendance must repeat the course when offered again.\n"
        "5. Two weeks consecutive absence results in the student's name being struck off.\n\n"
        "DOCUMENT:\n"
        "{document}\n\n"
        "ATOMIC FACTS (numbered list only, no other text):"
    )

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        batch_delay: float = 0.5,
    ):
        """
        Args:
            model       : OpenAI model for extraction (gpt-4o-mini is sufficient)
            batch_delay : seconds between API calls to avoid rate limiting
        """
        self.llm = ChatOpenAI(
            model          = model,
            temperature    = 0,
            max_tokens     = 1500,
            openai_api_key = os.getenv("OPENAI_API_KEY"),
        )
        self.batch_delay = batch_delay

    def extract_propositions(
        self,
        parent_content : str,
        parent_metadata: dict,
        parent_id      : str,
    ) -> List[Document]:
        """
        Extract atomic propositions from one parent document.

        Args:
            parent_content  : full text of the parent chunk
            parent_metadata : original metadata (source, category, url etc.)
            parent_id       : ID for E2 linking — each proposition keeps this

        Returns:
            List of Documents, one per proposition.
            Falls back to [original document] if extraction fails.
        """
        if not parent_content or len(parent_content.strip()) < 30:
            return []

        # Limit to 2000 chars — enough for extraction, saves tokens
        prompt = self.PROPOSITION_PROMPT.format(
            document=parent_content[:2000]
        )

        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            raw      = response.content.strip()
        except Exception as e:
            print(f"[E5] LLM call failed for {parent_id[:30]}: {e}")
            return self._fallback_doc(parent_content, parent_metadata, parent_id)

        # Parse numbered list
        propositions = []
        for line in raw.split("\n"):
            line    = line.strip()
            if not line:
                continue
            # Remove leading "1. " or "1) " or "1 - " etc.
            cleaned = re.sub(r"^\d+[\.\)\-\s]+", "", line).strip()
            if len(cleaned) >= 10:
                propositions.append(cleaned)

        if not propositions:
            print(f"[E5] No propositions parsed for {parent_id[:30]}. Fallback.")
            return self._fallback_doc(parent_content, parent_metadata, parent_id)

        # Build one Document per proposition
        docs = []
        for i, prop in enumerate(propositions):
            docs.append(Document(
                page_content = prop,
                metadata     = {
                    **parent_metadata,
                    "parent_id"   : parent_id,
                    "source_type" : "proposition",
                    "prop_index"  : i,
                }
            ))

        return docs

    def _fallback_doc(
        self,
        content : str,
        metadata: dict,
        pid     : str,
    ) -> List[Document]:
        """Return original content as single doc when extraction fails."""
        return [Document(
            page_content = content,
            metadata     = {
                **metadata,
                "parent_id"  : pid,
                "source_type": "proposition_fallback",
            }
        )]