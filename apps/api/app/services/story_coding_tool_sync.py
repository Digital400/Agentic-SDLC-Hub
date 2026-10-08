"""The per-story counterpart to app/services/stage_document_sync.py, for the
three per-story delivery lane stages that are plain documents a coding tool
can draft (Story LLD, Implementation Plan, Test Scenarios — see
app/services/coding_tool_skills.py's StageSpec.story_scoped).

Why a separate module: those three stages have no WorkflowNode/Artifact row
at all — their real home is StoryDeliveryNode/StoryArtifact (see
app/models/story_delivery_node.py), a different table scoped by story_id,
not project_id. The generic skill files (the slash command, the reviewer
subagent, the validator script) are still installed exactly once per repo,
generically, via the existing /skills/install route — nothing here
duplicates that. This module only covers the two things that ARE genuinely
per-story:

  1. build_story_input_snapshot — commits a read-only snapshot of one
     story's own context plus whatever upstream StoryArtifact(s)/the
     project's HLD it needs into docs/sdlc/stories/<slug>/inputs/, so the
     generic command has something real to read for that one story.
  2. sync_story_stage_document — pulls the finished
     docs/sdlc/stories/<slug>/<stage>.md back in as a new StoryArtifact
     version, and advances the StoryDeliveryNode exactly the same way its
     own in-app agent module (story_lld_agent.py etc.) does.

Implementation, PR Review Agent, Human Code Review, Testing and QA Approval
are deliberately NOT covered — they are the existing bespoke
ImplementationTask/PullRequestLink/PRReviewRun/TestRun systems tied to a
real GitHub PR, not a document this app pulls back as a draft (same
boundary app/services/coding_tool_skills.py's module docstring already
discloses for the project-level stages).
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    Project,
    Story,
    StoryArtifact,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    User,
)
from app.services.ai_generation import CLARIFICATION_MARKER
from app.services.coding_tool_skills import SDLC_DIR, STAGE_SPECS, SkillFile, StageSpec, parse_front_matter
from app.services.story_delivery import advance_lane
from app.services.story_implementation_plan_agent import (
    STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE,
    STORY_IMPLEMENTATION_PLAN_SECTIONS,
)
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE, STORY_LLD_SECTIONS, get_approved_hld_or_none
from app.services.story_test_scenarios_agent import STORY_TEST_SCENARIOS_ARTIFACT_TYPE, STORY_TEST_SCENARIOS_SECTIONS

# stage key (as used in STAGE_SPECS / this story lane's own agent modules) -> the
# StoryDeliveryNode.node_key it corresponds to (see STORY_DELIVERY_NODE_KEYS).
_LANE_NODE_KEY = {
    "story_lld": "STORY_LLD",
    "story_implementation_plan": "IMPLEMENTATION_PLAN",
    "story_test_scenarios": "TEST_SCENARIOS",
}
_ARTIFACT_TYPE = {
    "story_lld": STORY_LLD_ARTIFACT_TYPE,
    "story_implementation_plan": STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE,
    "story_test_scenarios": STORY_TEST_SCENARIOS_ARTIFACT_TYPE,
}
_SECTIONS = {
    "story_lld": STORY_LLD_SECTIONS,
    "story_implementation_plan": STORY_IMPLEMENTATION_PLAN_SECTIONS,
    "story_test_scenarios": STORY_TEST_SCENARIOS_SECTIONS,
}
# Only Story LLD auto-completes its node on a successful draft — see
# story_lld_agent.py's tail. Implementation Plan and Test Scenarios
# deliberately leave completion to an explicit human action
# (PATCH /delivery-lane-nodes/{id}); sync mirrors that exactly so a synced
# document behaves identically to one drafted in the app.
_AUTO_COMPLETE = {"story_lld": True, "story_implementation_plan": False, "story_test_scenarios": False}


class StoryCodingToolError(Exception):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _story_scoped_spec(stage: str) -> StageSpec:
    spec = STAGE_SPECS.get(stage)
    if spec is None or not spec.story_scoped:
        raise StoryCodingToolError(f"'{stage}' is not a per-story coding-tool stage.", status_code=400)
    return spec


def story_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "story"


def story_output_path(spec: StageSpec, slug: str) -> str:
    """Resolves the `<story-slug>` placeholder in a story-scoped StageSpec's
    templated output_path into a real path for one concrete story."""
    return spec.output_path.replace("<story-slug>", slug)


def _node_for_stage(story: Story, stage: str) -> StoryDeliveryNode:
    lane = story.delivery_lane
    if lane is None:
        raise StoryCodingToolError(f"Story '{story.title}' has no delivery lane yet — create one first.", status_code=409)
    node_key = _LANE_NODE_KEY[stage]
    node = next((n for n in lane.nodes if n.node_key == node_key), None)
    if node is None:
        raise StoryCodingToolError(f"This story's delivery lane has no {node_key} stage.", status_code=409)
    return node


def _latest_story_artifact_content(db: Session, story: Story, artifact_type: str) -> str | None:
    row = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == artifact_type)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    return row.content_markdown if row else None


def _latest_approved_hld(db: Session, project: Project) -> tuple[str, str | None]:
    """(display name, content) — reuses story_lld_agent.py's own lookup
    (handles either the default template's "hld" or the existing-project-
    feature template's "hld_delta") rather than duplicating it; see that
    module's BUG FIX note for why a second, independent copy of this
    lookup is exactly how it drifted and broke for one template before."""
    name, artifact = get_approved_hld_or_none(db, project)
    return name, (artifact.current_version.content_markdown if artifact and artifact.current_version else None)


def _snapshot_file(path: str, title: str, content: str | None) -> tuple[SkillFile, bool]:
    """Returns (file, is_ready). is_ready is False when `content` is None —
    the caller surfaces that explicitly (see StorySnapshot.not_ready) rather
    than only inside the committed placeholder's own text, so a human sees
    it in the app without first having to open the coding tool and hit the
    same wall this file was built to avoid."""
    if content is not None:
        body = (
            f"---\nsource: {title}\nstatus: approved\n---\n# {title} (approved, read-only snapshot)\n\n"
            f"_Re-run “Sync story inputs” in the app to refresh this if {title} changes._\n\n---\n\n{content.strip()}\n"
        )
        purpose = f"Read-only snapshot of the approved {title} — required input for this stage."
        return SkillFile(path, body, purpose), True
    body = (
        f"---\nsource: {title}\nstatus: not_yet_available\n---\n# {title} — not yet available\n\n"
        f"**{title}** has not been drafted/approved yet. Complete it first — in the app, or via its own "
        'coding-tool skill if one exists — then re-run "Sync story inputs" for this story.\n'
    )
    purpose = f"Placeholder — {title} is not available yet."
    return SkillFile(path, body, purpose), False


def _story_context_file(story: Story, slug: str) -> SkillFile:
    lines = [
        "---", "source: story_context", f"story_id: {story.id}", f"story_slug: {slug}", f"story_title: {story.title}", "---",
        f"# Story: {story.title}", "",
        f"**User Story:** {story.user_story.strip() or '—'}",
        f"**Business Value:** {story.business_value.strip() or '—'}",
        "**Acceptance Criteria:**",
        *([f"- {c}" for c in story.acceptance_criteria] or ["- (none stated)"]),
        f"**Priority:** {story.priority.strip() or '—'}",
        f"**Dependencies:** {story.dependencies.strip() or 'None.'}",
        "",
        "_Re-run “Sync story inputs” in the app to refresh this if the story's own fields change._",
    ]
    return SkillFile(f"{SDLC_DIR}/stories/{slug}/inputs/story-context.md", "\n".join(lines) + "\n", "This story's own crafted context.")


@dataclass
class StoryInputSnapshot:
    files: list[SkillFile]
    # Display names of upstream documents that aren't ready yet (placeholder
    # content was written for them) — e.g. ["Story LLD"]. Empty means every
    # input this stage needs is actually available right now.
    not_ready: list[str]


def build_story_input_snapshot(db: Session, *, project: Project, story: Story, stage: str) -> StoryInputSnapshot:
    """Every file "Sync story inputs" commits under
    docs/sdlc/stories/<slug>/inputs/ for one story + one story-scoped stage —
    that story's own context, always, plus whatever upstream document(s)
    `spec.story_upstream_input_files` names."""
    spec = _story_scoped_spec(stage)
    slug = story_slug(story.title)
    prefix = f"{SDLC_DIR}/stories/{slug}/inputs/"
    files = [_story_context_file(story, slug)]
    not_ready: list[str] = []
    for filename in spec.story_upstream_input_files:
        if filename == "hld.md":
            title, content = _latest_approved_hld(db, project)
        elif filename == "story-lld.md":
            title, content = "Story LLD", _latest_story_artifact_content(db, story, STORY_LLD_ARTIFACT_TYPE)
        elif filename == "implementation-plan.md":
            title, content = "Implementation Plan", _latest_story_artifact_content(db, story, STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
        else:  # pragma: no cover - defensive; every real spec's filenames are handled above
            raise StoryCodingToolError(f"Unknown story upstream input file '{filename}'.", status_code=500)
        file, is_ready = _snapshot_file(prefix + filename, title, content)
        files.append(file)
        if not is_ready:
            not_ready.append(title)
    return StoryInputSnapshot(files=files, not_ready=not_ready)


@dataclass
class SyncedStoryDocument:
    story_artifact: StoryArtifact
    node_status: str
    created: bool
    generated_by: str | None


def sync_story_stage_document(
    db: Session, *, project: Project, story: Story, user: User, stage: str, markdown: str, source_label: str
) -> SyncedStoryDocument:
    spec = _story_scoped_spec(stage)
    node = _node_for_stage(story, stage)
    if node.status == StoryDeliveryNodeStatus.LOCKED:
        raise StoryCodingToolError(f"{node.name} is still locked for this story — complete the stage before it first.")

    meta, body = parse_front_matter(markdown)
    if meta.get("sdlc_stage") and meta["sdlc_stage"] != stage:
        raise StoryCodingToolError(f"This file is for stage '{meta['sdlc_stage']}', not '{stage}'.")
    if meta.get("project_id") and meta["project_id"] != str(project.id):
        raise StoryCodingToolError("This file was generated for a different project (project_id does not match).")
    if meta.get("story_id") and meta["story_id"] != str(story.id):
        raise StoryCodingToolError("This file was generated for a different story (story_id does not match).")
    body = body.strip()
    if not body:
        raise StoryCodingToolError("The document is empty.", status_code=422)
    if body.startswith(CLARIFICATION_MARKER):
        raise StoryCodingToolError("The document is a clarification request, not a finished draft.", status_code=422)

    headings = [m.group(1).strip().lower() for m in re.finditer(r"^##\s+(.+?)\s*$", body, re.MULTILINE)]
    missing = [h for h in _SECTIONS[stage] if h.lower() not in headings]
    if missing:
        raise StoryCodingToolError(
            "The document is not ready to sync: missing section(s) " + ", ".join(f'"## {m}"' for m in missing) + ".", status_code=422
        )

    artifact_type = _ARTIFACT_TYPE[stage]
    last = db.query(StoryArtifact).filter(StoryArtifact.node_id == node.id).order_by(StoryArtifact.version_number.desc()).first()
    created = last is None
    generated_by = meta.get("generated_by")
    title = f"{node.name} — {story.title}" + (f" (via {generated_by})" if generated_by else "")
    story_artifact = StoryArtifact(
        story_id=story.id,
        lane_id=node.lane_id,
        node_id=node.id,
        artifact_type=artifact_type,
        title=title,
        content_markdown=body,
        version_number=(last.version_number if last else 0) + 1,
        created_by_id=user.id,
    )
    db.add(story_artifact)

    if node.status != StoryDeliveryNodeStatus.IN_PROGRESS:
        node.status = StoryDeliveryNodeStatus.IN_PROGRESS
        node.started_at = node.started_at or datetime.now(timezone.utc)
    if _AUTO_COMPLETE[stage]:
        node.status = StoryDeliveryNodeStatus.COMPLETED
        node.completed_at = datetime.now(timezone.utc)
        advance_lane(db, lane=node.lane, completed_node=node)
    db.flush()

    return SyncedStoryDocument(story_artifact=story_artifact, node_status=node.status.value, created=created, generated_by=generated_by)
