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


def has_real_sections(markdown: str) -> bool:
    """True only if `markdown` has at least one genuine `##` heading.
    `split_into_sections` synthesizes a single "Content" section (a title
    that never actually appears in the source text) when there are none —
    a meaningful distinction for any caller that needs to scope an edit to
    one *real* section: "improve the Content section" of a headingless
    document is not a scoped edit at all, it's the entire document, which
    the section-scoped agents aren't built to safely regenerate from a
    single instruction (see app/services/section_improve_agent.py)."""
    # _HEADING_RE is anchored with ^/$ and matched per-line elsewhere in
    # this module (no re.MULTILINE) — mirror that here rather than
    # .search() the whole string, which would only ever check the first line.
    return any(_HEADING_RE.match(line) for line in markdown.split("\n"))


def find_section(markdown: str, title: str) -> dict[str, str] | None:
    """Case-insensitive lookup of one section by heading title — used by
    graph_engine.py's validate_evidence_requirement to check a required
    section (e.g. "Test Evidence") actually exists and isn't empty."""
    normalized = title.strip().lower()
    for section in split_into_sections(markdown):
        if section["title"].strip().lower() == normalized:
            return section
    return None
