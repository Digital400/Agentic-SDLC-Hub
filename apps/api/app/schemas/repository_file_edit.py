import uuid

from pydantic import BaseModel, Field


class CommitFileEditRequest(BaseModel):
    """Body for POST /projects/{project_id}/github/repositories/{repository_id}/commit-file
    — see app/api/routes/repository_file_edit.py's module docstring for
    the full rationale. `content` replaces the file's entire content (a
    full-file save, same convention every text editor uses — not a
    line-level patch)."""

    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — who made this edit.")
    path: str = Field(..., min_length=1, max_length=1000, description="Repository-relative file path, e.g. 'backend/package.json'.")
    content: str = Field(..., description="The file's full new content.")
    commit_message: str | None = Field(default=None, max_length=500, description="Defaults to 'Fix {path}' when omitted.")
    base_branch: str | None = Field(default=None, description="Branch this edit is based on — defaults to the repository's default branch.")
    branch_name: str | None = Field(
        default=None, description="Branch to commit onto — auto-generated as fix/<slug>-<id> when omitted."
    )
    open_pull_request: bool = Field(default=True, description="Opens a real PR immediately after the commit (recommended — see module docstring).")
    pr_title: str | None = Field(default=None, max_length=255)
    pr_body: str | None = Field(default=None)


class CommitFileEditResponse(BaseModel):
    branch_name: str
    base_branch: str
    commit_sha: str
    pull_request_url: str | None
