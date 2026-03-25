"""Parse and write ===PASSAGE=== delimited text files.

Each passage block looks like:

    ===PASSAGE===
    URL: https://example.com
    TITLE: Short Title
    ID: textid1          (added after citation assignment)
    passage body text...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Passage:
    url: str = ""
    title: str = ""
    text: str = ""
    citation_id: Optional[str] = None


def parse_passages(txt_content: str) -> List[Passage]:
    """Parse ===PASSAGE=== delimited text into a list of Passage objects."""
    blocks = txt_content.split("===PASSAGE===")
    passages: List[Passage] = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        url = ""
        title = ""
        citation_id = None
        text_lines: list[str] = []

        for line in block.split("\n"):
            if line.startswith("URL: "):
                url = line[5:].strip()
            elif line.startswith("TITLE: "):
                title = line[7:].strip()
            elif line.startswith("ID: "):
                citation_id = line[4:].strip()
            else:
                text_lines.append(line)

        passages.append(Passage(
            url=url,
            title=title,
            text="\n".join(text_lines).strip(),
            citation_id=citation_id,
        ))

    return passages


def write_passages(passages: List[Passage], path: str) -> None:
    """Write a list of Passage objects to a file in ===PASSAGE=== format."""
    blocks: list[str] = []
    for p in passages:
        lines = [f"===PASSAGE===\nURL: {p.url}\nTITLE: {p.title}"]
        if p.citation_id:
            lines.append(f"ID: {p.citation_id}")
        lines.append(p.text)
        blocks.append("\n".join(lines))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")
