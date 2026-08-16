# ═══════════════════════════════════════════════════════════════
#  build_graph_rag.py  —  Run this ONCE to build the GraphRAG
# ═══════════════════════════════════════════════════════════════
#
#  USAGE:
#    python build_graph_rag.py           ← build for first time
#    python build_graph_rag.py --force   ← rebuild from scratch
#
#  WHAT IT DOES:
#    1. Loads all 959 chunks from your ChromaDB
#    2. Extracts entities + relationships from each chunk (GPT-4o-mini)
#    3. Builds NetworkX knowledge graph
#    4. Detects communities of related entities
#    5. Generates LLM summary for each community
#    6. Saves everything to data/graph_rag/
#
#  TIME: ~15-20 minutes (959 API calls for extraction)
#  COST: ~$0.25 total (one-time only)
#
#  RESUMABLE: If interrupted, run again — skips already-done chunks
#
# ═══════════════════════════════════════════════════════════════

import os
import sys
import argparse
import logging

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level  = logging.WARNING,   # only show warnings/errors
    format = "%(levelname)s: %(message)s"
)

from graph_rag.entity_extractor  import EntityExtractor
from graph_rag.community_builder import CommunityBuilder
from graph_rag.graph_store       import GraphStore
from knowledge_base import KnowledgeBase


def load_all_chunks(kb: KnowledgeBase):
    """
    Load all chunks from ChromaDB.

    Returns list of {"id": str, "text": str, "source": str}

    WHY load from ChromaDB directly (not re-scrape):
    Your KB already has all 959 processed, cleaned chunks.
    No need to re-scrape the website — we use what we already have.
    """
    print("Loading all chunks from ChromaDB...")

    # ChromaDB's .get() returns all stored documents
    # include=["documents", "metadatas", "ids"] gets text + metadata + IDs
    result = kb.db.get(
        include=["documents", "metadatas"]
    )

    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])
    ids       = result.get("ids",       [])

    chunks = []
    for doc_id, text, meta in zip(ids, documents, metadatas):
        source = meta.get("source", "") if meta else ""
        chunks.append({
            "id":     doc_id,
            "text":   text,
            "source": source,
        })

    print(f"Loaded {len(chunks)} chunks from ChromaDB")
    return chunks


def main():
    # ── Parse arguments ───────────────────────────────────────
    parser = argparse.ArgumentParser(description="Build GraphRAG knowledge graph")
    parser.add_argument(
        "--force",
        action  = "store_true",
        help    = "Force rebuild even if graph already exists"
    )
    args = parser.parse_args()

    # ── Initialize stores ─────────────────────────────────────
    store = GraphStore()

    # Check if already built
    if store.exists() and not args.force:
        meta = store.get_metadata()
        print(f"\nGraphRAG already built!")
        print(f"  Nodes:       {meta.get('num_nodes', '?')}")
        print(f"  Edges:       {meta.get('num_edges', '?')}")
        print(f"  Communities: {meta.get('num_communities', '?')}")
        print(f"  Built at:    {meta.get('built_at', '?')}")
        print(f"\nTo rebuild: python build_graph_rag.py --force")
        return

    if args.force:
        print("--force flag: deleting existing GraphRAG data...")
        store.delete()
        # Also delete extraction progress to start fresh
        progress_file = "data/graph_rag/progress.json"
        if os.path.exists(progress_file):
            os.remove(progress_file)
            print("Extraction progress cleared")

    print("\n" + "="*60)
    print("  GraphRAG Knowledge Graph Builder")
    print("  University of Loralai (UOLI) Chatbot")
    print("="*60)

    # ── Step 1: Load KB ───────────────────────────────────────
    print("\n[STEP 1/5] Loading Knowledge Base...")
    kb     = KnowledgeBase()
    chunks = load_all_chunks(kb)

    if not chunks:
        print("ERROR: No chunks found in ChromaDB.")
        print("Run: python setup_knowledge_base.py first")
        return

    # ── Step 2: Extract entities ──────────────────────────────
    print("\n[STEP 2/5] Extracting entities and relationships...")
    print("This step costs ~$0.19 and takes 15-20 minutes.")
    print("It is RESUMABLE — safe to interrupt and restart.\n")

    extractor = EntityExtractor(
        progress_file="data/graph_rag/progress.json"
    )
    results = extractor.extract_all_chunks(chunks, delay=0.2)

    # Count what we got
    total_entities      = sum(len(r.get("entities", []))      for r in results)
    total_relationships = sum(len(r.get("relationships", [])) for r in results)
    print(f"\nExtraction summary:")
    print(f"  Total entities found:      {total_entities}")
    print(f"  Total relationships found: {total_relationships}")

    # ── Step 3: Build graph ───────────────────────────────────
    print("\n[STEP 3/5] Building knowledge graph...")
    builder = CommunityBuilder()
    builder.add_extraction_results(results)
    graph = builder.get_graph()

    if graph.number_of_nodes() == 0:
        print("WARNING: Graph has no nodes!")
        print("Entity extraction may have failed. Check your API key and retry.")
        return

    # ── Step 4: Detect communities ────────────────────────────
    print("\n[STEP 4/5] Detecting entity communities...")
    communities = builder.detect_communities()

    if not communities:
        print("WARNING: No communities detected.")
        print("Graph may have no connected entities.")
        return

    # ── Step 5: Generate summaries ────────────────────────────
    print("\n[STEP 5/5] Generating community summaries...")
    print("This step costs ~$0.05 and takes 2-3 minutes.\n")
    summaries = builder.generate_summaries(communities)

    # ── Save everything ───────────────────────────────────────
    entity_descriptions = builder.get_entity_descriptions()
    store.save(graph, summaries, entity_descriptions)

    # ── Final report ──────────────────────────────────────────
    print("\n" + "="*60)
    print("  GraphRAG Build Complete!")
    print("="*60)
    print(f"  Graph nodes (entities):     {graph.number_of_nodes()}")
    print(f"  Graph edges (relationships):{graph.number_of_edges()}")
    print(f"  Communities detected:       {len(summaries)}")
    print(f"  Entity descriptions:        {len(entity_descriptions)}")
    print(f"\n  Files saved to: data/graph_rag/")
    print(f"    ✓ graph.pkl")
    print(f"    ✓ summaries.json")
    print(f"    ✓ entities.json")
    print(f"    ✓ metadata.json")
    print(f"\n  Your chatbot will now use GraphRAG for relationship questions.")
    print(f"  Restart your Streamlit app to activate GraphRAG.")
    print("="*60)


if __name__ == "__main__":
    main()