"""Pulls a stage document written in a developer's own coding tool back into
the app (see app/services/coding_tool_skills.py).

The document arrives as a DRAFT artifact version — never as approved — so the
normal Send-for-review / approval gates still apply. Only a stage that is
unlocked and whose current artifact (if any) is still a DRAFT can be synced
into; anything already submitted for review must be versioned in the app first,
so a stray sync can never overwrite reviewed work.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Artifact, ArtifactStatus, ArtifactVersion, Project, User, WorkflowNode, WorkflowStatus
from app.services.audit import record_audit_log
from app.services.coding_tool_skills import STAGE_SPECS, parse_front_matter
from app.services.graph_engine import GraphEngineService
from app.services.review_time import review_time_problems
from app.services.story_export import parse_story_backlog

CLARIFICATION_MARKER = "# Clarification Needed"


class StageSyncError(Exception):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SyncedDocument:
    artifact: Artifact
    version: ArtifactVersion
    created_artifact: bool
    generated_by: str | None


def _check_document(spec, body: str) -> list[str]:
    problems: list[str] = []
    headings = [m.group(1).strip().lower() for m in re.finditer(r"^##\s+(.+?)\s*$", body, re.MULTILINE)]
    for group in spec.required_headings:
        if not any(g.lower() in headings for g in group):
            problems.append(f'missing section "## {group[0]}"' + (f' (or "## {group[1]}")' if len(group) > 1 else ""))
    if len(body.split()) < spec.min_words:
        problems.append(f"too short (at least {spec.min_words} words)")
    if re.search(r"<write this section>|\bTODO\b|\bTBD\b", body, re.IGNORECASE):
        problems.append("still contains placeholder text")
    return problems


# Story Crafting's document is repeated `## Story: <title>` blocks, not a
# fixed set of `##` sections — reuse the same parser story_export.py already
# uses for the in-app backlog (never re-implement that parsing here), and
# check the same fields the stage's own prompt/validation_checklist require.
_REQUIRED_STORY_FIELDS = (
    ("epic", "Epic"), ("feature", "Feature"), ("mode", "Mode"), ("user_story", "User Story"),
    ("business_value", "Business Value"), ("acceptance_criteria", "Acceptance Criteria"),
    ("suggested_owner_role", "Suggested Owner Role"), ("technical_areas", "Technical Areas Involved"),
    ("priority", "Priority"), ("story_points_estimate", "Story Points Estimate"),
    ("jira_issue_type", "Jira Issue Type"), ("suggested_subtasks", "Suggested Subtasks"),
    ("release_readiness_criteria", "Release Readiness Criteria"), ("definition_of_done", "Definition of Done"),
)


def _check_story_backlog(body: str) -> list[str]:
    stories = parse_story_backlog(body)
    if not stories:
        return ['no "## Story: <title>" blocks found']
    problems: list[str] = []
    for story in stories:
        title = story.title or "(untitled story)"
        for attr, label in _REQUIRED_STORY_FIELDS:
            if not getattr(story, attr, None):
                problems.append(f'"{title}": missing {label}')
        # "One human must be able to review this story's PR" — see
        # app/services/review_time.py, the single place this cap is enforced.
        problems += review_time_problems(title, story.estimated_pr_review_time)
    return problems


def sync_stage_document(
    db: Session, *, project: Project, node: WorkflowNode, user: User, markdown: str, source_label: str, source_path: str
) -> SyncedDocument:
    """`markdown` is the raw file (front matter included)."""
    spec = STAGE_SPECS.get(node.node_key)
    if spec is None:
        raise StageSyncError(f"Stage '{node.node_key}' cannot be synced from a repository yet.", status_code=400)
    if node.status == WorkflowStatus.LOCKED:
        raise StageSyncError(f"{node.name} is still locked — complete the stages before it first.")

    meta, body = parse_front_matter(markdown)
    if meta.get("sdlc_stage") and meta["sdlc_stage"] != node.node_key:
        raise StageSyncError(f"This file is for stage '{meta['sdlc_stage']}', not '{node.node_key}'.")
    if meta.get("project_id") and meta["project_id"] != str(project.id):
        raise StageSyncError("This file was generated for a different project (project_id does not match).")
    body = body.strip()
    if not body:
        raise StageSyncError("The document is empty.", status_code=422)
    if body.startswith(CLARIFICATION_MARKER):
        raise StageSyncError("The document is a clarification request, not a finished draft.", status_code=422)
    problems = _check_story_backlog(body) if spec.kind == "story_backlog" else _check_document(spec, body)
    if problems:
        raise StageSyncError("The document is not ready to sync: " + "; ".join(problems) + ".", status_code=422)

    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == node.id, Artifact.artifact_type == node.output_artifact_type)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    created = artifact is None
    if artifact is None:
        artifact = Artifact(
            project_id=project.id, workflow_node=node, artifact_type=node.output_artifact_type, title=node.name,
            status=ArtifactStatus.DRAFT, created_by=user,
        )
        db.add(artifact)
        db.flush()
        record_audit_log(
            db, project_id=project.id, actor_user_id=user.id, action="artifact.created", entity_type="Artifact",
            entity_id=artifact.id, extra_data={"workflow_node": node.node_key, "artifact_type": artifact.artifact_type, "source": "repository_sync"},
        )
    elif artifact.status != ArtifactStatus.DRAFT:
        raise StageSyncError(
            f"The current {node.name} document is {artifact.status.value.replace('_', ' ').lower()}. Create a new version in the app "
            "first, then sync again — a sync never overwrites reviewed work."
        )

    last = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
        or 0
    )
    generated_by = meta.get("generated_by")
    version = ArtifactVersion(
        artifact=artifact, version_number=last + 1, content_markdown=body, created_by=user,
        change_summary=f"Synced from {source_label} ({source_path})" + (f", written with {generated_by}." if generated_by else "."),
    )
    db.add(version)
    db.flush()
    artifact.current_version = version
    artifact.status = ArtifactStatus.DRAFT
    GraphEngineService(db).mark_ready(node)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=user.id, action="artifact_version.synced_from_repository",
        entity_type="ArtifactVersion", entity_id=version.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": version.version_number, "source": source_label, "path": source_path, "generated_by": generated_by},
    )
    return SyncedDocument(artifact=artifact, version=version, created_artifact=created, generated_by=generated_by)


def new_branch_name(tool: str, stage: str) -> str:
    return f"sdlc/{tool.replace('_', '-')}-{stage.replace('_', '-')}-{uuid.uuid4().hex[:8]}"
