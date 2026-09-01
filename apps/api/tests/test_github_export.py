"""Unit tests for the GitHub PR description preview — see
app/services/github_export.py. Pure-function tests, same style as
jira_export.py's own tests: no DB, no HTTP, just asserting the generated
shape.
"""

from app.services.github_export import build_github_pr_preview


def test_build_github_pr_preview_includes_title_with_version():
    preview = build_github_pr_preview(
        artifact_title="User Login", version_number=3, content_markdown="Added the login endpoint."
    )

    assert preview.suggested_title == "User Login (v3)"


def test_build_github_pr_preview_embeds_content_under_a_summary_heading():
    preview = build_github_pr_preview(
        artifact_title="User Login", version_number=1, content_markdown="Added the login endpoint."
    )

    assert "## Summary" in preview.description_markdown
    assert "Added the login endpoint." in preview.description_markdown


def test_build_github_pr_preview_includes_a_checklist():
    preview = build_github_pr_preview(artifact_title="User Login", version_number=1, content_markdown="Change.")

    assert len(preview.checklist) > 0
    assert "## Checklist" in preview.description_markdown
    for item in preview.checklist:
        assert f"- [ ] {item}" in preview.description_markdown


def test_build_github_pr_preview_never_calls_out(monkeypatch):
    """No real GitHub connection exists — this must be pure, local
    rendering. Fail loudly if anything ever tries to reach the network."""
    import socket

    def _forbidden(*args, **kwargs):
        raise AssertionError("build_github_pr_preview must not perform network I/O")

    monkeypatch.setattr(socket, "socket", _forbidden)

    build_github_pr_preview(artifact_title="User Login", version_number=1, content_markdown="Change.")
