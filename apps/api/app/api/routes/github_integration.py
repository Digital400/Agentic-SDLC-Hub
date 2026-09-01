"""GitHub integration endpoints — the read-only foundation.

Covers: connect/disconnect a GitHub PAT, save a project's repo
configuration, and read-only repository actions (branches, default
branch, tree, file content, snapshots). See
app/services/github_integration.py's module docstring for the security
contract every handler here follows — most importantly: the access token
is write-only (accepted once, verified, encrypted, stored) and is never
included in any response, audit log, or error message from this point on.

No push, no branch creation, no write scope of any kind — every handler
below either reads from GitHub or writes only to this app's own database
(the connection/repository/snapshot rows), never to GitHub itself.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import SecretDecryptionError, decrypt_secret, encrypt_secret, last_four
from app.models import (
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Project,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
    User,
)
from app.schemas.github_integration import (
    ConnectGitHubRequest,
    CreateRepositoryRequest,
    CreateSnapshotRequest,
    GitHubRepoSummaryRead,
    IntegrationConnectionRead,
    RepositoryFileContentRead,
    RepositoryFileIndexRead,
    RepositoryRead,
    RepositorySnapshotRead,
    RepositoryTreeRead,
)
from app.services.audit import record_audit_log
from app.services.github_integration import GitHubIntegrationError, get_default_branch as _get_default_branch
from app.services import github_integration as github_api

router = APIRouter(prefix="/github", tags=["github"])


def _get_connection_or_404(db: Session, connection_id: uuid.UUID) -> IntegrationConnection:
    connection = db.get(IntegrationConnection, connection_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"GitHub connection {connection_id} not found")
    return connection


def _get_repository_or_404(db: Session, repository_id: uuid.UUID) -> Repository:
    repo = db.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository {repository_id} not found")
    return repo


def _get_snapshot_or_404(db: Session, snapshot_id: uuid.UUID) -> RepositorySnapshot:
    snapshot = db.get(RepositorySnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository snapshot {snapshot_id} not found")
    return snapshot


def decrypt_repository_token(repo: Repository) -> str:
    """Decrypts the token exactly once, for exactly one outbound GitHub
    call — never cached, never logged. Callers must not hold onto the
    return value beyond the single request/response cycle that needed it."""
    try:
        return decrypt_secret(repo.connection.access_token_encrypted)
    except SecretDecryptionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This repository's stored credential can no longer be decrypted — reconnect GitHub."
        ) from exc


def _github_error_to_http(exc: GitHubIntegrationError) -> HTTPException:
    # 401/403 from GitHub means the stored token is bad/expired/revoked —
    # surfaced as 409 (a real conflict in this app's own state, "your
    # stored connection no longer works"), not a bare 502, so the frontend
    # can prompt to reconnect rather than showing a generic server error.
    if exc.status_code in (401, 403):
        return HTTPException(status.HTTP_409_CONFLICT, f"GitHub rejected the stored credential: {exc}")
    if exc.status_code == 404:
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


# 1. Connect a GitHub PAT ------------------------------------------------------------


@router.post("/connections", response_model=IntegrationConnectionRead, status_code=status.HTTP_201_CREATED)
def connect_github(payload: ConnectGitHubRequest, db: Session = Depends(get_db)) -> IntegrationConnectionRead:
    connected_by = db.get(User, payload.connected_by_id)
    if connected_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"connected_by_id {payload.connected_by_id} does not match an existing user")

    try:
        github_user = github_api.verify_token(payload.access_token)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc

    integration = db.query(Integration).filter(Integration.provider == IntegrationProvider.GITHUB).first()
    if integration is None:
        # Defensive — seed.py already creates this row, but don't assume a
        # seeded database.
        integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.NOT_CONNECTED)
        db.add(integration)
        db.flush()

    connection = IntegrationConnection(
        integration=integration,
        access_token_encrypted=encrypt_secret(payload.access_token),
        token_last_four=last_four(payload.access_token),
        github_username=github_user.login,
        scopes=github_user.scopes,
        connected_by=connected_by,
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    integration.status = IntegrationStatus.CONNECTED
    integration.connected_by_id = connected_by.id
    db.flush()

    # SECURITY: extra_data below must never include payload.access_token —
    # only the non-secret facts a real audit trail needs (who, when, and
    # the GitHub username/scopes the token itself reported).
    record_audit_log(
        db,
        actor_user_id=connected_by.id,
        action="github.connected",
        entity_type="IntegrationConnection",
        entity_id=connection.id,
        extra_data={"github_username": github_user.login, "scopes": github_user.scopes},
    )

    db.commit()
    db.refresh(connection)
    return IntegrationConnectionRead.from_orm_connection(connection)


@router.get("/connections", response_model=list[IntegrationConnectionRead])
def list_github_connections(db: Session = Depends(get_db)) -> list[IntegrationConnectionRead]:
    connections = db.query(IntegrationConnection).order_by(IntegrationConnection.created_at.desc()).all()
    return [IntegrationConnectionRead.from_orm_connection(c) for c in connections]


@router.post("/connections/{connection_id}/disconnect", response_model=IntegrationConnectionRead)
def disconnect_github(connection_id: uuid.UUID, db: Session = Depends(get_db)) -> IntegrationConnectionRead:
    connection = _get_connection_or_404(db, connection_id)
    connection.status = IntegrationStatus.NOT_CONNECTED
    connection.integration.status = IntegrationStatus.NOT_CONNECTED
    # Security hygiene: a disconnected connection shouldn't leave a live,
    # decryptable credential sitting in the database — clear it rather
    # than just flipping status. A repository still pointing at this
    # connection will correctly fail closed (SecretDecryptionError -> 409
    # "reconnect GitHub") the next time it tries to use it.
    connection.access_token_encrypted = ""
    connection.token_last_four = ""

    record_audit_log(
        db,
        action="github.disconnected",
        entity_type="IntegrationConnection",
        entity_id=connection.id,
        extra_data={"github_username": connection.github_username},
    )

    db.commit()
    db.refresh(connection)
    return IntegrationConnectionRead.from_orm_connection(connection)


@router.get("/connections/{connection_id}/repositories", response_model=list[GitHubRepoSummaryRead])
def list_connection_repositories(connection_id: uuid.UUID, db: Session = Depends(get_db)) -> list[GitHubRepoSummaryRead]:
    """Every repo the connected token can see — backs the repo picker in
    the repository-configuration form, so a project's owner/name is
    chosen from what actually exists rather than typed by hand (typos
    there surface as a confusing "GitHub API returned 404" only once you
    try to save)."""
    connection = _get_connection_or_404(db, connection_id)
    if connection.status != IntegrationStatus.CONNECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This GitHub connection isn't CONNECTED.")
    token = decrypt_secret(connection.access_token_encrypted)
    try:
        repos = github_api.list_repositories(token)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    return [
        GitHubRepoSummaryRead(
            owner=r.owner, name=r.name, full_name=r.full_name, default_branch=r.default_branch,
            description=r.description, is_private=r.is_private, html_url=r.html_url,
        )
        for r in repos
    ]


# 2. Save a project's repo configuration ----------------------------------------------


@router.post("/repositories", response_model=RepositoryRead, status_code=status.HTTP_201_CREATED)
def create_repository(payload: CreateRepositoryRequest, db: Session = Depends(get_db)) -> RepositoryRead:
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"project_id {payload.project_id} does not match an existing project")
    connection = _get_connection_or_404(db, payload.connection_id)
    if connection.status != IntegrationStatus.CONNECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This GitHub connection isn't CONNECTED.")

    token = decrypt_secret(connection.access_token_encrypted)
    try:
        github_repo = github_api.get_repository(token, payload.owner, payload.name)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc

    repository = Repository(
        project=project,
        connection=connection,
        owner=payload.owner,
        name=payload.name,
        default_branch=github_repo.default_branch,
        description=github_repo.description,
        html_url=github_repo.html_url,
        is_private=github_repo.is_private,
    )
    db.add(repository)
    db.flush()

    record_audit_log(
        db,
        project_id=project.id,
        action="repository.connected",
        entity_type="Repository",
        entity_id=repository.id,
        extra_data={"owner": payload.owner, "name": payload.name, "default_branch": github_repo.default_branch},
    )

    db.commit()
    db.refresh(repository)
    return RepositoryRead.model_validate(repository)


@router.get("/repositories/{repository_id}", response_model=RepositoryRead)
def get_repository(repository_id: uuid.UUID, db: Session = Depends(get_db)) -> RepositoryRead:
    return RepositoryRead.model_validate(_get_repository_or_404(db, repository_id))


# 3. Read-only repository actions -----------------------------------------------------


@router.get("/repositories/{repository_id}/branches", response_model=list[str])
def list_repository_branches(repository_id: uuid.UUID, db: Session = Depends(get_db)) -> list[str]:
    repo = _get_repository_or_404(db, repository_id)
    token = decrypt_repository_token(repo)
    try:
        return github_api.list_branches(token, repo.owner, repo.name)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc


@router.get("/repositories/{repository_id}/default-branch", response_model=str)
def get_repository_default_branch(repository_id: uuid.UUID, db: Session = Depends(get_db)) -> str:
    """Fetches the LIVE default branch from GitHub (as opposed to
    `Repository.default_branch`, the cached value from connect/snapshot
    time) — the two can drift if the repo's default branch changed since."""
    repo = _get_repository_or_404(db, repository_id)
    token = decrypt_repository_token(repo)
    try:
        return _get_default_branch(token, repo.owner, repo.name)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc


@router.get("/repositories/{repository_id}/tree", response_model=RepositoryTreeRead)
def get_repository_tree(
    repository_id: uuid.UUID, db: Session = Depends(get_db), ref: str | None = Query(default=None)
) -> RepositoryTreeRead:
    repo = _get_repository_or_404(db, repository_id)
    token = decrypt_repository_token(repo)
    resolved_ref = ref or repo.default_branch
    if resolved_ref is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No ref given and this repository has no known default branch.")
    try:
        tree = github_api.get_repository_tree(token, repo.owner, repo.name, resolved_ref)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    return RepositoryTreeRead(
        commit_sha=tree.commit_sha,
        truncated=tree.truncated,
        entries=[{"path": e.path, "entry_type": e.entry_type, "size": e.size, "sha": e.sha} for e in tree.entries],
    )


@router.get("/repositories/{repository_id}/file", response_model=RepositoryFileContentRead)
def read_repository_file(
    repository_id: uuid.UUID,
    db: Session = Depends(get_db),
    path: str = Query(...),
    ref: str | None = Query(default=None),
) -> RepositoryFileContentRead:
    repo = _get_repository_or_404(db, repository_id)
    token = decrypt_repository_token(repo)
    resolved_ref = ref or repo.default_branch
    if resolved_ref is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No ref given and this repository has no known default branch.")
    try:
        file_content = github_api.read_file(token, repo.owner, repo.name, path, resolved_ref)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc
    return RepositoryFileContentRead(
        path=file_content.path, sha=file_content.sha, size=file_content.size,
        content=file_content.content, truncated=file_content.truncated, is_binary=file_content.is_binary,
    )


# 4. Repository snapshots ---------------------------------------------------------------


@router.post("/repositories/{repository_id}/snapshots", response_model=RepositorySnapshotRead, status_code=status.HTTP_201_CREATED)
def create_repository_snapshot(
    repository_id: uuid.UUID, payload: CreateSnapshotRequest, db: Session = Depends(get_db)
) -> RepositorySnapshotRead:
    repo = _get_repository_or_404(db, repository_id)
    triggered_by = db.get(User, payload.triggered_by_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_id {payload.triggered_by_id} does not match an existing user")

    token = decrypt_repository_token(repo)
    resolved_ref = payload.ref or repo.default_branch
    if resolved_ref is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No ref given and this repository has no known default branch.")
    try:
        tree = github_api.get_repository_tree(token, repo.owner, repo.name, resolved_ref)
    except GitHubIntegrationError as exc:
        raise _github_error_to_http(exc) from exc

    snapshot = RepositorySnapshot(
        repository=repo, ref=resolved_ref, commit_sha=tree.commit_sha,
        file_count=len(tree.entries), truncated=tree.truncated, triggered_by=triggered_by,
    )
    db.add(snapshot)
    db.flush()

    for entry in tree.entries:
        db.add(
            RepositoryFileIndex(
                snapshot=snapshot, path=entry.path,
                entry_type=RepositoryFileEntryType(entry.entry_type), size=entry.size, sha=entry.sha,
            )
        )

    # SECURITY: extra_data intentionally carries only metadata — no file
    # contents, no token.
    record_audit_log(
        db,
        project_id=repo.project_id,
        actor_user_id=triggered_by.id,
        action="repository_snapshot.created",
        entity_type="RepositorySnapshot",
        entity_id=snapshot.id,
        extra_data={"ref": resolved_ref, "commit_sha": tree.commit_sha, "file_count": len(tree.entries), "truncated": tree.truncated},
    )

    db.commit()
    db.refresh(snapshot)
    return RepositorySnapshotRead.from_orm_snapshot(snapshot)


@router.get("/repositories/{repository_id}/snapshots", response_model=list[RepositorySnapshotRead])
def list_repository_snapshots(repository_id: uuid.UUID, db: Session = Depends(get_db)) -> list[RepositorySnapshotRead]:
    repo = _get_repository_or_404(db, repository_id)
    snapshots = (
        db.query(RepositorySnapshot)
        .filter(RepositorySnapshot.repository_id == repo.id)
        .order_by(RepositorySnapshot.created_at.desc())
        .all()
    )
    return [RepositorySnapshotRead.from_orm_snapshot(s) for s in snapshots]


@router.get("/snapshots/{snapshot_id}/files", response_model=list[RepositoryFileIndexRead])
def list_snapshot_files(snapshot_id: uuid.UUID, db: Session = Depends(get_db)) -> list[RepositoryFileIndexRead]:
    _get_snapshot_or_404(db, snapshot_id)
    files = db.query(RepositoryFileIndex).filter(RepositoryFileIndex.snapshot_id == snapshot_id).order_by(RepositoryFileIndex.path).all()
    return [RepositoryFileIndexRead.model_validate(f) for f in files]
