"""Persistence layer for state.json and citations.json.

All JSON I/O goes through this module to enforce safety conventions:
- read with strict=False (defend against control characters)
- write with ensure_ascii=False (preserve non-ASCII text)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


_INITIAL_STATE = {
    "user_query": "",
    "state": "search",
    "cursor": "outline",
    "step": 0,
    "extend_time": 0,
    "citation_counter": 0,
    "survey": {},
}


class StateStore:
    """Read/write state.json and citations.json for a research session."""

    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)
        self.state_path = self.work_dir / "state.json"
        self.citations_path = self.work_dir / "citations.json"

    # -- state.json -----------------------------------------------------------

    def initialize(self, user_query: str) -> Dict[str, Any]:
        """Create directories and write a fresh state.json. Returns the state.

        Clears stale artifacts (retrieved/, content/, citations.json) from any
        previous run in the same work_dir to prevent state/filesystem mismatch.
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)

        for subdir in ("content", "retrieved"):
            d = self.work_dir / subdir
            if d.exists():
                for f in d.iterdir():
                    if f.is_file():
                        f.unlink()
            d.mkdir(exist_ok=True)

        state = dict(_INITIAL_STATE)
        state["user_query"] = user_query
        self.save(state)
        self.save_citations({})

        return state

    def load(self) -> Dict[str, Any]:
        """Load state.json. Raises FileNotFoundError if missing."""
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f, strict=False)

    def save(self, state: Dict[str, Any]) -> None:
        """Atomically write state.json."""
        tmp = self.state_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(self.state_path)

    # -- citations.json -------------------------------------------------------

    def load_citations(self) -> Dict[str, Any]:
        """Load citations registry. Returns {} if file is missing."""
        if not self.citations_path.exists():
            return {}
        with open(self.citations_path, "r", encoding="utf-8") as f:
            return json.load(f, strict=False)

    def save_citations(self, registry: Dict[str, Any]) -> None:
        """Atomically write citations.json."""
        tmp = self.citations_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False)
        tmp.replace(self.citations_path)

    def exists(self) -> bool:
        """Check whether state.json exists (i.e. session was initialised)."""
        return self.state_path.exists()
