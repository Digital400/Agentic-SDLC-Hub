"""GitHub PR description preview — the "GitHub integration" for the
Implementation stage's `code_change` artifact (and, downstream, the PR
Review stage that gates on it).

No real GitHub connection exists (see app/api/routes/integrations.py —
`connect` is an explicit 501; no MCP tool, no API client, no auth). This
mirrors app/services/jira_export.py's exact pattern: render locally what a
human would paste into the real tool, never call out to it. Given an
artifact's current content, this produces a suggested PR title, a
description formatted the way a GitHub PR description box expects, and a
short review checklist — nothing here reaches github.com.
"""

from dataclasses import dataclass, field

_DEFAULT_CHECKLIST = [
    "Follows the approved Low-Level Design",
    "No unrelated changes bundled in",
    "Tests added or updated for the behavior this change affects",
]


@dataclass
class GithubPrPreview:
    suggested_title: str
    description_markdown: str
    checklist: list[str] = field(default_factory=lambda: list(_DEFAULT_CHECKLIST))


def build_github_pr_preview(
    *, artifact_title: str, version_number: int, content_markdown: str
) -> GithubPrPreview:
    """Pure, local rendering — no DB/HTTP access, same as jira_export.py's
    build_jira_export_preview. `content_markdown` is the code_change
    artifact's own content; it's passed through under a standard PR
    description template rather than re-parsed, since a code-change
    description is already prose, not a structured field set like Story
    Crafting's stories."""
    suggested_title = f"{artifact_title} (v{version_number})"
    description_markdown = (
        f"## Summary\n\n{content_markdown.strip()}\n\n"
        "## Checklist\n\n" + "\n".join(f"- [ ] {item}" for item in _DEFAULT_CHECKLIST)
    )
    return GithubPrPreview(
        suggested_title=suggested_title,
        description_markdown=description_markdown,
        checklist=list(_DEFAULT_CHECKLIST),
    )
