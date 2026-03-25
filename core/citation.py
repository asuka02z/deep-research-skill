"""Citation assignment and deduplication.

After search subagents write retrieved/{position}.txt, the main engine calls
assign_citations() to give each passage a stable textidN identifier and
maintain the global citation registry in citations.json.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from .passage import Passage, parse_passages, write_passages
from .store import StateStore


class CitationManager:
    """Assign citation IDs to passages and maintain the registry."""

    def __init__(self, store: StateStore):
        self.store = store

    def assign_citations(self, state: dict, positions: List[str]) -> dict:
        """Assign citation IDs for all passages at the given positions.

        Reads retrieved/{position}.txt for each position, deduplicates against
        the existing registry, assigns new textidN IDs, rewrites the files
        with ID: lines, and updates citations.json + state["citation_counter"].

        Returns the mutated state dict.
        """
        registry = self.store.load_citations()
        counter = state.get("citation_counter", 0)

        for position in positions:
            txt_path = self.store.work_dir / "retrieved" / f"{position}.txt"
            if not txt_path.exists():
                continue

            with open(txt_path, "r", encoding="utf-8") as f:
                passages = parse_passages(f.read())

            for p in passages:
                doc_key = p.text.strip()[:100]
                if doc_key in registry:
                    p.citation_id = registry[doc_key]["id"]
                else:
                    counter += 1
                    cid = f"textid{counter}"
                    p.citation_id = cid
                    registry[doc_key] = {
                        "id": cid,
                        "url": p.url,
                        "title": p.title,
                    }

            write_passages(passages, str(txt_path))

        self.store.save_citations(registry)
        state["citation_counter"] = counter
        return state
