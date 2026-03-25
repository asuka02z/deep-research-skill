"""Prompt template loading and rendering.

Templates live in prompts/*.txt as plain text with {variable} placeholders.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def render_template(name: str, **kwargs: Any) -> str:
    """Load prompts/{name} and substitute {variable} placeholders.

    Uses str.format_map with a default-dict wrapper so that missing keys
    produce "{key}" literally instead of raising KeyError.
    """
    path = _PROMPTS_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        template = f.read()

    class _DefaultDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return template.format_map(_DefaultDict(**kwargs))
