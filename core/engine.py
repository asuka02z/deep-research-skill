"""Main controller for the deep-research state machine.

Provides the agent-assisted interface:
  - init(query)        → create session, return first action
  - next_action()      → return current pending action (idempotent)
  - complete(trigger, data) → process completion, transition, return next action
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .citation import CitationManager
from .prompts import render_template
from .report import finalize as finalize_report
from .states import transition, _first_uncompleted, _pending_positions
from .store import StateStore
from .validation import validate_init_plan, validate_extend_plan


_MAX_SEARCH_BATCH = 4


class Engine:
    """Orchestrates the deep-research workflow via action JSON protocol."""

    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir).resolve()
        self.store = StateStore(self.work_dir)
        self.citation_mgr = CitationManager(self.store)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def init(self, user_query: str) -> Dict[str, Any]:
        """Initialize a new research session. Returns the first action JSON."""
        self.store.initialize(user_query)
        return self.next_action()

    def next_action(self) -> Dict[str, Any]:
        """Return the action JSON for the current state (idempotent)."""
        state = self.store.load()
        return self._build_action(state)

    def complete(self, trigger: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process completion data, transition state, return next action.

        The *data* dict carries results from the agent:
        - analyze_query: {"focus_statement": "..."}
        - init_plan:     {"survey": {...}}
        - extend_plan:   {"action": "expand"|"skip", ...}
        - search:        (no data needed — Python reads retrieved/ files)
        - write:         (no data needed — Python reads state.json + content/)
        """
        state = self.store.load()
        current = state["state"]

        # -- Process completion data per state ----------------------------

        if current == "analyze_query" and data:
            state["focus_statement"] = data.get("focus_statement", "")

        elif current == "init_plan":
            if not data:
                state = transition(state, "parse_failed")
                self.store.save(state)
                return self._build_action(state)
            survey = data.get("survey")
            if not survey and "title" in data and "sections" in data:
                survey = data
            if not survey:
                state = transition(state, "parse_failed")
                self.store.save(state)
                return self._build_action(state)
            ok, msg = validate_init_plan(survey, state.get("user_query", ""))
            if not ok:
                state = transition(state, "parse_failed")
                self.store.save(state)
                return self._build_action(state)
            for s in survey.get("sections", []):
                s.setdefault("completed", False)
            state["survey"] = survey
            trigger = "plan_created"

        elif current == "extend_plan" and data:
            action_type = data.get("action", "skip")
            if action_type == "expand":
                expansion = data
                ok, msg = validate_extend_plan(expansion, state, state.get("user_query", ""))
                if not ok:
                    trigger = "parse_failed"
                else:
                    pos = expansion["position"]
                    new_subs = expansion.get("subsections", [])
                    sections = state["survey"]["sections"]
                    for i, sub in enumerate(new_subs, 1):
                        sections.append({
                            "position": f"{pos}.{i}",
                            "title": sub["title"],
                            "plan": sub["plan"],
                            "completed": False,
                        })
                    parent_summary = expansion.get("parent_summary", "")
                    if parent_summary:
                        content_path = self.work_dir / "content" / f"{pos}.md"
                        with open(content_path, "w", encoding="utf-8") as f:
                            f.write(parent_summary)
                    trigger = "expanded"
            else:
                trigger = "no_expansion"

        elif current == "search":
            positions = self._searched_positions(state)
            state = self.citation_mgr.assign_citations(state, positions)

            if state.get("cursor") == "outline":
                trigger = "outline_searched"
            else:
                remaining = [
                    p for p in _pending_positions(state)
                    if not (self.work_dir / "retrieved" / f"{p}.txt").exists()
                ]
                if remaining:
                    trigger = "batch_remaining"
                else:
                    trigger = "sections_searched"

        elif current == "write":
            state = self.store.load()
            state = self._sync_completed_from_fs(state)
            pending = _pending_positions(state)
            if pending:
                missing_pos = pending[0]
                retrieved_path = self.work_dir / "retrieved" / f"{missing_pos}.txt"
                if not retrieved_path.exists():
                    state["_missing_position"] = missing_pos
                    trigger = "missing_retrieved"
                else:
                    trigger = "partial_complete"
            else:
                trigger = "all_complete"

        # -- Execute transition -------------------------------------------

        state = transition(state, trigger)
        self.store.save(state)

        # -- If new state is done, finalize report ------------------------

        if state["state"] == "done":
            return self._finalize(state)

        return self._build_action(state)

    # ------------------------------------------------------------------
    # Action builders
    # ------------------------------------------------------------------

    def _build_action(self, state: dict) -> Dict[str, Any]:
        """Dispatch to the appropriate action builder for the current state."""
        s = state["state"]

        if s == "done" or state.get("step", 0) >= 140:
            return self._finalize(state)

        builder = {
            "analyze_query": self._action_analyze_query,
            "search": self._action_search,
            "init_plan": self._action_init_plan,
            "write": self._action_write,
            "extend_plan": self._action_extend_plan,
        }.get(s)

        if builder is None:
            return {"done": True, "state": s, "action": "finalize", "error": f"unknown state: {s}"}

        return builder(state)

    def _action_analyze_query(self, state: dict) -> Dict[str, Any]:
        prompt = render_template(
            "analyze_query.txt",
            user_query=state["user_query"],
        )
        return {
            "done": False,
            "state": "analyze_query",
            "step": state["step"],
            "action": "direct",
            "prompt": prompt,
            "on_complete": {
                "trigger": "focus_extracted",
                "data_spec": {"focus_statement": "<extracted focus statement text>"},
            },
        }

    def _action_search(self, state: dict) -> Dict[str, Any]:
        cursor = state.get("cursor", "outline")

        if cursor == "outline":
            positions = ["outline"]
        else:
            positions = [
                e["position"]
                for e in state.get("survey", {}).get("sections", [])
                if not e.get("completed", False)
                   and not (self.work_dir / "retrieved" / f"{e['position']}.txt").exists()
            ]
            positions = positions[:_MAX_SEARCH_BATCH]

        if not positions:
            return {
                "done": True, "state": "search", "action": "finalize",
                "error": "No sections to search — survey is empty or all sections already have retrieved data.",
            }

        tasks = []
        for pos in positions:
            section_context = ""
            if pos != "outline":
                section = self._get_section(state, pos)
                if section:
                    section_context = (
                        f"- Section to search for:\n"
                        f"  - Title: {section.get('title', '')}\n"
                        f"  - Plan: {section.get('plan', '')}\n"
                    )

            prompt = render_template(
                "search.txt",
                work_dir=str(self.work_dir),
                output_file=str(self.work_dir / "retrieved" / f"{pos}.txt"),
                user_query=state["user_query"],
                focus_statement=state.get("focus_statement", ""),
                cursor=pos,
                section_context=section_context,
                outline=self._format_outline(state),
            )
            tasks.append({
                "description": f"deep-research: search {pos}",
                "prompt": prompt,
            })

        trigger = "outline_searched" if cursor == "outline" else "sections_searched"

        return {
            "done": False,
            "state": "search",
            "step": state["step"],
            "action": "subagent",
            "parallel": True,
            "tasks": tasks,
            "on_complete": {
                "trigger": trigger,
            },
        }

    def _action_init_plan(self, state: dict) -> Dict[str, Any]:
        retrieved_info = self._read_retrieved("outline")

        prompt = render_template(
            "init_plan.txt",
            user_query=state["user_query"],
            focus_statement=state.get("focus_statement", ""),
            retrieved_info=retrieved_info,
        )
        return {
            "done": False,
            "state": "init_plan",
            "step": state["step"],
            "action": "direct",
            "prompt": prompt,
            "on_complete": {
                "trigger": "plan_created",
                "data_spec": {"survey": "<survey JSON object>"},
            },
        }

    def _action_write(self, state: dict) -> Dict[str, Any]:
        if not state.get("survey", {}).get("sections"):
            return {
                "done": True, "state": "write", "action": "finalize",
                "error": "No sections to write — survey is empty. Check init_plan stage.",
            }

        pending = [
            e for e in state.get("survey", {}).get("sections", [])
            if not e.get("completed", False)
        ]

        pending_lines = []
        for e in pending:
            pending_lines.append(
                f"- Position: {e['position']} | Title: {e['title']} "
                f"| Plan: {e['plan']} | Retrieved: retrieved/{e['position']}.txt"
            )

        prompt = render_template(
            "write.txt",
            work_dir=str(self.work_dir),
            user_query=state["user_query"],
            focus_statement=state.get("focus_statement", ""),
            step=state["step"],
            pending_sections="\n".join(pending_lines),
            outline=self._format_outline(state),
        )
        return {
            "done": False,
            "state": "write",
            "step": state["step"],
            "action": "subagent",
            "parallel": False,
            "tasks": [{
                "description": "deep-research: write all sections",
                "prompt": prompt,
            }],
            "on_complete": {
                "trigger": "all_complete",
            },
        }

    def _action_extend_plan(self, state: dict) -> Dict[str, Any]:
        prompt = render_template(
            "extend_plan.txt",
            user_query=state["user_query"],
            focus_statement=state.get("focus_statement", ""),
            outline=self._format_outline(state, include_status=True),
        )
        return {
            "done": False,
            "state": "extend_plan",
            "step": state["step"],
            "action": "direct",
            "prompt": prompt,
            "on_complete": {
                "trigger": "expanded",
                "data_spec": {
                    "action": "expand|skip",
                    "position": "<position to expand>",
                    "parent_summary": "<3-5 sentence overview>",
                    "subsections": [{"title": "...", "plan": "..."}],
                },
            },
        }

    def _finalize(self, state: dict) -> Dict[str, Any]:
        """Run validation/repair and assemble report."""
        if state["state"] != "done":
            state["state"] = "done"
            self.store.save(state)

        report_path = finalize_report(self.work_dir)

        survey = state.get("survey", {})
        sections = survey.get("sections", [])

        return {
            "done": True,
            "state": "done",
            "step": state["step"],
            "action": "finalize",
            "report_path": report_path,
            "summary": {
                "total_sections": len(sections),
                "total_steps": state["step"],
                "total_citations": state.get("citation_counter", 0),
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sync_completed_from_fs(self, state: dict) -> dict:
        """Mark sections as completed if their content file exists on disk.

        This provides crash recovery: if the write subagent wrote content
        files but crashed before updating state.json, we detect the written
        files and reconcile the state accordingly.
        """
        for entry in state.get("survey", {}).get("sections", []):
            if not entry.get("completed", False):
                content_file = self.work_dir / "content" / f"{entry['position']}.md"
                if content_file.exists() and content_file.stat().st_size > 0:
                    entry["completed"] = True
        return state

    def _get_section(self, state: dict, position: str) -> Optional[Dict]:
        """Find a section by position in the survey."""
        for e in state.get("survey", {}).get("sections", []):
            if e["position"] == position:
                return e
        return None

    def _format_outline(self, state: dict, include_status: bool = False) -> str:
        """Format the current survey as a text outline."""
        survey = state.get("survey", {})
        sections = survey.get("sections", [])
        if not sections:
            return "No outline yet"

        lines = []
        for e in sections:
            status = ""
            if include_status:
                status = " [completed]" if e.get("completed") else " [pending]"
            lines.append(f"{e['position']}. {e['title']}{status}")
        return "\n".join(lines)

    def _read_retrieved(self, position: str) -> str:
        """Read a retrieved/ file, return content or empty string."""
        path = self.work_dir / "retrieved" / f"{position}.txt"
        if not path.exists():
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _searched_positions(self, state: dict) -> List[str]:
        """Determine which positions were just searched (have retrieved/ files)."""
        cursor = state.get("cursor", "outline")
        if cursor == "outline":
            return ["outline"]

        positions = []
        for e in state.get("survey", {}).get("sections", []):
            if not e.get("completed", False):
                txt = self.work_dir / "retrieved" / f"{e['position']}.txt"
                if txt.exists():
                    positions.append(e["position"])
        return positions
