"""Declarative state machine for the deep-research workflow.

All state transitions are deterministic Python logic — no LLM involvement.
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple


class InvalidTransition(Exception):
    """Raised when a (state, trigger) pair has no defined transition."""


def _first_uncompleted(state: dict) -> Optional[str]:
    """Return the position of the first uncompleted section, or None."""
    survey = state.get("survey", {})
    for entry in survey.get("sections", []):
        if not entry.get("completed", False):
            return entry["position"]
    return None


def _pending_positions(state: dict) -> List[str]:
    """Return all positions where completed == false."""
    survey = state.get("survey", {})
    return [
        e["position"]
        for e in survey.get("sections", [])
        if not e.get("completed", False)
    ]


def _has_unsearched_positions(state: dict) -> bool:
    """Check if there are uncompleted sections that still need searching."""
    return bool(_pending_positions(state))


# ---------------------------------------------------------------------------
# Transition table
#
# Key:   (current_state, trigger)
# Value: callable(state_dict) -> (next_state_name, updates_dict)
#
# The updates_dict is merged into state after setting state["state"].
# ---------------------------------------------------------------------------

TransitionFn = Callable[[dict], Tuple[str, dict]]

TRANSITIONS: Dict[Tuple[str, str], TransitionFn] = {
    ("analyze_query", "focus_extracted"):
        lambda s: ("search", {"cursor": "outline"}),

    ("search", "outline_searched"):
        lambda s: ("init_plan", {}),

    ("search", "sections_searched"):
        lambda s: ("write", {"cursor": _first_uncompleted(s)}),

    ("search", "batch_remaining"):
        lambda s: ("search", {"cursor": _first_uncompleted(s)}),

    ("init_plan", "plan_created"):
        lambda s: ("search", {"cursor": _first_uncompleted(s)}),

    ("init_plan", "parse_failed"):
        lambda s: ("init_plan", {}),

    ("write", "all_complete"):
        lambda s: (
            ("extend_plan", {"extend_time": s["extend_time"] + 1})
            if s["extend_time"] < 5
            else ("done", {})
        ),

    ("write", "partial_complete"):
        lambda s: ("write", {"cursor": _first_uncompleted(s)}),

    ("write", "missing_retrieved"):
        lambda s: ("search", {"cursor": s.get("_missing_position", s.get("cursor"))}),

    ("extend_plan", "expanded"):
        lambda s: ("search", {"cursor": _first_uncompleted(s)}),

    ("extend_plan", "no_expansion"):
        lambda s: ("done", {}),

    ("extend_plan", "parse_failed"):
        lambda s: (
            ("extend_plan", {"extend_time": s["extend_time"] + 1})
            if s["extend_time"] < 5
            else ("done", {})
        ),
}


def transition(state: dict, trigger: str) -> dict:
    """Execute a state transition.

    Mutates *state* in-place: sets the new state name, merges updates,
    increments step, and enforces the step ceiling (140 → done).

    Returns the mutated state dict.
    Raises InvalidTransition for undefined (state, trigger) pairs.
    """
    key = (state["state"], trigger)
    if key not in TRANSITIONS:
        raise InvalidTransition(
            f"No transition defined for state={state['state']!r}, trigger={trigger!r}"
        )

    next_state_name, updates = TRANSITIONS[key](state)

    state["state"] = next_state_name
    state["step"] = state.get("step", 0) + 1
    state.update(updates)

    if state["step"] >= 140:
        state["state"] = "done"

    # Clean up transient keys
    state.pop("_missing_position", None)

    return state


def valid_triggers(current_state: str) -> List[str]:
    """Return the list of triggers available from *current_state*."""
    return [trigger for (s, trigger) in TRANSITIONS if s == current_state]
