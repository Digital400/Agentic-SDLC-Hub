"""Drafts a Story LLD — the STORY_LLD story-delivery-lane node's real
output, scoped to exactly one story (see app/models/story_delivery_node.py).
Distinct from the project-level `lld` stage (workflows/sdlc-workflow.json),
which designs for a whole story backlog at once; this designs for one
story only, and its output is a StoryArtifact, not a project-level
Artifact/ArtifactVersion.

REUSE, NOT REBUILD: calls the exact same app/services/ai_generation.py
`generate()` every project-level stage uses — same provider fallback
chain, same P0-P5 token-budget prioritization, same two-part
draft/clarification response contract — by constructing a transient,
NEVER-PERSISTED WorkflowNode as `generate()`'s required `node` parameter.
`generate()`/`build_prioritized_context()`/`mock_agent.generate_mock_output()`
only ever read plain string attributes off that parameter (name,
node_key, description, output_artifact_type, budgets,
full_content_artifact_types) — never its identity or FK relationships —
so a transient instance (constructed, used, discarded; never `db.add()`-ed)
is a safe, correct stand-in for a story-scoped stage that has no
corresponding row in the project-level `workflow_nodes` table.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    AgentDefinition,
    AgentPromptRole,
    Artifact,
    ArtifactStatus,
    Project,
    Story,
    StoryArtifact,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    User,
    WorkflowNode,
    WorkflowStatus,
)
from app.services.ai_generation import generate
from app.services.story_delivery import advance_lane

# Requirement 1 — the new artifact type this module produces.
STORY_LLD_ARTIFACT_TYPE = "story_lld"

STORY_LLD_AGENT_KEY = "story-lld-agent"

# Requirement 4's exact output section list — kept here (not just in the
# seeded prompt) so app/db/seed.py's RICH_DEFAULT_PROMPTS and this
# module's own validation, if any is ever added, can't silently drift
# apart independently.
#
# "Test Cases" and "Implementation Tasks" — present in an earlier version
# of this list — were deliberately dropped: they're now their own lane
# stages (TEST_SCENARIOS, IMPLEMENTATION_PLAN — see
# app/services/story_delivery.py), so keeping them here too would just
# duplicate content across two places in the lane.
STORY_LLD_SECTIONS = (
    "Story Summary",
    "Scope",
    "Out of Scope",
    "Related HLD Sections",
    "API Changes",
    "Database Changes",
    "Frontend Changes",
    "Business Rules",
    "Validation Rules",
    "Permission Rules",
    "Error Handling",
    "Logging/Audit Needs",
    "Dependencies",
    "Risks",
    "Developer Notes",
)


class StoryLldError(Exception):
    """Raised when Story LLD drafting can't proceed — see
    run_story_lld_agent's three preconditions (requirement 3)."""


@dataclass
class StoryLldResult:
    agent_run_used_mock: bool
    needs_clarification: bool
    story_artifact: StoryArtifact | None
    node_status: StoryDeliveryNodeStatus


def _get_approved_hld(db: Session, project: Project) -> Artifact:
    """Requirement 3's third precondition — HLD is approved. Project-level,
    same document every story's lane shares (see
    app/services/story_lane_templates.py's retired predecessor for the
    prior art on this exact lookup shape)."""
    hld_node = db.query(WorkflowNode).filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "hld").first()
    if hld_node is None:
        raise StoryLldError(f"Project {project.id}'s workflow has no hld stage.")
    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == hld_node.id, Artifact.status == ArtifactStatus.APPROVED)
        .order_by(Artifact.updated_at.desc())
        .first()
    )
    if artifact is None or artifact.current_version is None:
        raise StoryLldError("The High-Level Design must be approved before Story LLD can start.")
    return artifact


def run_story_lld_agent(db: Session, *, node: StoryDeliveryNode, triggered_by: User) -> StoryLldResult:
    """Requirement 3's three preconditions, checked in order, before
    anything else runs:
      1. story is approved — the story exists and has progressed past
         PENDING (a lane was created for it, which itself already
         required its origin Story Crafting backlog to be approved).
      2. story delivery lane exists — `node.lane` is always non-null by
         construction (a StoryDeliveryNode can't exist without one), but
         checked explicitly for clarity/defense.
      3. HLD is approved — see _get_approved_hld.
    """
    lane = node.lane
    if lane is None:
        raise StoryLldError(f"Delivery lane node {node.id} has no owning lane.")
    story: Story | None = lane.story
    if story is None:
        raise StoryLldError(f"Delivery lane {lane.id} has no owning story.")
    if story.status.value == "PENDING":
        raise StoryLldError(f"Story {story.id} is not yet approved into a delivery lane.")
    if node.status == StoryDeliveryNodeStatus.LOCKED:
        raise StoryLldError(f"Node {node.id} is LOCKED — its predecessor (Story Ready) must complete first.")

    project = db.get(Project, lane.project_id)
    if project is None:
        raise StoryLldError(f"Lane {lane.id}'s project no longer exists.")
    hld_artifact = _get_approved_hld(db, project)
    hld_content = hld_artifact.current_version.content_markdown

    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == STORY_LLD_AGENT_KEY).first()
    if agent is None:
        raise StoryLldError(f"No agent is configured for agent_key '{STORY_LLD_AGENT_KEY}'.")
    active_prompt = next((p for p in agent.prompts if p.role == AgentPromptRole.DRAFT and p.is_active), None)
    if active_prompt is None:
        raise StoryLldError(f"No active draft prompt configured for agent '{STORY_LLD_AGENT_KEY}'.")

    # A transient, never-persisted WorkflowNode — see module docstring.
    virtual_node = WorkflowNode(
        project_id=project.id,
        node_key="story_lld",
        name=f"Story LLD — {story.title}",
        description=f"Low-Level Design scoped to exactly one story: {story.title}.",
        agent_key=STORY_LLD_AGENT_KEY,
        required_inputs=["hld_document"],
        output_artifact_type=STORY_LLD_ARTIFACT_TYPE,
        requires_human_approval=True,
        allowed_actions=["draft"],
        status=WorkflowStatus.READY,
        context_token_budget=12000,
        output_token_budget=3072,
        full_content_artifact_types=["hld_document"],
        order_index=0,
        position_x=0,
        position_y=0,
    )

    if node.status != StoryDeliveryNodeStatus.IN_PROGRESS:
        node.status = StoryDeliveryNodeStatus.IN_PROGRESS
        node.started_at = node.started_at or datetime.now(timezone.utc)

    result = generate(
        project=project,
        node=virtual_node,
        action=AgentPromptRole.DRAFT,
        active_prompt=active_prompt,
        approved_artifact_content={"hld_document": hld_content},
        approved_artifact_summaries={"hld_document": hld_artifact.current_version.agent_context_summary or hld_content},
        freeform_context={
            "story_title": story.title,
            "user_story": story.user_story,
            "business_value": story.business_value,
            "acceptance_criteria": "; ".join(story.acceptance_criteria) or "None stated.",
        },
        context_token_budget=virtual_node.context_token_budget,
        output_token_budget=virtual_node.output_token_budget,
        full_content_artifact_types={"hld_document"},
    )

    # BUG FIX: needs_clarification used to return story_artifact=None here
    # — result.content_markdown already contains the model's actual
    # clarification questions (see ai_generation.py's
    # format_clarification_output), but discarding it meant a human had
    # literally nothing to read: the UI's "see the generated notes"
    # message pointed at notes that were never saved anywhere. Persisting
    # it as a normal StoryArtifact version — same as a real draft — is
    # what lets the UI actually show the questions and, once answered,
    # regenerate against them (same mechanism generic drafting-agent
    # stages already use).
    last_version = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.node_id == node.id)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    next_version_number = (last_version.version_number if last_version else 0) + 1
    story_artifact = StoryArtifact(
        story_id=story.id,
        lane_id=lane.id,
        node_id=node.id,
        artifact_type=STORY_LLD_ARTIFACT_TYPE,
        title=f"Story LLD — {story.title}",
        content_markdown=result.content_markdown,
        version_number=next_version_number,
        created_by_id=triggered_by.id,
    )
    db.add(story_artifact)
    db.flush()

    if result.needs_clarification:
        return StoryLldResult(
            agent_run_used_mock=result.used_mock, needs_clarification=True, story_artifact=story_artifact, node_status=node.status
        )

    # Requirement 6 relies on this: STORY_LLD completing here is what
    # unlocks LLD_REVIEW (a Tech Lead's gate — see
    # app/api/routes/stories.py's update_lane_node_status), which itself
    # is what unlocks IMPLEMENTATION next — sequential, no extra wiring
    # needed beyond app/services/story_delivery.py's existing advance_lane.
    node.status = StoryDeliveryNodeStatus.COMPLETED
    node.completed_at = datetime.now(timezone.utc)
    db.flush()
    advance_lane(db, lane=lane, completed_node=node)

    return StoryLldResult(
        agent_run_used_mock=result.used_mock, needs_clarification=False, story_artifact=story_artifact,
        node_status=node.status,
    )
