"""Final report assembly: validation/repair, survey formatting, citation conversion.

Refactored from the original format_survey.py and done.md scripts.
"""

from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Text cleaning helpers
# ---------------------------------------------------------------------------

_DOT = r'[.\uFF0E\u3002]'


def clean_title(title: str) -> str:
    """Remove numbering prefixes from section titles."""
    title = re.sub(rf'(?i)^\s*section[-\s]*[\d{_DOT}]*\s*', '', title)
    title = re.sub(
        r'^(?:第\s*[0-9一二三四五六七八九十百千]+\s*(?:章|节|部(?:分)?|篇|卷)\s*[-—:：\.、，,]?\s*)+',
        '', title,
    )
    title = re.sub(rf'^\s*\d+(?:{_DOT}\d+)+[\.、，,]?\s*', '', title)
    title = re.sub(r'^\s*\d+[.、，,]\s*', '', title)
    title = re.sub(r'[\(（]\d+[\)）]\s*', '', title)
    title = re.sub(rf'^\s*[一二三四五六七八九十]+(?:{_DOT}\d+)+[\.、，,]?\s*', '', title)
    title = re.sub(r'^\s*[一二三四五六七八九十]+[.、，,]\s*', '', title)
    title = re.sub(r'[\(（][一二三四五六七八九十]+[\)）]\s*', '', title)
    title = re.sub(r'^[一二三四五六七八九十]+\s+', '', title)
    return title.strip()


def clean_content(content: str) -> str:
    """Clean up content text for proper Markdown formatting."""
    if not content:
        return ""
    content = re.sub(r'\bSection-(\d+(?:\.\d+)*)\b', '', content)
    content = re.sub(r'^[ \t]+(#{1,6})\s+', r'\1 ', content, flags=re.MULTILINE)
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = re.sub(r'([^\n])\n(#{1,6}\s)', r'\1\n\n\2', content)
    content = re.sub(r'(#{1,6}\s[^\n]+)\n([^\n#])', r'\1\n\n\2', content)
    return content.strip()


# ---------------------------------------------------------------------------
# Citation conversion
# ---------------------------------------------------------------------------

def _build_url_merge_map(registry: Dict[str, Any]) -> Dict[int, int]:
    """Collapse textids sharing the same URL to the lowest-numbered canonical."""
    url_to_canonical: Dict[str, int] = {}
    merge: Dict[int, int] = {}
    id_entries: list = []

    for key, val in registry.items():
        if not isinstance(val, dict):
            continue
        m = re.match(r"textid(\d+)", key)
        if m:
            id_entries.append((int(m.group(1)), val))
        else:
            tid = val.get("id", "")
            m2 = re.match(r"textid(\d+)", tid if isinstance(tid, str) else "")
            if m2:
                id_entries.append((int(m2.group(1)), val))

    for old_int, val in sorted(id_entries, key=lambda x: x[0]):
        url = val.get("url", "").strip()
        if not url:
            merge[old_int] = old_int
            continue
        if url not in url_to_canonical:
            url_to_canonical[url] = old_int
        merge[old_int] = url_to_canonical[url]

    return merge


def convert_citations(text: str, merge_map: Optional[Dict[int, int]] = None) -> Tuple[str, Dict[int, int]]:
    """Convert [[textidN, textidM]] to sequential [1][2] format.

    Returns (converted_text, id_map) where id_map = {canonical_old_id: new_int_id}.
    """
    if merge_map is None:
        merge_map = {}

    id_map: Dict[int, int] = {}
    _counter = [0]

    def _get_new_id(old_id: int) -> int:
        canonical = merge_map.get(old_id, old_id)
        if canonical not in id_map:
            _counter[0] += 1
            id_map[canonical] = _counter[0]
        return id_map[canonical]

    def _replace(match):
        bibkey_group = match.group(1)
        bibs = [b.strip() for b in bibkey_group.split(",")]
        nums = []
        for bib in bibs:
            bib = bib.replace("bibkey: ", "").replace("bibkey:", "").strip()
            if bib.startswith("textid"):
                try:
                    nums.append(int(bib[len("textid"):]))
                except ValueError:
                    pass
        new_nums = sorted(set(_get_new_id(n) for n in nums))
        return "".join(f"[{n}]" for n in new_nums) if new_nums else ""

    text = re.sub(r"\[\[(.+?)\]\]", _replace, text)
    text = re.sub(r"\\cite\{(.+?)\}", _replace, text)
    return text, id_map


def build_references_section(id_map: Dict[int, int], registry: Dict[str, Any]) -> str:
    """Build a references section with renumbered citation IDs."""
    if not id_map or not registry:
        return ""

    lines = ["\n## 参考文献\n"]
    for old_id, new_id in sorted(id_map.items(), key=lambda x: x[1]):
        key = f"textid{old_id}"
        entry = registry.get(key, {})
        title = entry.get("title", "").strip()
        url = entry.get("url", "").strip()
        if url and title:
            lines.append(f"[{new_id}] {title}. {url}")
        elif url:
            lines.append(f"[{new_id}] {url}")
        elif title:
            lines.append(f"[{new_id}] {title}")
        else:
            lines.append(f"[{new_id}] (source unavailable)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation & repair (done-state)
# ---------------------------------------------------------------------------

def validate_and_repair(work_dir: str | Path) -> Dict[str, int]:
    """Run full-text validation and auto-fix on all content files.

    Returns a summary dict with counts: issues_found, auto_fixed, warnings.
    """
    work_dir = Path(work_dir)
    state_path = work_dir / "state.json"

    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f, strict=False)

    sections = state["survey"]["sections"]
    issues_found = 0
    auto_fixed = 0
    warnings = 0

    for entry in sections:
        pos = entry["position"]
        content_path = work_dir / "content" / f"{pos}.md"
        retrieved_path = work_dir / "retrieved" / f"{pos}.txt"

        if not content_path.exists():
            continue

        with open(content_path, "r", encoding="utf-8") as f:
            content = f.read()
        original_content = content

        # Check 1: Cross-section citation leak
        valid_ids: set = set()
        if retrieved_path.exists():
            with open(retrieved_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ID: "):
                        valid_ids.add(line[4:].strip())

        cited_ids = re.findall(r"\[\[([^\]]+)\]\]", content)
        for group in cited_ids:
            for cid in group.split(","):
                cid = cid.strip()
                if cid and cid not in valid_ids:
                    warnings += 1
                    issues_found += 1

        # Check 2: Duplicate adjacent citations
        new_content = re.sub(r"\[\[(textid\d+)\]\]\[\[\1\]\]", r"[[\1]]", content)
        new_content = re.sub(r"\[\[(textid\d+),\s*\1\]\]", r"[[\1]]", new_content)
        if new_content != content:
            auto_fixed += 1
            issues_found += 1
            content = new_content

        # Check 3: Headers in content
        header_matches = re.findall(r"^#{1,6}\s", content, re.MULTILINE)
        if header_matches:
            content = re.sub(r"^#{1,6}\s+", "", content, flags=re.MULTILINE)
            auto_fixed += len(header_matches)
            issues_found += len(header_matches)

        # Check 4: Empty section
        if len(content.strip()) < 50:
            warnings += 1
            issues_found += 1

        if content != original_content:
            with open(content_path, "w", encoding="utf-8") as f:
                f.write(content)

    # Check 5: Citation metadata repair
    citations_path = work_dir / "citations.json"
    if citations_path.exists():
        with open(citations_path, "r", encoding="utf-8") as f:
            registry = json.load(f, strict=False)

        broken = {
            k: v for k, v in registry.items()
            if isinstance(v, dict) and not v.get("url", "").strip()
        }
        if broken:
            id_to_lines: Dict[str, list] = {}
            for txt_file in sorted(glob.glob(str(work_dir / "retrieved" / "*.txt"))):
                with open(txt_file, "r", encoding="utf-8") as f:
                    raw = f.read()
                for block in raw.split("===PASSAGE==="):
                    block = block.strip()
                    if not block:
                        continue
                    block_id = None
                    body_lines = []
                    for line in block.split("\n"):
                        if line.startswith("ID: "):
                            block_id = line[4:].strip()
                        elif line.startswith("URL: ") or line.startswith("TITLE: "):
                            pass
                        else:
                            body_lines.append(line)
                    if block_id:
                        id_to_lines[block_id] = body_lines

            repaired = 0
            unfixable_ids: set = set()
            for key, val in broken.items():
                cid = val.get("id", "")
                lines = id_to_lines.get(cid, [])
                new_url, new_title = "", ""
                for ln in lines:
                    ln_s = ln.strip()
                    if not ln_s:
                        continue
                    if not new_url and (ln_s.startswith("http://") or ln_s.startswith("https://")):
                        new_url = ln_s
                    elif new_url and not new_title:
                        new_title = ln_s
                        break
                if new_url:
                    val["url"] = new_url
                    if new_title:
                        val["title"] = new_title
                    repaired += 1
                    auto_fixed += 1
                    issues_found += 1
                else:
                    unfixable_ids.add(cid)

            if unfixable_ids:
                keys_to_del = [
                    k for k, v in registry.items()
                    if isinstance(v, dict) and v.get("id", "") in unfixable_ids
                ]
                for k in keys_to_del:
                    del registry[k]

                for md_file in sorted(glob.glob(str(work_dir / "content" / "*.md"))):
                    with open(md_file, "r", encoding="utf-8") as f:
                        text = f.read()
                    original_text = text
                    for uid in unfixable_ids:
                        text = re.sub(r',\s*' + re.escape(uid) + r'(?=\s*[\],])', '', text)
                        text = re.sub(re.escape(uid) + r'\s*,\s*', '', text)
                        text = re.sub(r'\[\[' + re.escape(uid) + r'\]\]', '', text)
                        text = re.sub(r'\[\[\s*\]\]', '', text)
                    if text != original_text:
                        with open(md_file, "w", encoding="utf-8") as f:
                            f.write(text)
                auto_fixed += len(unfixable_ids)
                issues_found += len(unfixable_ids)

            if repaired or unfixable_ids:
                with open(citations_path, "w", encoding="utf-8") as f:
                    json.dump(registry, f, ensure_ascii=False)

    return {"issues_found": issues_found, "auto_fixed": auto_fixed, "warnings": warnings}


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _build_nested(entries: List[Dict], parent_prefix: str = "") -> List[Dict]:
    """Convert flat section list to nested structure for formatting."""
    result = []
    for e in entries:
        pos = e["position"]
        if parent_prefix == "":
            if "." not in pos:
                node = {
                    "title": e["title"],
                    "plan": e.get("plan", ""),
                    "content": e.get("content", ""),
                }
                node["subsections"] = _build_nested(entries, pos)
                result.append(node)
        else:
            if pos.startswith(parent_prefix + ".") and pos[len(parent_prefix) + 1:].count(".") == 0:
                node = {
                    "title": e["title"],
                    "plan": e.get("plan", ""),
                    "content": e.get("content", ""),
                }
                node["subsections"] = _build_nested(entries, pos)
                result.append(node)
    return result


def _format_nested(survey: Dict[str, Any]) -> str:
    """Format a nested survey dict as clean Markdown."""
    if not survey:
        return "No survey generated."

    lines = []
    title = survey.get("title", "Untitled Report")
    lines.append(f"# {title}")
    lines.append("")

    for i, section in enumerate(survey.get("sections", [])):
        title_key = "name" if "name" in section else "title"
        sec_title = clean_title(section.get(title_key, ""))
        sec_num = i + 1

        lines.append(f"## {sec_num} {sec_title}")
        lines.append("")

        if section.get("content"):
            lines.append(clean_content(section["content"]))
            lines.append("")

        for j, sub in enumerate(section.get("subsections", [])):
            sub_title = clean_title(sub.get(title_key, ""))
            lines.append(f"### {sec_num}.{j + 1} {sub_title}")
            lines.append("")

            if sub.get("content"):
                lines.append(clean_content(sub["content"]))
                lines.append("")

            for k, subsub in enumerate(sub.get("subsections", [])):
                subsub_title = clean_title(subsub.get(title_key, ""))
                lines.append(f"#### {sec_num}.{j + 1}.{k + 1} {subsub_title}")
                lines.append("")

                if subsub.get("content"):
                    lines.append(clean_content(subsub["content"]))
                    lines.append("")

    result = "\n".join(lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result


def assemble_report(work_dir: str | Path) -> str:
    """Assemble the final Markdown report from state.json + content/ files.

    Returns the formatted Markdown string.
    """
    work_dir = Path(work_dir)

    with open(work_dir / "state.json", "r", encoding="utf-8") as f:
        state = json.load(f, strict=False)

    flat = state["survey"]

    for entry in flat["sections"]:
        content_path = work_dir / "content" / f"{entry['position']}.md"
        if content_path.exists():
            with open(content_path, "r", encoding="utf-8") as f:
                entry["content"] = f.read()

    nested = {"title": flat["title"], "sections": _build_nested(flat["sections"])}

    # Load citation registry
    citations_path = work_dir / "citations.json"
    if citations_path.exists():
        with open(citations_path, "r", encoding="utf-8") as f:
            citation_registry = json.load(f, strict=False)
    else:
        citation_registry = {}

    ref_map: Dict[str, Dict] = {}
    for key, val in citation_registry.items():
        if isinstance(val, dict):
            ref_map[val.get("id", key)] = {
                "url": val.get("url", ""),
                "title": val.get("title", ""),
            }

    result = _format_nested(nested)

    merge_map = _build_url_merge_map(ref_map)
    result, id_map = convert_citations(result, merge_map)
    refs_section = build_references_section(id_map, ref_map)
    if refs_section:
        result = result.strip() + "\n" + refs_section

    return result.strip()


def finalize(work_dir: str | Path) -> str:
    """Run validation/repair, assemble report, write report.md.

    Returns the path to the written report.
    """
    work_dir = Path(work_dir)

    validate_and_repair(work_dir)
    report_md = assemble_report(work_dir)

    report_path = work_dir / "report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    return str(report_path)
