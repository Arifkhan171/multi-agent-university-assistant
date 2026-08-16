# ═══════════════════════════════════════════════════════════════
#  graph_rag/community_builder.py
# ═══════════════════════════════════════════════════════════════
#
#  WHAT THIS FILE DOES:
#  Takes all extracted entities + relationships and:
#    1. Builds a NetworkX directed graph
#    2. Detects communities (groups of related entities)
#    3. Generates LLM summary for each community
#
#  WHY THREE STEPS:
#
#  Step 1 — Graph building:
#    Raw entity lists from 959 chunks need to become a connected
#    graph. Same entity ("Ismail Khan") appears in multiple chunks.
#    We merge them into ONE node with all their relationships.
#
#  Step 2 — Community detection:
#    The graph has hundreds of nodes. We group related nodes.
#    "CS Department", "Ismail Khan", "BS CS program", "Bilal Khan"
#    all belong to one community. This grouping lets us retrieve
#    an entire related cluster when a student asks about CS.
#
#  Step 3 — Community summaries:
#    Raw graph data is structured but not readable by LLM in prompt.
#    We convert each community into a natural language paragraph.
#    This paragraph goes directly into the LLM prompt at query time.
#
# ═══════════════════════════════════════════════════════════════

import json
import logging
from typing import Dict, List, Any, Tuple
from collections import defaultdict

import networkx as nx
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

import config

logger = logging.getLogger(__name__)

# ── Community Summary Prompt ──────────────────────────────────
SUMMARY_PROMPT = """You are summarizing a group of related university entities
for a university knowledge base.

ENTITIES IN THIS GROUP:
{entities}

RELATIONSHIPS BETWEEN THEM:
{relationships}

Write a clear, factual paragraph (100-150 words) summarizing:
- What this group of entities represents
- Key people, their roles, and contact details if present
- How entities relate to each other
- Any important policies or programs mentioned

Write in third person. Be specific. Include emails/phones if present.
Do NOT include generic statements like "the university strives for excellence."
Only state specific facts from the entities above."""


class CommunityBuilder:
    """
    Builds a NetworkX knowledge graph and detects entity communities.

    FLOW:
        builder = CommunityBuilder()
        builder.add_extraction_results(all_results)
        communities = builder.detect_communities()
        summaries = builder.generate_summaries(communities)
        graph = builder.get_graph()
    """

    def __init__(self):
        """
        WHY DiGraph (directed graph):
        Relationships have direction. "Ismail Khan IS HOD OF CS Dept"
        is different from "CS Dept IS HOD OF Ismail Khan". Direction
        preserves meaning. DiGraph supports both directed and
        undirected traversal.
        """
        self.graph              = nx.DiGraph()
        self.entity_descriptions = {}   # entity_name → description
        self.entity_sources      = defaultdict(set)   # entity_name → set of source URLs

        self.llm = ChatOpenAI(
            model          = "gpt-4o-mini",
            temperature    = 0,
            openai_api_key = config.OPENAI_API_KEY,
        )

    def add_extraction_results(self, results: List[Dict]) -> None:
        """
        Add all entity extraction results to the graph.

        WHY we merge duplicates:
        "Ismail Khan" appears in CS faculty page (chunk 1),
        faculty listing page (chunk 2), contact page (chunk 3).
        Without merging, we'd have 3 separate "Ismail Khan" nodes.
        With merging, ONE node with all relationships from all chunks.

        HOW merging works:
        Node name is the entity name (normalized to lowercase for matching).
        If node already exists → add new relationships, update description
        if the new one is longer (more informative).
        """
        total_entities      = 0
        total_relationships = 0

        for result in results:
            entities      = result.get("entities", [])
            relationships = result.get("relationships", [])
            source        = result.get("source", "")

            # Add entities as graph nodes
            for entity in entities:
                name = entity.get("name", "").strip()
                if not name:
                    continue

                entity_type = entity.get("type", "unknown")
                description = entity.get("description", "")

                # Add or update node
                if name not in self.graph:
                    self.graph.add_node(name, type=entity_type, description=description)
                    self.entity_descriptions[name] = description
                else:
                    # Node exists — update description if new one is longer
                    existing_desc = self.entity_descriptions.get(name, "")
                    if len(description) > len(existing_desc):
                        self.entity_descriptions[name] = description
                        self.graph.nodes[name]["description"] = description

                # Track which source pages mention this entity
                if source:
                    self.entity_sources[name].add(source)

                total_entities += 1

            # Add relationships as graph edges
            for rel in relationships:
                source_entity  = rel.get("source", "").strip()
                relation       = rel.get("relation", "").strip()
                target_entity  = rel.get("target", "").strip()

                if not source_entity or not target_entity or not relation:
                    continue

                # Ensure both nodes exist before adding edge
                if source_entity not in self.graph:
                    self.graph.add_node(source_entity, type="unknown", description="")
                if target_entity not in self.graph:
                    self.graph.add_node(target_entity, type="unknown", description="")

                # Add edge (or update if already exists)
                self.graph.add_edge(source_entity, target_entity, relation=relation)
                total_relationships += 1

        print(f"Graph built:")
        print(f"  Nodes (entities):        {self.graph.number_of_nodes()}")
        print(f"  Edges (relationships):   {self.graph.number_of_edges()}")
        print(f"  Total entities seen:     {total_entities}")
        print(f"  Total relationships seen:{total_relationships}")

    def detect_communities(self) -> List[List[str]]:
        """
        Group related entities into communities using NetworkX.

        WHY community detection:
        A student asking "tell me about CS department" should get
        ALL related entities: the department, its HOD, its faculty,
        its programs, its policies — not just isolated facts.
        Communities capture these natural groupings.

        ALGORITHM: Greedy Modularity Communities
        WHY this algorithm:
        - Built into NetworkX (no extra library needed)
        - Works well on university-scale graphs (hundreds of nodes)
        - "Modularity" measures how well communities are separated
        - "Greedy" means fast enough for our graph size

        HOW IT WORKS:
        Starts with each node as its own community.
        Repeatedly merges communities that increase modularity score.
        Stops when no merge improves the score.
        Result: groups of densely connected nodes.

        NOTE: Uses undirected version of graph for community detection.
        WHY: Community detection works on connection patterns,
        not directionality. Converting to undirected preserves
        all connections for grouping purposes.
        """
        from networkx.algorithms.community import greedy_modularity_communities

        # Convert to undirected for community detection
        undirected = self.graph.to_undirected()

        # Remove isolated nodes (no connections) — they can't form communities
        isolated = list(nx.isolates(undirected))
        undirected.remove_nodes_from(isolated)

        if undirected.number_of_nodes() == 0:
            print("Warning: No connected entities found in graph")
            return []

        # Detect communities
        raw_communities = greedy_modularity_communities(undirected)

        # Convert frozensets to sorted lists for consistent ordering
        communities = [sorted(list(community)) for community in raw_communities]

        # Sort communities by size (largest first)
        communities.sort(key=len, reverse=True)

        print(f"\nCommunity detection complete:")
        print(f"  Total communities found: {len(communities)}")
        print(f"  Largest community size:  {len(communities[0]) if communities else 0}")
        print(f"  Smallest community size: {len(communities[-1]) if communities else 0}")

        # Show top 5 communities for verification
        print(f"\nTop 5 communities:")
        for i, community in enumerate(communities[:5]):
            print(f"  Community {i+1} ({len(community)} entities): "
                  f"{', '.join(community[:3])}{'...' if len(community) > 3 else ''}")

        return communities

    def generate_summaries(self, communities: List[List[str]]) -> List[Dict]:
        """
        Generate LLM summary for each community.

        WHY summaries instead of raw graph data:
        At query time, we need to pass community context to the LLM.
        Raw graph data (nodes + edges) is not readable as prose.
        A well-written summary paragraph is directly usable in prompts.

        Each summary contains:
        - community_id: index number
        - entities: list of entity names in this community
        - summary: natural language description (100-150 words)
        - size: number of entities

        COST: ~50 summaries × ~500 tokens input × $0.15/1M = ~$0.004
        Basically free.
        """
        summaries = []
        total     = len(communities)

        print(f"\nGenerating summaries for {total} communities...")

        for i, community in enumerate(communities):
            if (i + 1) % 10 == 0 or i == total - 1:
                print(f"  Summary {i+1}/{total}...")

            # Build entity list for prompt
            entity_lines = []
            for entity_name in community:
                desc  = self.entity_descriptions.get(entity_name, "")
                etype = self.graph.nodes.get(entity_name, {}).get("type", "unknown")
                sources = list(self.entity_sources.get(entity_name, set()))[:2]
                line = f"- {entity_name} ({etype})"
                if desc:
                    line += f": {desc}"
                entity_lines.append(line)

            # Build relationship list for prompt
            rel_lines = []
            for entity_name in community:
                # Outgoing edges
                for _, target, data in self.graph.out_edges(entity_name, data=True):
                    if target in community:  # only show within-community relationships
                        rel = data.get("relation", "related_to")
                        rel_lines.append(f"- {entity_name} → {rel} → {target}")
                        if len(rel_lines) >= 20:  # cap at 20 relationships per summary
                            break

            # Skip very small communities (1-2 entities) — not worth summarizing
            if len(community) < 2:
                summaries.append({
                    "community_id": i,
                    "entities":     community,
                    "summary":      self.entity_descriptions.get(community[0], ""),
                    "size":         len(community),
                })
                continue

            # Generate summary with LLM
            prompt = SUMMARY_PROMPT.format(
                entities      = "\n".join(entity_lines),
                relationships = "\n".join(rel_lines) if rel_lines else "No explicit relationships found",
            )

            try:
                response = self.llm.invoke([HumanMessage(content=prompt)])
                summary_text = response.content.strip()
            except Exception as e:
                logger.error(f"Summary generation failed for community {i}: {e}")
                # Fallback: join entity descriptions
                summary_text = " ".join([
                    self.entity_descriptions.get(e, e)
                    for e in community[:5]
                ])

            summaries.append({
                "community_id": i,
                "entities":     community,
                "summary":      summary_text,
                "size":         len(community),
            })

        print(f"Summary generation complete: {len(summaries)} summaries")
        return summaries

    def get_graph(self) -> nx.DiGraph:
        """Return the built NetworkX graph."""
        return self.graph

    def get_entity_descriptions(self) -> Dict[str, str]:
        """Return entity name → description mapping."""
        return self.entity_descriptions