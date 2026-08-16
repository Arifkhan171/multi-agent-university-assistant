# parent_doc_store.py
# ─────────────────────────────────────────────────────────────────────────────
# ParentDocStore — stores large parent chunks on disk as JSON.
#
# WHY THIS FILE EXISTS:
# ChromaDB stores small child chunks for precise vector search.
# But we want to give the LLM large parent chunks for rich context.
# This file is the "library" where parent chunks live.
# Children in ChromaDB carry a parent_id → we look up that ID here → get parent.
#
# STORAGE: Simple JSON file at data/parent_chunks.json
# No new database. No Redis. Just a JSON file.
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
from typing import Optional


class ParentDocStore:
    """
    Key-value store for parent chunks.
    Key   = parent_id (string, unique per parent chunk)
    Value = {content: str, metadata: dict}
    
    Saved to disk so parents survive system restarts.
    Loaded into memory at startup for fast lookups.
    """

    def __init__(self, path: str = "data/parent_chunks.json"):
        # Where the JSON file lives on disk
        self.path = path
        
        # In-memory dict for fast lookups at query time
        # Format: { "parent_id": {"content": "...", "metadata": {...}} }
        self.store = {}
        
        # Load existing parents from disk (if KB was already built)
        self._load()

    # ─────────────────────────────────────────────────────────────────────────
    # WRITE OPERATIONS (used during setup_knowledge_base.py)
    # ─────────────────────────────────────────────────────────────────────────

    def add_parent(self, content: str, metadata: dict) -> str:
        """
        Store one parent chunk. Returns the parent_id.
        
        The parent_id is stored in each child's metadata field "parent_id".
        At query time: child retrieved → child.metadata["parent_id"] → look up here.
        
        WHY hash-based ID:
        Same content from same source always gets same ID.
        Running setup twice does not create duplicate parents.
        This is idempotency — same result no matter how many times you run.
        """
        source = metadata.get("source", "unknown")
        
        # Create short hash from content to make ID unique but stable
        content_hash = str(abs(hash(content)))[:8]
        parent_id = f"{source}__{content_hash}"
        
        self.store[parent_id] = {
            "content": content,
            "metadata": metadata
        }
        return parent_id

    def clear(self):
        """
        Delete all parents from memory and disk.
        Called at start of setup_knowledge_base.py before rebuilding KB.
        Ensures no stale parents from old KB build remain.
        """
        self.store = {}
        self._save()
        print(f"[ParentDocStore] Cleared. File: {self.path}")

    def save(self):
        """
        Write all in-memory parents to disk.
        Call this ONCE at the end of setup — not after every parent.
        Saves time by doing one big write instead of 400+ small writes.
        """
        self._save()
        print(f"[ParentDocStore] Saved {len(self.store)} parents to {self.path}")

    # ─────────────────────────────────────────────────────────────────────────
    # READ OPERATIONS (used at query time in knowledge_base.py)
    # ─────────────────────────────────────────────────────────────────────────

    def get_parent(self, parent_id: str) -> Optional[dict]:
        """
        Retrieve one parent chunk by its ID.
        Returns: {"content": "...", "metadata": {...}}
        Returns None if parent_id not found (fallback to child in that case).
        
        This is called at query time — must be fast.
        Reading from in-memory dict = microseconds. No disk access.
        """
        return self.store.get(parent_id)

    # ─────────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _save(self):
        """Write store dict to JSON file on disk."""
        # Create data/ folder if it doesn't exist
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        
        with open(self.path, 'w', encoding='utf-8') as f:
            # ensure_ascii=False preserves Urdu/Pashto characters
            # indent=2 makes JSON human-readable for debugging
            json.dump(self.store, f, ensure_ascii=False, indent=2)

    def _load(self):
        """Load JSON file into memory dict at startup."""
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                self.store = json.load(f)
            print(f"[ParentDocStore] Loaded {len(self.store)} parents from {self.path}")
        else:
            # First time — no file yet, start empty
            self.store = {}

    def __len__(self):
        return len(self.store)

    def __repr__(self):
        return f"ParentDocStore({len(self.store)} parents, path={self.path})"
    
# ADD THIS at the bottom of parent_doc_store.py
# (after the ParentDocStore class ends)

from langchain_text_splitters import RecursiveCharacterTextSplitter

def split_into_parent_child(
    text: str,
    metadata: dict,
    parent_store: ParentDocStore,
    parent_chunk_size: int = 800,
    parent_overlap: int = 50,
    child_chunk_size: int = 200,
    child_overlap: int = 20
) -> list:
    """
    Split one document into parent-child chunks.
    Parents → saved to ParentDocStore (rich context for LLM)
    Children → returned, added to ChromaDB (precise for search)
    """
    from langchain_core.documents import Document

    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=parent_chunk_size,
        chunk_overlap=parent_overlap,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=child_chunk_size,
        chunk_overlap=child_overlap,
        separators=["\n", ". ", ", ", " ", ""]
    )

    all_children = []

    for parent_text in parent_splitter.split_text(text):
        if len(parent_text.strip()) < 50:
            continue

        # Save parent → get ID back
        parent_id = parent_store.add_parent(
            content=parent_text,
            metadata=metadata
        )

        # Create children from this parent
        for child_text in child_splitter.split_text(parent_text):
            if len(child_text.strip()) < 20:
                continue
            all_children.append(Document(
                page_content=child_text,
                metadata={
                    **metadata,
                    "parent_id": parent_id,
                    "chunk_type": "child"
                }
            ))

    return all_children