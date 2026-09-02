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


def split_into_sections(markdown: str) -> list[dict[str, str | bool]]:
    """Splits `markdown` into one dict per top-level (`##`) heading:
    `{"title": ..., "content": ..., "is_synthetic": ...}`, in document
    order. A document with no `##` headings at all (or text before its
    first one) comes back with that leading text as a "Content" section
    rather than losing it — `is_synthetic=True` marks that this "Content"
    title never actually appeared in the source, so join_sections (below)
    never turns it into a real, literal heading."""
    lines = markdown.split("\n")
    sections: list[dict[str, str | bool]] = []
    current_title = "Content"
    current_lines: list[str] = []
    current_is_synthetic = True

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if content or not current_is_synthetic:
            sections.append({"title": current_title, "content": content, "is_synthetic": current_is_synthetic})

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            flush()
            current_title = match.group(1).strip()
            current_lines = []
            current_is_synthetic = False
        else:
            current_lines.append(line)
    flush()

    if not sections:
        return [{"title": "Content", "content": markdown.strip(), "is_synthetic": True}]
    return sections


def join_sections(sections: list[dict[str, str | bool]]) -> str:
    """Inverse of split_into_sections. A section flaged `is_synthetic`
    (the leading "Content" placeholder — never a key present when the
    section came from anywhere but split_into_sections's own fallback,
    so `.get` defaults False for a hand-built section) is written back as
    plain text, not `## Content` — that heading never existed in the
    original document and must not get invented by a save round-trip."""
    parts = []
    for s in sections:
        if s.get("is_synthetic"):
            parts.append(str(s["content"]).rstrip())
        else:
            parts.append(f"## {s['title']}\n\n{s['content']}".rstrip())
    return "\n\n".join(p for p in parts if p)


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
