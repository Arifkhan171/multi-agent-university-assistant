# ═══════════════════════════════════════════════════════════════
#  graph_rag/graph_store.py
# ═══════════════════════════════════════════════════════════════
#
#  WHAT THIS FILE DOES:
#  Saves and loads the GraphRAG data to/from disk.
#
#  WHY SAVING TO DISK:
#  Building the graph costs ~$0.25 and takes 15 minutes.
#  We never want to rebuild unless the KB changes.
#  Saving to disk means: build once, use forever.
#
#  WHAT WE SAVE:
#    data/graph_rag/
#    ├── graph.pkl          ← NetworkX graph (nodes + edges)
#    ├── summaries.json     ← community summaries (LLM-generated)
#    ├── entities.json      ← entity name → description mapping
#    └── metadata.json      ← when built, how many nodes/edges
#
#  WHY PICKLE FOR GRAPH:
#  NetworkX graphs have complex Python objects (node attributes,
#  edge attributes, adjacency lists). JSON cannot represent these.
#  Pickle serializes any Python object — perfect for NetworkX.
#
#  WHY JSON FOR SUMMARIES AND ENTITIES:
#  These are simple string data. JSON is human-readable —
#  you can open summaries.json and read the community summaries.
#  Pickle would make them unreadable binary files.
#
# ═══════════════════════════════════════════════════════════════

import json
import pickle
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime

import networkx as nx

logger = logging.getLogger(__name__)

# Default storage directory
GRAPH_RAG_DIR = Path("data/graph_rag")


class GraphStore:
    """
    Handles saving and loading of GraphRAG components.

    USAGE (saving — in build_graph_rag.py):
        store = GraphStore()
        store.save(graph, summaries, entity_descriptions)

    USAGE (loading — in graph_searcher.py at startup):
        store = GraphStore()
        if store.exists():
            graph, summaries, entities = store.load()
    """

    def __init__(self, directory: str = None):
        self.dir = Path(directory) if directory else GRAPH_RAG_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

        # File paths
        self.graph_file     = self.dir / "graph.pkl"
        self.summaries_file = self.dir / "summaries.json"
        self.entities_file  = self.dir / "entities.json"
        self.metadata_file  = self.dir / "metadata.json"

    def exists(self) -> bool:
        """
        Check if all GraphRAG files exist.
        Returns True only if ALL required files are present.

        WHY check all files: If only graph.pkl exists but summaries.json
        is missing (e.g., build was interrupted), the system is broken.
        We must rebuild in that case.
        """
        return (
            self.graph_file.exists() and
            self.summaries_file.exists() and
            self.entities_file.exists()
        )

    def save(self,
             graph:                nx.DiGraph,
             summaries:            List[Dict],
             entity_descriptions:  Dict[str, str]) -> None:
        """
        Save all GraphRAG components to disk.

        Called once at the end of build_graph_rag.py.
        Saves everything atomically — if any save fails,
        the partial files are left but exists() returns False
        until all files are complete.
        """
        print(f"\nSaving GraphRAG data to {self.dir}/...")

        # Save graph as pickle
        with open(self.graph_file, "wb") as f:
            pickle.dump(graph, f)
        print(f"  ✓ graph.pkl saved ({graph.number_of_nodes()} nodes, "
              f"{graph.number_of_edges()} edges)")

        # Save community summaries as JSON
        with open(self.summaries_file, "w", encoding="utf-8") as f:
            json.dump(summaries, f, ensure_ascii=False, indent=2)
        print(f"  ✓ summaries.json saved ({len(summaries)} communities)")

        # Save entity descriptions as JSON
        with open(self.entities_file, "w", encoding="utf-8") as f:
            json.dump(entity_descriptions, f, ensure_ascii=False, indent=2)
        print(f"  ✓ entities.json saved ({len(entity_descriptions)} entities)")

        # Save metadata
        metadata = {
            "built_at":          datetime.now().isoformat(),
            "num_nodes":         graph.number_of_nodes(),
            "num_edges":         graph.number_of_edges(),
            "num_communities":   len(summaries),
            "num_entities":      len(entity_descriptions),
        }
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        print(f"  ✓ metadata.json saved")
        print(f"\nGraphRAG build complete and saved!")

    def load(self) -> tuple:
        """
        Load all GraphRAG components from disk.

        Returns: (graph, summaries, entity_descriptions)

        Called at chatbot startup (once) in workflow.py.
        After loading, data stays in memory for all queries.
        WHY: Reading from disk on every query would be slow.
        Loading once at startup means zero disk I/O at query time.
        """
        if not self.exists():
            raise FileNotFoundError(
                f"GraphRAG data not found in {self.dir}/. "
                f"Run: python build_graph_rag.py"
            )

        print(f"Loading GraphRAG data from {self.dir}/...")

        # Load graph from pickle
        with open(self.graph_file, "rb") as f:
            graph = pickle.load(f)
        print(f"  ✓ Graph loaded: {graph.number_of_nodes()} nodes, "
              f"{graph.number_of_edges()} edges")

        # Load summaries from JSON
        with open(self.summaries_file, "r", encoding="utf-8") as f:
            summaries = json.load(f)
        print(f"  ✓ Summaries loaded: {len(summaries)} communities")

        # Load entity descriptions from JSON
        with open(self.entities_file, "r", encoding="utf-8") as f:
            entity_descriptions = json.load(f)
        print(f"  ✓ Entities loaded: {len(entity_descriptions)} entities")

        # Show metadata
        if self.metadata_file.exists():
            with open(self.metadata_file, "r") as f:
                meta = json.load(f)
            print(f"  ✓ Built at: {meta.get('built_at', 'unknown')}")

        return graph, summaries, entity_descriptions

    def get_metadata(self) -> Dict:
        """Return metadata about the built graph."""
        if self.metadata_file.exists():
            with open(self.metadata_file, "r") as f:
                return json.load(f)
        return {}

    def delete(self) -> None:
        """
        Delete all GraphRAG files.
        Use when you want to force a complete rebuild.
        """
        for f in [self.graph_file, self.summaries_file,
                  self.entities_file, self.metadata_file]:
            if f.exists():
                f.unlink()
        print(f"GraphRAG data deleted from {self.dir}/")