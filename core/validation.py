"""Output validation rules for each action type.

Ported from the original deep-research validation-rules.md.
Each validator returns (ok: bool, message: str).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# search output
# ---------------------------------------------------------------------------

def validate_search_keywords(keywords: List[str], user_query: str = "") -> Tuple[bool, str]:
    if not isinstance(keywords, list):
        return False, "keywords must be a list"
    if not (1 <= len(keywords) <= 5):
        return False, f"keywords length must be 1-5, got {len(keywords)}"
    for kw in keywords:
        if not isinstance(kw, str) or not kw.strip():
            return False, f"each keyword must be a non-empty string, got {kw!r}"
    return True, "ok"


# ---------------------------------------------------------------------------
# init_plan output
# ---------------------------------------------------------------------------

def validate_init_plan(survey: Dict[str, Any], user_query: str = "") -> Tuple[bool, str]:
    title = survey.get("title", "")
    if not title:
        return False, "survey title is empty"

    sections = survey.get("sections", [])
    if not sections:
        return False, "sections list is empty"
    if not (3 <= len(sections) <= 12):
        return False, f"section count must be 3-12, got {len(sections)}"

    for s in sections:
        if not isinstance(s, dict):
            return False, f"each section must be a dict, got {type(s)}"
        if not s.get("title", "").strip():
            return False, f"section missing title: {s}"
        if not s.get("plan", "").strip():
            return False, f"section missing plan: {s}"
        pos = s.get("position", "")
        if "." in str(pos):
            return False, f"init_plan must only have top-level positions, got {pos!r}"

    return True, "ok"


# ---------------------------------------------------------------------------
# extend_plan output
# ---------------------------------------------------------------------------

def validate_extend_plan(
    expansion: Dict[str, Any],
    state: Dict[str, Any],
    user_query: str = "",
) -> Tuple[bool, str]:
    """Validate an expansion proposal.

    expansion = {
        "position": "3",
        "subsections": [{"title": "...", "plan": "..."}, ...]
    }
    """
    position = expansion.get("position", "")
    if not position:
        return False, "expansion missing target position"

    survey = state.get("survey", {})
    existing_positions = {e["position"] for e in survey.get("sections", [])}
    if position not in existing_positions:
        return False, f"position {position!r} not in survey"

    if str(position).count(".") >= 2:
        return False, f"max nesting depth exceeded for {position!r} (max 3 levels)"

    has_children = any(
        p.startswith(f"{position}.") for p in existing_positions
    )
    if has_children:
        return False, f"position {position!r} already has subsections"

    subs = expansion.get("subsections", [])
    if not (2 <= len(subs) <= 5):
        return False, f"subsection count must be 2-5, got {len(subs)}"

    for s in subs:
        if not isinstance(s, dict):
            return False, f"each subsection must be a dict"
        if not s.get("title", "").strip():
            return False, "subsection missing title"
        if not s.get("plan", "").strip():
            return False, "subsection missing plan"

    return True, "ok"


# ---------------------------------------------------------------------------
# write output
# ---------------------------------------------------------------------------

def validate_write_content(
    content: str,
    position: str,
    retrieved_ids: Optional[Set[str]] = None,
    user_query: str = "",
) -> Tuple[bool, str]:
    if len(content.strip()) <= 50:
        return False, f"content too short ({len(content.strip())} chars, min 50)"

    if re.search(r"^#{1,6}\s", content, re.MULTILINE):
        return False, "content must not contain # headers"

    if re.search(r"\bbibkey\b", content, re.IGNORECASE):
        return False, "content must not contain literal 'bibkey'"

    citations = re.findall(r"\[\[(.+?)\]\]", content)
    if not citations:
        return False, "content must contain at least one [[textidN]] citation"

    citation_count = len(citations)
    if citation_count > 12:
        return False, f"too many citation groups ({citation_count}, max 12)"

    all_cited: list[str] = []
    for group in citations:
        for cid in group.split(","):
            cid = cid.strip()
            if cid:
                all_cited.append(cid)

    if retrieved_ids is not None:
        for cid in all_cited:
            if cid not in retrieved_ids:
                return False, f"citation {cid!r} not found in retrieved passages"

    dup_pattern = re.search(r"\[\[(textid\d+)\]\]\[\[\1\]\]", content)
    if dup_pattern:
        return False, f"duplicate adjacent citation: {dup_pattern.group(0)}"
    dup_inner = re.search(r"\[\[(textid\d+),\s*\1\]\]", content)
    if dup_inner:
        return False, f"duplicate citation within group: {dup_inner.group(0)}"

    return True, "ok"
