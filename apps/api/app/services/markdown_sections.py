"""Shared Markdown `##`-heading section helpers — mirrors
apps/web/lib/markdown-sections.ts exactly, and must stay in sync with it: a
section title is what round-trips a reviewer's comment
(ReviewComment.section_title) back to a specific part of a regenerated
document, and what app/services/graph_engine.py's
validate_evidence_requirement looks for (e.g. a required "Test Evidence"
section) before an artifact can be approved.

Split out of app/services/revision_agent.py (which still uses these for
its section-scoped merge) so app/services/graph_engine.py can use them too
without a circular import — revision_agent.py itself imports
GraphEngineService.
"""

import re

_HEADING_RE = re.compile(r"^##\s+(.*)$")


def split_into_sections(markdown: str) -> list[dict[str, str]]:
    """Splits `markdown` into one dict per top-level (`##`) heading:
    `{"title": ..., "content": ...}`, in document order. A document with no
    `##` headings at all comes back as a single "Content" section rather
    than losing its text."""
    lines = markdown.split("\n")
    sections: list[dict[str, str]] = []
    current_title = "Content"
    current_lines: list[str] = []
    saw_heading = False

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if content or saw_heading:
            sections.append({"title": current_title, "content": content})

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            flush()
            current_title = match.group(1).strip()
            current_lines = []
            saw_heading = True
        else:
            current_lines.append(line)
    flush()

    if not sections:
        return [{"title": "Content", "content": markdown.strip()}]
    return sections


def join_sections(sections: list[dict[str, str]]) -> str:
    return "\n\n".join(f"## {s['title']}\n\n{s['content']}".rstrip() for s in sections)


def find_section(markdown: str, title: str) -> dict[str, str] | None:
    """Case-insensitive lookup of one section by heading title — used by
    graph_engine.py's validate_evidence_requirement to check a required
    section (e.g. "Test Evidence") actually exists and isn't empty."""
    normalized = title.strip().lower()
    for section in split_into_sections(markdown):
        if section["title"].strip().lower() == normalized:
            return section
    return None
