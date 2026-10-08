"""Repository bootstrap endpoint — the one deliberate exception to
app/services/github_integration.py's "no push, no branch creation, no
write scope of any kind" read-only contract (see that module's own
docstring). Separate router, on purpose, so that file's claim stays
literally true and this one real write path stays easy to find and
audit on its own.

Turns a genuinely empty, newly-connected GitHub repository into a real,
runnable starter project from this project's own Project Engineering
Setup (technology stack) and already-approved documents — see
app/services/repo_bootstrap.py for the full rationale, including why the
first commit must land directly on the repository's default branch
(there is no branch yet to open a PR against).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import _github_error_to_http, decrypt_repository_token
from app.core.database import get_db
from app.models import Project, ProjectEngineeringSetup, Repository, User
from app.schemas.repo_bootstrap import BootstrapCommitRead, BootstrapRepositoryRequest, BootstrapRepositoryResponse
from app.services import github_integration as github_api
from app.services.audit import record_audit_log
from app.services.repo_bootstrap import RepoBootstrapError, generate_scaffold_files, push_scaffold

router = APIRouter(prefix="/projects/{project_id}/github", tags=["repo-bootstrap"])


@router.post("/bootstrap-repository", response_model=BootstrapRepositoryResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_repository(project_id: uuid.UUID, payload: BootstrapRepositoryRequest, db: Session = Depends(get_db)) -> BootstrapRepositoryResponse:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    repository = (
        db.query(Repository)
        .filter(Repository.project_id == project_id, Repository.is_primary.is_(True))
        .first()
    )
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This project has no primary GitHub repository connected yet.")

    setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project_id).first()
    if setup is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot bootstrap — this project has no Project Engineering Setup yet (technology stack is unknown).",
        )

    token = decrypt_repository_token(repository)

    # Independently re-verify emptiness immediately before writing — never
    # trust a caller's earlier scan result, which may be stale (someone
    # else could have pushed real work to this repo since then). An empty
    # repository has no branches at all; GitHub returns 200 + [] for this,
    # not an error, so a non-empty list is the one signal that matters.
    try:
        branches = github_api.list_branches(token, repository.owner, repository.name)
    except github_api.GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    if branches:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Refusing to bootstrap — {repository.owner}/{repository.name} already has {len(branches)} branch(es); "
            "this action only ever runs against a genuinely empty repository.",
        )

    files = generate_scaffold_files(db, project=project, setup=setup)

    try:
        result = push_scaffold(token, repository, files)
    except RepoBootstrapError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="repository.bootstrap_failed",
            entity_type="Repository", entity_id=repository.id,
            extra_data={"error": str(exc), "files_committed": [c.path for c in exc.commits]},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="repository.bootstrapped",
        entity_type="Repository", entity_id=repository.id,
        extra_data={"branch": result.branch, "file_count": len(result.commits), "files": [c.path for c in result.commits]},
    )
    db.commit()

    return BootstrapRepositoryResponse(
        repository_id=repository.id, branch=result.branch,
        commits=[BootstrapCommitRead(path=c.path, commit_sha=c.commit_sha) for c in result.commits],
    )
