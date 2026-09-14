"""Repository file edit endpoint — the second deliberate exception to
app/services/github_integration.py's "no push, no branch creation, no
write scope of any kind" read-only contract (see that module's own
docstring, and app/api/routes/repo_bootstrap.py's docstring for the
first exception, repository bootstrapping). Separate router, on purpose,
so github_integration.py's own claim stays literally true and this write
path stays easy to find and audit on its own — same reasoning
repo_bootstrap.py already gives for itself.

WHY THIS EXISTS: several real failure modes only ever surface once a
human is looking at an actual file — a scaffold-generated package.json
missing a "test" script that fails a story's Code Runner run, a typo in
a config value, a one-line fix a reviewer spots while reading a PR. Every
other write path in this codebase is gated behind an AI-agent-produced,
human-reviewed diff (ImplementationRun, RepoBootstrapService's scaffold).
This is the one path that commits exactly what a human typed, with no
agent step in between — so it leans harder on the same safety rails
every other write path already uses, not fewer:

RULE — "never write to a repository's default branch": identical
invariant to every other write path in this codebase (see
github_integration.py's and repo_bootstrap.py's own docstrings) — this
endpoint always commits to a new or already-existing NON-default branch
and never accepts the default branch as its target, checked explicitly
even though branch_name always differs from base_branch by construction
below (defense in depth, same convention implementation_runs.py's
create_pull_request already uses for its own equivalent check).

RULE — "always open a real pull request": open_pull_request defaults to
True. A caller can skip it (open_pull_request=False, e.g. to stage
several file fixes on the same branch before opening one PR for all of
them), but nothing here ever merges anything — same boundary as every
other PR-creation path in this codebase.

RULE — "audited like every other write": every attempt (success, a
failed commit, or a commit that landed but whose PR creation failed)
records an audit log entry — see app/services/audit.py's own convention,
followed everywhere else in this codebase.
"""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import _get_repository_or_404, _github_error_to_http, decrypt_repository_token
from app.core.database import get_db
from app.models import Project, User
from app.schemas.repository_file_edit import CommitFileEditRequest, CommitFileEditResponse
from app.services import github_integration as github_api
from app.services.audit import record_audit_log
from app.services.github_integration import GitHubIntegrationError
from app.services.permissions import require_can_edit_repository_file

router = APIRouter(prefix="/projects/{project_id}/github", tags=["repository-file-edit"])

# GitHub's "ref already exists" response for POST .../git/refs — reusing an
# already-created branch (e.g. a second fix committed onto the same
# branch before its PR is opened) is a normal, expected case, not a
# failure; anything else still raises.
_REF_ALREADY_EXISTS_STATUS = 422


def _default_branch_name(path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "fix"
    return f"fix/{slug}-{uuid.uuid4().hex[:8]}"


@router.post(
    "/repositories/{repository_id}/commit-file",
    response_model=CommitFileEditResponse,
    status_code=status.HTTP_201_CREATED,
)
def commit_file_edit(
    project_id: uuid.UUID, repository_id: uuid.UUID, payload: CommitFileEditRequest, db: Session = Depends(get_db)
) -> CommitFileEditResponse:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")

    repository = _get_repository_or_404(db, repository_id)
    if repository.project_id != project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Repository {repository_id} does not belong to project {project_id}.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_repository_file(triggered_by)

    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    branch_name = payload.branch_name or _default_branch_name(payload.path)
    # RULE — never write to the default branch (see module docstring).
    # Unreachable by construction via the auto-generated name above, but
    # checked explicitly rather than trusted implicitly, same defense-in-
    # depth convention implementation_runs.py's create_pull_request uses.
    if branch_name == base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Refusing to use the repository's default branch as the edit branch.")

    token = decrypt_repository_token(repository)
    commit_message = payload.commit_message or f"Fix {payload.path}"

    try:
        try:
            github_api.create_branch(token, repository.owner, repository.name, new_branch=branch_name, base_ref=base_branch)
        except GitHubIntegrationError as exc:
            if exc.status_code != _REF_ALREADY_EXISTS_STATUS:
                raise
        existing_sha = github_api.get_file_sha(token, repository.owner, repository.name, payload.path, branch_name)
        commit_sha = github_api.create_or_update_file(
            token, repository.owner, repository.name, payload.path,
            content=payload.content, message=commit_message, branch=branch_name, sha=existing_sha,
        )
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="repository.file_commit_failed",
            entity_type="Repository", entity_id=repository.id,
            extra_data={"path": payload.path, "branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise _github_error_to_http(exc) from exc

    pull_request_url: str | None = None
    if payload.open_pull_request:
        try:
            pr = github_api.create_pull_request(
                token, repository.owner, repository.name,
                title=(payload.pr_title or f"Fix: {payload.path}")[:255],
                head=branch_name, base=base_branch,
                body=payload.pr_body or (
                    f"Manual fix to `{payload.path}`, committed directly through Agentic SDLC Hub's repository "
                    f"file editor.\n\nCommit message: {commit_message}\n\n"
                    "_No AI agent generated this change — a human edited and committed it directly._"
                ),
            )
            pull_request_url = pr.html_url
        except GitHubIntegrationError as exc:
            # SCOPE: the commit above already landed on branch_name — no
            # rollback (same convention implementation_runs.py's
            # create_pull_request already follows for this exact
            # situation). Recorded so a human can open the PR manually.
            record_audit_log(
                db, project_id=project.id, actor_user_id=triggered_by.id, action="repository.file_commit_pr_failed",
                entity_type="Repository", entity_id=repository.id,
                extra_data={"path": payload.path, "branch_name": branch_name, "commit_sha": commit_sha, "error": str(exc)},
            )
            db.commit()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"File was committed to {branch_name}, but pull request creation failed: {exc}"
            ) from exc

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="repository.file_committed",
        entity_type="Repository", entity_id=repository.id,
        extra_data={
            "path": payload.path, "branch_name": branch_name, "base_branch": base_branch,
            "commit_sha": commit_sha, "pull_request_url": pull_request_url,
        },
    )
    db.commit()

    return CommitFileEditResponse(branch_name=branch_name, base_branch=base_branch, commit_sha=commit_sha, pull_request_url=pull_request_url)
