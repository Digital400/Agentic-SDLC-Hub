"""Jira integration endpoints.

Covers: connect/disconnect a Jira API token, save a project's Jira
project key, preview what would be pushed (Epics/Stories/Implementation
Tasks/Testing bugs, each with validation errors and duplicate detection),
push an explicitly human-selected subset, and sync status back from Jira.

HARD RULE (see app/services/jira_integration.py's module docstring):
no update, delete, transition, or merge-equivalent call exists anywhere
in the Jira client this router uses — only create_issue (a write) and
three reads. `/jira/push` never creates anything beyond exactly what's
named in `selections` — there is no "push everything" endpoint.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import SecretDecryptionError, decrypt_secret, encrypt_secret, last_four
from app.models import (
    Artifact,
    ArtifactStatus,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    JiraIssueLink,
    JiraProjectLink,
    JiraSetupOption,
    JiraSourceType,
    Project,
    ProjectEngineeringSetup,
    Story,
    StoryStatus,
    User,
)
from app.schemas.jira_integration import (
    BulkPreviewStoriesToJiraRequest,
    BulkStoryJiraPreviewResponse,
    BulkStoryJiraSyncResponse,
    BulkSyncStoriesToJiraRequest,
    ConnectJiraRequest,
    CreateJiraProjectLinkRequest,
    JiraConnectionRead,
    JiraIssueLinkRead,
    JiraProjectLinkRead,
    JiraPushItemRead,
    JiraPushPreviewRead,
    JiraPushRequest,
    JiraPushResponse,
    JiraPushResultItem,
    JiraSyncStatusResponse,
    StoryJiraPreviewRead,
    StoryJiraSubtaskPreviewRead,
    StoryJiraSyncResultRead,
    SubtaskJiraSyncResultRead,
    SyncStoryToJiraRequest,
)
from app.services import jira_integration as jira_api
from app.services.audit import record_audit_log
from app.services.jira_integration import JiraIntegrationError
from app.services.jira_push_preview import JiraPushItem, build_jira_push_preview
from app.services.story_export import STORY_BACKLOG_ARTIFACT_TYPE, parse_story_backlog
from app.services.story_jira_sync import (
    StoryJiraPreview,
    StoryJiraSyncResult,
    build_story_jira_preview,
    sync_story_to_jira,
)

router = APIRouter(prefix="/jira", tags=["jira"])

# A Jira status this app treats as "done" for requirement 5's one-directional
# status sync-back — deliberately narrow (see sync_jira_status below).
_DONE_LIKE_JIRA_STATUSES = {"done", "closed", "resolved"}


def _get_connection_or_404(db: Session, connection_id: uuid.UUID) -> IntegrationConnection:
    connection = db.get(IntegrationConnection, connection_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Jira connection {connection_id} not found")
    return connection


def _get_project_or_404(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    return project


def _get_jira_project_link_or_404(db: Session, project_id: uuid.UUID) -> JiraProjectLink:
    link = db.query(JiraProjectLink).filter(JiraProjectLink.project_id == project_id).first()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} has no configured Jira project yet.")
    return link


def _check_jira_engineering_setup_allows_sync(db: Session, project_id: uuid.UUID) -> None:
    """Project Engineering Setup rule 3 — "Jira Sync requires Jira
    config." A project with no ProjectEngineeringSetup row is ungated
    (rule 10); one that explicitly chose SKIP_FOR_NOW during setup is
    blocked with a message pointing at *why*, ahead of
    _get_jira_project_link_or_404's more generic 404 (which a real
    JiraProjectLink might still technically satisfy in some edge case,
    e.g. a setup skipped after a link already existed — SKIP_FOR_NOW
    should still mean "don't sync," not just "wasn't configured yet")."""
    setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project_id).first()
    if setup is not None and setup.jira_config is not None and setup.jira_config.option == JiraSetupOption.SKIP_FOR_NOW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot sync to Jira — this project's engineering setup skipped Jira. Connect a Jira project "
            "(Settings → Integrations → Jira, or update the engineering setup) before syncing.",
        )


def _get_story_or_404(db: Session, story_id: uuid.UUID) -> Story:
    story = db.get(Story, story_id)
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} not found")
    return story


def decrypt_jira_token(connection: IntegrationConnection) -> str:
    """Decrypts the token exactly once, for exactly one outbound Jira
    call — never cached, never logged. Mirrors
    app/api/routes/github_integration.py's decrypt_repository_token."""
    try:
        return decrypt_secret(connection.access_token_encrypted)
    except SecretDecryptionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This Jira connection's stored credential can no longer be decrypted — reconnect Jira."
        ) from exc


def _jira_error_to_http(exc: JiraIntegrationError) -> HTTPException:
    if exc.status_code in (401, 403):
        return HTTPException(status.HTTP_409_CONFLICT, f"Jira rejected the stored credential: {exc}")
    if exc.status_code == 404:
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


def _config(integration: Integration) -> dict:
    return integration.config_json or {}


# 1. Connect a Jira API token ----------------------------------------------------------


@router.post("/connections", response_model=JiraConnectionRead, status_code=status.HTTP_201_CREATED)
def connect_jira(payload: ConnectJiraRequest, db: Session = Depends(get_db)) -> JiraConnectionRead:
    connected_by = db.get(User, payload.connected_by_id)
    if connected_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"connected_by_id {payload.connected_by_id} does not match an existing user")

    try:
        jira_user = jira_api.verify_credentials(payload.base_url, payload.email, payload.api_token)
    except JiraIntegrationError as exc:
        raise _jira_error_to_http(exc) from exc

    integration = db.query(Integration).filter(Integration.provider == IntegrationProvider.JIRA).first()
    if integration is None:
        integration = Integration(integration_name="Jira", provider=IntegrationProvider.JIRA, status=IntegrationStatus.NOT_CONNECTED)
        db.add(integration)
        db.flush()

    integration.config_json = {"base_url": payload.base_url, "email": payload.email}
    integration.status = IntegrationStatus.CONNECTED
    integration.connected_by_id = connected_by.id

    connection = IntegrationConnection(
        integration=integration,
        access_token_encrypted=encrypt_secret(payload.api_token),
        token_last_four=last_four(payload.api_token),
        connected_by=connected_by,
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()

    # SECURITY: extra_data below must never include payload.api_token —
    # only the non-secret facts a real audit trail needs.
    record_audit_log(
        db, actor_user_id=connected_by.id, action="jira.connected", entity_type="IntegrationConnection", entity_id=connection.id,
        extra_data={"email": payload.email, "base_url": payload.base_url, "jira_account_id": jira_user.account_id},
    )

    db.commit()
    db.refresh(connection)
    return JiraConnectionRead.from_orm_connection(connection, base_url=payload.base_url, email=payload.email)


@router.get("/connections", response_model=list[JiraConnectionRead])
def list_jira_connections(db: Session = Depends(get_db)) -> list[JiraConnectionRead]:
    connections = (
        db.query(IntegrationConnection)
        .join(Integration, IntegrationConnection.integration_id == Integration.id)
        .filter(Integration.provider == IntegrationProvider.JIRA)
        .order_by(IntegrationConnection.created_at.desc())
        .all()
    )
    return [JiraConnectionRead.from_orm_connection(c, **_config(c.integration)) for c in connections if _config(c.integration)]


@router.post("/connections/{connection_id}/disconnect", response_model=JiraConnectionRead)
def disconnect_jira(connection_id: uuid.UUID, db: Session = Depends(get_db)) -> JiraConnectionRead:
    connection = _get_connection_or_404(db, connection_id)
    connection.status = IntegrationStatus.NOT_CONNECTED
    connection.integration.status = IntegrationStatus.NOT_CONNECTED
    # Security hygiene: same as GitHub's disconnect — clear the live,
    # decryptable credential rather than just flipping status.
    connection.access_token_encrypted = ""
    connection.token_last_four = ""
    config = _config(connection.integration)

    record_audit_log(
        db, action="jira.disconnected", entity_type="IntegrationConnection", entity_id=connection.id,
        extra_data={"email": config.get("email")},
    )

    db.commit()
    db.refresh(connection)
    return JiraConnectionRead.from_orm_connection(connection, base_url=config.get("base_url", ""), email=config.get("email", ""))


# 2. Save a project's Jira project configuration ---------------------------------------


@router.post("/projects", response_model=JiraProjectLinkRead, status_code=status.HTTP_201_CREATED)
def create_jira_project_link(payload: CreateJiraProjectLinkRequest, db: Session = Depends(get_db)) -> JiraProjectLink:
    project = _get_project_or_404(db, payload.project_id)
    connection = _get_connection_or_404(db, payload.connection_id)
    if connection.status != IntegrationStatus.CONNECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This Jira connection isn't CONNECTED.")

    config = _config(connection.integration)
    token = decrypt_jira_token(connection)
    try:
        jira_project = jira_api.get_project(config.get("base_url", ""), config.get("email", ""), token, payload.jira_project_key)
    except JiraIntegrationError as exc:
        raise _jira_error_to_http(exc) from exc

    link = JiraProjectLink(
        project=project, connection=connection, jira_project_key=jira_project.key, jira_project_name=jira_project.name,
    )
    db.add(link)
    db.flush()

    record_audit_log(
        db, project_id=project.id, action="jira_project.connected", entity_type="JiraProjectLink", entity_id=link.id,
        extra_data={"jira_project_key": jira_project.key, "jira_project_name": jira_project.name},
    )

    db.commit()
    db.refresh(link)
    return link


# 3. Preview (requirements 3, 5) --------------------------------------------------------


def _to_item_read(item: JiraPushItem) -> JiraPushItemRead:
    return JiraPushItemRead(
        source_type=item.source_type, source_key=item.source_key, label=item.label, jira_issue_type=item.jira_issue_type,
        parent_source_key=item.parent_source_key, summary=item.summary, description=item.description,
        validation_errors=item.validation_errors,
        already_linked=JiraIssueLinkRead.model_validate(item.already_linked) if item.already_linked else None,
    )


@router.get("/projects/{project_id}/push-preview", response_model=JiraPushPreviewRead)
def get_jira_push_preview(project_id: uuid.UUID, db: Session = Depends(get_db)) -> JiraPushPreviewRead:
    project = _get_project_or_404(db, project_id)
    jira_project_link = _get_jira_project_link_or_404(db, project_id)

    artifact = (
        db.query(Artifact)
        .filter(Artifact.project_id == project_id, Artifact.artifact_type == STORY_BACKLOG_ARTIFACT_TYPE)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    overall_errors = []
    stories = []
    if artifact is None:
        overall_errors.append(f"Project {project_id} has no {STORY_BACKLOG_ARTIFACT_TYPE} artifact yet.")
    elif artifact.status != ArtifactStatus.APPROVED:
        overall_errors.append(f"Story backlog is not yet APPROVED (current status: {artifact.status.value}).")
    elif artifact.current_version is None:
        overall_errors.append("Story backlog artifact has no version to preview.")
    else:
        stories = parse_story_backlog(artifact.current_version.content_markdown)

    preview = build_jira_push_preview(db, project=project, jira_project_link=jira_project_link, stories=stories)
    preview.overall_errors = overall_errors + preview.overall_errors

    return JiraPushPreviewRead(
        project_id=project_id, jira_project_key=jira_project_link.jira_project_key,
        epics=[_to_item_read(i) for i in preview.epics], stories=[_to_item_read(i) for i in preview.stories],
        implementation_tasks=[_to_item_read(i) for i in preview.implementation_tasks],
        testing_bugs=[_to_item_read(i) for i in preview.testing_bugs], overall_errors=preview.overall_errors,
    )


# 4. Push (requirements 6, 7 — rules: no auto-create, duplicate prevention, validate first) ---


_TYPE_ORDER = [JiraSourceType.EPIC, JiraSourceType.STORY, JiraSourceType.IMPLEMENTATION_TASK, JiraSourceType.TESTING_BUG]


@router.post("/push", response_model=JiraPushResponse)
def push_to_jira(payload: JiraPushRequest, db: Session = Depends(get_db)) -> JiraPushResponse:
    project = _get_project_or_404(db, payload.project_id)
    _check_jira_engineering_setup_allows_sync(db, project.id)
    jira_project_link = _get_jira_project_link_or_404(db, project.id)
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    connection = jira_project_link.connection
    config = _config(connection.integration)
    token = decrypt_jira_token(connection)  # a real write cannot silently degrade to "no token"

    artifact = (
        db.query(Artifact)
        .filter(Artifact.project_id == project.id, Artifact.artifact_type == STORY_BACKLOG_ARTIFACT_TYPE)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    stories = []
    if artifact is not None and artifact.status == ArtifactStatus.APPROVED and artifact.current_version is not None:
        stories = parse_story_backlog(artifact.current_version.content_markdown)

    preview = build_jira_push_preview(db, project=project, jira_project_link=jira_project_link, stories=stories)
    all_items = {
        (i.source_type, i.source_key): i
        for i in preview.epics + preview.stories + preview.implementation_tasks + preview.testing_bugs
    }

    requested = {(s.source_type, s.source_key) for s in payload.selections}
    # Resolve in a fixed type order (Epic -> Story -> Sub-task/Bug) so a
    # Story's parent Epic (or a Sub-task/Bug's parent Story) created
    # earlier in this same request is already linked by the time it's needed.
    ordered_selections = sorted(payload.selections, key=lambda s: _TYPE_ORDER.index(s.source_type))

    results: list[JiraPushResultItem] = []
    newly_created: dict[tuple[JiraSourceType, str], str] = {}  # (type, source_key) -> jira_issue_key, this request only

    for selection in ordered_selections:
        key = (selection.source_type, selection.source_key)
        item = all_items.get(key)
        if item is None:
            results.append(JiraPushResultItem(source_type=selection.source_type, source_key=selection.source_key, status="skipped_invalid", errors=["Item not found in the current preview."]))
            continue
        if item.already_linked is not None:
            results.append(
                JiraPushResultItem(
                    source_type=selection.source_type, source_key=selection.source_key, status="skipped_duplicate",
                    jira_issue_key=item.already_linked.jira_issue_key, jira_issue_url=item.already_linked.jira_issue_url,
                )
            )
            continue
        if not item.is_valid:
            results.append(JiraPushResultItem(source_type=selection.source_type, source_key=selection.source_key, status="skipped_invalid", errors=item.validation_errors))
            continue

        parent_key = None
        if item.parent_source_key:
            parent_type = JiraSourceType.EPIC if item.source_type == JiraSourceType.STORY else JiraSourceType.STORY
            parent_key = newly_created.get((parent_type, item.parent_source_key))
            if parent_key is None:
                parent_link = db.query(JiraIssueLink).filter(
                    JiraIssueLink.jira_project_link_id == jira_project_link.id,
                    JiraIssueLink.source_type == parent_type, JiraIssueLink.source_key == item.parent_source_key,
                ).first()
                parent_key = parent_link.jira_issue_key if parent_link else None

        try:
            issue = jira_api.create_issue(
                config.get("base_url", ""), config.get("email", ""), token,
                project_key=jira_project_link.jira_project_key, issue_type=item.jira_issue_type,
                summary=item.summary, description=item.description, parent_key=parent_key,
            )
        except JiraIntegrationError as exc:
            results.append(JiraPushResultItem(source_type=selection.source_type, source_key=selection.source_key, status="failed", errors=[str(exc)]))
            continue

        link = JiraIssueLink(
            project_id=project.id, jira_project_link=jira_project_link, source_type=item.source_type,
            source_key=item.source_key, source_label=item.label, jira_issue_key=issue.key, jira_issue_type=item.jira_issue_type,
            jira_issue_url=issue.url, parent_jira_issue_key=parent_key, triggered_by_user_id=triggered_by.id,
        )
        db.add(link)
        db.flush()
        newly_created[key] = issue.key

        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="jira_issue.created",
            entity_type="JiraIssueLink", entity_id=link.id,
            extra_data={"source_type": item.source_type.value, "source_key": item.source_key, "jira_issue_key": issue.key, "jira_issue_type": item.jira_issue_type},
        )
        results.append(JiraPushResultItem(source_type=selection.source_type, source_key=selection.source_key, status="created", jira_issue_key=issue.key, jira_issue_url=issue.url))

    db.commit()
    return JiraPushResponse(results=results)


# 5. Sync status (requirement 8; per-story requirement 5) -------------------------------


@router.post("/projects/{project_id}/sync-status", response_model=JiraSyncStatusResponse)
def sync_jira_status(project_id: uuid.UUID, db: Session = Depends(get_db)) -> JiraSyncStatusResponse:
    _get_project_or_404(db, project_id)
    jira_project_link = _get_jira_project_link_or_404(db, project_id)
    connection = jira_project_link.connection
    config = _config(connection.integration)
    token = decrypt_jira_token(connection)

    links = db.query(JiraIssueLink).filter(JiraIssueLink.jira_project_link_id == jira_project_link.id).all()
    for link in links:
        try:
            link.jira_status = jira_api.get_issue_status(config.get("base_url", ""), config.get("email", ""), token, link.jira_issue_key)
            link.last_synced_at = datetime.now(timezone.utc)
        except JiraIntegrationError:
            continue  # one issue's status failing to sync shouldn't block the rest

        # Per-story requirement 5 — "Sync Jira status back to story
        # status". Deliberately narrow and one-directional (a read only):
        # an arbitrary in-progress Jira workflow name (e.g. a board's own
        # "In Review", "Ready for QA", ...) has no honest mapping onto
        # StoryStatus's own lifecycle (PENDING/IN_SPRINT/LANE_ACTIVE/DONE),
        # which tracks lane progress, not a kanban column. Only a
        # "done"-like terminal Jira status ever moves a Story, and only to
        # DONE — never invented custom-status mappings.
        if link.source_type == JiraSourceType.STORY and link.jira_status and link.jira_status.strip().lower() in _DONE_LIKE_JIRA_STATUSES:
            story = (
                db.query(Story)
                .filter(Story.project_id == project_id, Story.title == link.source_key)
                .first()
            )
            if story is not None and story.status != StoryStatus.DONE:
                story.status = StoryStatus.DONE

    db.commit()
    for link in links:
        db.refresh(link)
    return JiraSyncStatusResponse(links=[JiraIssueLinkRead.model_validate(link) for link in links])


# 6. Per-story Jira sync (requirements 1-8 of the per-story spec) -----------------------


def _to_story_preview_read(preview: StoryJiraPreview) -> StoryJiraPreviewRead:
    return StoryJiraPreviewRead(
        story_id=preview.story_id, summary=preview.summary, description=preview.description, priority=preview.priority,
        story_points=preview.story_points, sprint_name=preview.sprint_name, labels=preview.labels,
        subtasks=[
            StoryJiraSubtaskPreviewRead(
                implementation_task_id=uuid.UUID(s.implementation_task_id), title=s.title, description=s.description,
                validation_errors=s.validation_errors,
                already_linked=JiraIssueLinkRead.model_validate(s.already_linked) if s.already_linked else None,
            )
            for s in preview.subtasks
        ],
        validation_errors=preview.validation_errors,
        already_linked=JiraIssueLinkRead.model_validate(preview.already_linked) if preview.already_linked else None,
    )


def _to_story_sync_result_read(story_id: uuid.UUID, result: StoryJiraSyncResult) -> StoryJiraSyncResultRead:
    return StoryJiraSyncResultRead(
        story_id=story_id, status=result.status, jira_issue_key=result.jira_issue_key, jira_issue_url=result.jira_issue_url,
        errors=result.errors,
        subtasks=[
            SubtaskJiraSyncResultRead(
                implementation_task_id=uuid.UUID(s.implementation_task_id), status=s.status,
                jira_issue_key=s.jira_issue_key, jira_issue_url=s.jira_issue_url, errors=s.errors,
            )
            for s in result.subtasks
        ],
    )


@router.get("/stories/{story_id}/preview", response_model=StoryJiraPreviewRead)
def get_story_jira_preview(story_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryJiraPreviewRead:
    """Requirement 2 — "User must preview Jira payload before
    creating/updating Jira issue." Read-only; never calls Jira."""
    story = _get_story_or_404(db, story_id)
    jira_project_link = _get_jira_project_link_or_404(db, story.project_id)
    preview = build_story_jira_preview(db, story=story, jira_project_link=jira_project_link)
    return _to_story_preview_read(preview)


@router.post("/stories/bulk-preview", response_model=BulkStoryJiraPreviewResponse)
def bulk_preview_stories_jira(payload: BulkPreviewStoriesToJiraRequest, db: Session = Depends(get_db)) -> BulkStoryJiraPreviewResponse:
    """Requirement 4 — preview several stories at once, e.g. every
    checkbox-selected story before a bulk-sync confirmation. Read-only;
    never calls Jira. Each story may belong to a different project, so
    each is resolved against its own project's Jira link independently —
    a story whose project has none simply reports that as a validation
    error rather than 404ing the whole batch."""
    previews: list[StoryJiraPreviewRead] = []
    for story_id in payload.story_ids:
        story = _get_story_or_404(db, story_id)
        try:
            jira_project_link = _get_jira_project_link_or_404(db, story.project_id)
        except HTTPException as exc:
            previews.append(
                StoryJiraPreviewRead(
                    story_id=story.id, summary=story.title, description="", priority=None, story_points=story.story_points,
                    sprint_name=None, labels=[], subtasks=[], validation_errors=[str(exc.detail)], already_linked=None,
                )
            )
            continue
        preview = build_story_jira_preview(db, story=story, jira_project_link=jira_project_link)
        previews.append(_to_story_preview_read(preview))
    return BulkStoryJiraPreviewResponse(previews=previews)


@router.post("/stories/{story_id}/sync", response_model=StoryJiraSyncResultRead)
def sync_story_jira(story_id: uuid.UUID, payload: SyncStoryToJiraRequest, db: Session = Depends(get_db)) -> StoryJiraSyncResultRead:
    """Requirements 1/4/6 — syncs exactly this one story (never "sync
    all" — see /stories/bulk-sync for the explicit, human-confirmed bulk
    variant required by the stated Rule)."""
    story = _get_story_or_404(db, story_id)
    jira_project_link = _get_jira_project_link_or_404(db, story.project_id)
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    connection = jira_project_link.connection
    config = _config(connection.integration)
    token = decrypt_jira_token(connection)

    result = sync_story_to_jira(
        db, story=story, jira_project_link=jira_project_link, base_url=config.get("base_url", ""),
        email=config.get("email", ""), token=token, triggered_by=triggered_by,
    )
    db.commit()
    return _to_story_sync_result_read(story.id, result)


@router.post("/stories/bulk-sync", response_model=BulkStoryJiraSyncResponse)
def bulk_sync_stories_jira(payload: BulkSyncStoriesToJiraRequest, db: Session = Depends(get_db)) -> BulkStoryJiraSyncResponse:
    """Rule — "Do not sync all stories automatically unless user chooses
    bulk sync. Bulk sync still needs preview and confirmation." This
    endpoint only ever syncs the exact `story_ids` the caller names —
    there is no "all stories in project" mode. The UI is expected to have
    already shown a preview (via repeated GET .../preview calls) and
    gotten explicit confirmation before calling this."""
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    results: list[StoryJiraSyncResultRead] = []
    for story_id in payload.story_ids:
        story = _get_story_or_404(db, story_id)
        jira_project_link = _get_jira_project_link_or_404(db, story.project_id)
        connection = jira_project_link.connection
        config = _config(connection.integration)
        token = decrypt_jira_token(connection)

        result = sync_story_to_jira(
            db, story=story, jira_project_link=jira_project_link, base_url=config.get("base_url", ""),
            email=config.get("email", ""), token=token, triggered_by=triggered_by,
        )
        results.append(_to_story_sync_result_read(story.id, result))

    db.commit()
    return BulkStoryJiraSyncResponse(results=results)
