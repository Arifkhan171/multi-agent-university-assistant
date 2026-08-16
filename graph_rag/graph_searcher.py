# ═══════════════════════════════════════════════════════════════
#  graph_rag/graph_searcher.py
# ═══════════════════════════════════════════════════════════════
#
#  WHAT THIS FILE DOES:
#  At query time, searches the knowledge graph to find relevant
#  community summaries and entity information.
#
#  TWO SEARCH MODES (Microsoft's Local + Global):
#
#  LOCAL SEARCH:
#    Student asks about a specific entity: "Tell me about Ismail Khan"
#    → Find "Ismail Khan" node in graph
#    → Get all neighbors (CS Dept, HOD role, his email)
#    → Get the community summary for Ismail Khan's community
#    → Return this focused context to the LLM
#
#  GLOBAL SEARCH:
#    Student asks a broad question: "What departments exist and how are they connected?"
#    → No specific entity to start from
#    → Retrieve TOP community summaries (biggest communities)
#    → Return multiple community summaries to LLM
#    → LLM synthesizes a comprehensive answer
#
#  DETECTION:
#  We detect which mode to use with a two-step approach:
#    Step 1: Keyword check (free, instant) — obvious cases
#    Step 2: Entity name matching in graph — if a query mentions
#            a known entity name → LOCAL search
#            Otherwise → GLOBAL search
#
#  WHEN TO USE GRAPHRAG vs STANDARD VECTOR SEARCH:
#    GraphRAG is for RELATIONSHIP questions:
#      "How are departments connected?"
#      "Who reports to whom?"
#      "What is the structure of university administration?"
#
#    Standard vector search is for FACT questions:
#      "What is VC email?"    → single fact, vector search better
#      "Attendance policy?"   → single policy, vector search better
#
#    WHY: GraphRAG adds overhead (graph traversal). For simple
#    fact questions, it's overkill. Vector search is faster and
#    equally accurate for simple lookups.
#
# ═══════════════════════════════════════════════════════════════

import re
import logging
from typing import Dict, List, Any, Optional, Tuple

import networkx as nx

logger = logging.getLogger(__name__)

# ── Keywords that indicate relationship questions ────────────
# WHY keyword check first: It's free and instant.
# If query clearly asks about relationships, use GraphRAG.
# If query is simple factual, skip GraphRAG entirely.

RELATIONSHIP_KEYWORDS = [
    # Structural questions
    "relationship", "connected", "connection", "how are",
    "structure", "hierarchy", "organization", "how is",
    "related", "between", "link", "linked",
    # Overview questions
    "all departments", "all offices", "overview of",
    "tell me about all", "list all", "what are all",
    # Reporting/governance questions
    "reports to", "report to", "who report",
    "who works under", "who oversees",
    "who manages", "governed by", "under which",
    "which department", "which office",
    "who is above", "who is below", "above whom",
    "chain of command", "answers to", "accountable to",
    "reporting structure", "administrative structure",
    "who is responsible to", "who is under",
    "university governed", "governance structure",
    # Comparison questions
    "compare", "difference between", "similarities",
    "both departments",
]


class GraphSearcher:
    """
    Searches the knowledge graph for relevant context at query time.

    USAGE in general_agent_node:
        searcher = GraphSearcher(graph, summaries, entity_descriptions)

        # Check if query needs graph search
        if searcher.is_graph_question(query):
            context = searcher.search(query)
            # use context in LLM prompt alongside vector search results
    """

    def __init__(self,
                 graph:                nx.DiGraph,
                 summaries:            List[Dict],
                 entity_descriptions:  Dict[str, str]):
        """
        WHY loaded at startup (not per query):
        Graph is large — loading from disk takes 1-2 seconds.
        At startup this is fine. At query time it would add
        unacceptable latency to every response.

        All three components stay in memory for the entire
        chatbot lifetime.
        """
        self.graph               = graph
        self.summaries           = summaries
        self.entity_descriptions = entity_descriptions

        # Build entity lookup index (lowercase → original name)
        # WHY: Student types "ismail khan" (lowercase).
        # Graph stores "Ismail Khan" (proper case).
        # We need case-insensitive matching.
        self.entity_index = {
            name.lower(): name
            for name in entity_descriptions.keys()
        }

        # Build community index: entity_name → community_id
        # WHY: When we find an entity, we need to quickly find
        # which community it belongs to for summary retrieval.
        self.entity_to_community = {}
        for community in summaries:
            community_id = community["community_id"]
            for entity_name in community["entities"]:
                self.entity_to_community[entity_name] = community_id

        print(f"GraphSearcher ready:")
        print(f"  Entities indexed:    {len(self.entity_index)}")
        print(f"  Communities indexed: {len(summaries)}")

    def is_graph_question(self, query: str) -> bool:
        """
        Detect if query needs graph search (relationship question)
        or standard vector search (fact question).

        TWO-STEP DETECTION:

        Step 1 — Keyword check (free, instant):
        Does the query contain relationship keywords?
        "how are departments connected" → YES (contains "connected")
        "vc email" → NO (no relationship keywords)

        Step 2 — Entity check:
        Does the query mention 2+ known entities?
        "relationship between Ismail Khan and CS Department"
        → Ismail Khan (known entity) + CS Department (known entity)
        → YES, this is a relationship question

        WHY step 2: A query can ask about relationships WITHOUT
        using keyword words. "Ismail Khan CS Department" might be
        asking how they connect. Two entities = likely relationship.
        """
        query_lower = query.lower()
        # Step 1: Keyword check
        for keyword in RELATIONSHIP_KEYWORDS:
            if keyword in query_lower:
                return True

        # Step 2: Entity count check
        entities_found = self._find_entities_in_query(query)
        if len(entities_found) >= 2:
            return True

        return False

    def _find_entities_in_query(self, query: str) -> List[str]:
        """
        Find known entity names mentioned in the query.

        APPROACH: Check if each known entity name appears
        in the query string (case-insensitive).

        WHY not NER (Named Entity Recognition):
        NER would find ANY named entity — but we only care about
        entities that are IN OUR GRAPH. Checking against our
        entity index is more precise and doesn't need extra libraries.
        """
        query_lower  = query.lower()
        found        = []

        for entity_lower, entity_original in self.entity_index.items():
            # Only check multi-word entities or names longer than 4 chars
            # WHY: Avoid false matches on short words like "CS" or "IT"
            if len(entity_lower) > 4 and entity_lower in query_lower:
                found.append(entity_original)

        return found

    def local_search(self, query: str, max_results: int = 3) -> str:
        """
        LOCAL SEARCH — for questions about specific entities.

        PROCESS:
        1. Find which entities from our graph are mentioned in query
        2. For each found entity, get its neighbors in the graph
        3. Find which community each entity belongs to
        4. Return those community summaries + entity details

        EXAMPLE:
        Query: "Tell me about Ismail Khan and his role"
        → Find "Ismail Khan" in graph
        → Get neighbors: CS Department, HOD role
        → Get community for CS Department community
        → Return community summary (describes all CS-related entities)
        """
        found_entities = self._find_entities_in_query(query)

        if not found_entities:
            # No specific entity found — fall back to global search
            return self.global_search(query, max_results)

        # Collect relevant community IDs
        relevant_community_ids = set()
        entity_details         = []

        for entity_name in found_entities[:3]:  # max 3 entities
            # Get community for this entity
            community_id = self.entity_to_community.get(entity_name)
            if community_id is not None:
                relevant_community_ids.add(community_id)

            # Get entity description
            desc = self.entity_descriptions.get(entity_name, "")
            if desc:
                entity_details.append(f"• {entity_name}: {desc}")

            # Get direct neighbors from graph
            if entity_name in self.graph:
                neighbors = list(self.graph.neighbors(entity_name))[:5]
                for neighbor in neighbors:
                    neighbor_desc = self.entity_descriptions.get(neighbor, "")
                    community_id  = self.entity_to_community.get(neighbor)
                    if community_id is not None:
                        relevant_community_ids.add(community_id)

        # Build context from relevant community summaries
        context_parts = []

        # Add entity-specific details first
        if entity_details:
            context_parts.append("SPECIFIC ENTITIES MENTIONED:\n" +
                                  "\n".join(entity_details))

        # Add community summaries
        for cid in list(relevant_community_ids)[:max_results]:
            community = next(
                (c for c in self.summaries if c["community_id"] == cid), None
            )
            if community and community.get("summary"):
                context_parts.append(
                    f"[RELATED GROUP - {community['size']} entities]\n"
                    f"{community['summary']}"
                )

        return "\n\n---\n\n".join(context_parts) if context_parts else ""

    def global_search(self, query: str, max_results: int = 3) -> str:
        """
        GLOBAL SEARCH — for broad questions about university structure.

        PROCESS:
        1. Take the largest communities (most entities = most important)
        2. Return their summaries as context

        WHY largest communities:
        Larger communities represent more central parts of the
        university knowledge graph. CS Department community is large
        (HOD + 7 faculty + programs + courses). A tiny 2-node community
        is less likely to answer a broad structural question.

        FUTURE IMPROVEMENT:
        In E4, we will add semantic similarity between query and
        community summaries to pick the MOST RELEVANT communities,
        not just the largest.
        """
        # Sort communities by size (largest first)
        sorted_communities = sorted(
            self.summaries,
            key=lambda c: c["size"],
            reverse=True
        )

        context_parts = []
        for community in sorted_communities[:max_results]:
            if community.get("summary"):
                context_parts.append(
                    f"[UNIVERSITY GROUP - {community['size']} entities]\n"
                    f"{community['summary']}"
                )

        return "\n\n---\n\n".join(context_parts) if context_parts else ""

    def search(self, query: str, max_results: int = 3) -> str:
        """
        Main search function — automatically chooses local or global.

        RETURNS: Context string to inject into LLM prompt.
        Empty string if no relevant graph context found.

        Called from general_agent_node in agents.py.
        """
        found_entities = self._find_entities_in_query(query)

        if found_entities:
            # Specific entities mentioned → local search
            context = self.local_search(query, max_results)
            search_type = "local"
        else:
            # No specific entities → global search
            context = self.global_search(query, max_results)
            search_type = "global"

        logger.info(f"GraphRAG {search_type} search for: '{query[:50]}...' "
                    f"→ {len(context)} chars of context")

        return context

    def get_stats(self) -> Dict:
        """Return statistics about the loaded graph."""
        return {
            "num_entities":      len(self.entity_descriptions),
            "num_communities":   len(self.summaries),
            "num_nodes":         self.graph.number_of_nodes(),
            "num_edges":         self.graph.number_of_edges(),
        }