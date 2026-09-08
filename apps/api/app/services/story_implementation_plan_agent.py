"""Drafts a Story Implementation Plan — the IMPLEMENTATION_PLAN
story-delivery-lane node's real output, scoped to exactly one story (rule
1/3 — "belongs to one story lane only", "do not include other stories").
Mirrors app/services/story_lld_agent.py's exact shape and reuse pattern
(same transient, never-persisted WorkflowNode trick to call
app/services/ai_generation.py's `generate()` — see that module's
docstring for why this is safe).

RULE 2 — "Do not generate code here": the seeded prompt (see
app/db/seed.py's RICH_DEFAULT_PROMPTS["story_implementation_plan"])
explicitly instructs the agent never to write real code, only to
describe the plan; Code Implementation is IMPLEMENTATION, a separate,
later lane stage.

ACCEPT / REQUEST CHANGES, not auto-complete: unlike STORY_LLD (whose
"review" is the *next* node, LLD_REVIEW), this stage's review is its own
node — drafting here only moves IMPLEMENTATION_PLAN to IN_PROGRESS, it
never completes it. Completion (== acceptance, rule 4 — "implementation
cannot start before this plan is approved or accepted by assigned
user") only happens through app/api/routes/stories.py's
update_lane_node_status, gated the same way as this lane's other
review-style transitions (see that function's IMPLEMENTATION_PLAN
branch).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    AgentDefinition,
    AgentPromptRole,
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
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE

# Add requirement — the new artifact type this module produces.
STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE = "story_implementation_plan"

STORY_IMPLEMENTATION_PLAN_AGENT_KEY = "story-implementation-plan-agent"

# The 12-item output section list — kept here (not just in the seeded
# prompt) so app/db/seed.py's RICH_DEFAULT_PROMPTS can't silently drift
# apart from it independently. Order matches the request exactly.
STORY_IMPLEMENTATION_PLAN_SECTIONS = (
    "Implementation Summary",
    "Files/Folders Likely Affected",
    "Backend Tasks",
    "Frontend Tasks",
    "Database/Migration Tasks",
    "Configuration Changes",
    "Test Tasks",
    "Git Branch Name Suggestion",
    "PR Title Suggestion",
    "Estimated Risk Level",
    "Step-by-Step Coding Plan",
    "Rollback Notes",
)


class StoryImplementationPlanError(Exception):
    """Raised when Story Implementation Plan drafting can't proceed — see
    run_story_implementation_plan_agent's preconditions."""


@dataclass
class StoryImplementationPlanResult:
    agent_run_used_mock: bool
    needs_clarification: bool
    story_artifact: StoryArtifact | None
    node_status: StoryDeliveryNodeStatus


def _get_approved_story_lld(db: Session, story: Story) -> StoryArtifact:
    """Requirement — "after Story LLD approval". Story LLD is
    StoryArtifact-based, not project-level Artifact-based (see
    app/services/story_lld_agent.py) — its "approval" is its lane's own
    LLD_REVIEW node reaching COMPLETED, checked by the caller
    (run_story_implementation_plan_agent) via the node sequence itself;
    this just fetches the latest drafted content to feed the agent."""
    artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_LLD_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    if artifact is None:
        raise StoryImplementationPlanError(f"Story {story.id} has no Story LLD drafted yet.")
    return artifact


def run_story_implementation_plan_agent(
    db: Session, *, node: StoryDeliveryNode, triggered_by: User, clarification_answers: str | None = None
) -> StoryImplementationPlanResult:
    """Preconditions, checked in order:
      1. story delivery lane exists — `node.lane` is always non-null by
         construction, checked explicitly for defense/clarity.
      2. Story LLD is approved — its lane's own LLD_REVIEW node must be
         COMPLETED (the same "review gate IS the parent stage's real
         approval" pattern as every review gate in this lane).
    `node.status == LOCKED` (the sequential-lane default, before its
    predecessor completes) is covered by check 2 automatically, since
    LLD_REVIEW not being COMPLETED is exactly why IMPLEMENTATION_PLAN
    would still be LOCKED.
    """
    lane = node.lane
    if lane is None:
        raise StoryImplementationPlanError(f"Delivery lane node {node.id} has no owning lane.")
    story: Story | None = lane.story
    if story is None:
        raise StoryImplementationPlanError(f"Delivery lane {lane.id} has no owning story.")

    lld_review_node = next((n for n in lane.nodes if n.node_key == "LLD_REVIEW"), None)
    if lld_review_node is None or lld_review_node.status != StoryDeliveryNodeStatus.COMPLETED:
        raise StoryImplementationPlanError(
            f"Story {story.id}'s Story LLD has not been approved yet (LLD_REVIEW must be COMPLETED first)."
        )

    project = db.get(Project, lane.project_id)
    if project is None:
        raise StoryImplementationPlanError(f"Lane {lane.id}'s project no longer exists.")
    story_lld_artifact = _get_approved_story_lld(db, story)
    story_lld_content = story_lld_artifact.content_markdown

    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == STORY_IMPLEMENTATION_PLAN_AGENT_KEY).first()
    if agent is None:
        raise StoryImplementationPlanError(f"No agent is configured for agent_key '{STORY_IMPLEMENTATION_PLAN_AGENT_KEY}'.")
    active_prompt = next((p for p in agent.prompts if p.role == AgentPromptRole.DRAFT and p.is_active), None)
    if active_prompt is None:
        raise StoryImplementationPlanError(f"No active draft prompt configured for agent '{STORY_IMPLEMENTATION_PLAN_AGENT_KEY}'.")

    # A transient, never-persisted WorkflowNode — see module docstring.
    virtual_node = WorkflowNode(
        project_id=project.id,
        node_key="story_implementation_plan",
        name=f"Implementation Plan — {story.title}",
        description=f"Implementation plan scoped to exactly one story: {story.title}.",
        agent_key=STORY_IMPLEMENTATION_PLAN_AGENT_KEY,
        required_inputs=["story_lld"],
        output_artifact_type=STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE,
        requires_human_approval=True,
        allowed_actions=["draft"],
        status=WorkflowStatus.READY,
        context_token_budget=12000,
        output_token_budget=3072,
        full_content_artifact_types=["story_lld"],
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
        approved_artifact_content={"story_lld": story_lld_content},
        approved_artifact_summaries={"story_lld": story_lld_content},
        freeform_context={
            "story_title": story.title,
            "user_story": story.user_story,
            "acceptance_criteria": "; ".join(story.acceptance_criteria) or "None stated.",
            **({"clarification_answers": clarification_answers} if clarification_answers else {}),
        },
        context_token_budget=virtual_node.context_token_budget,
        output_token_budget=virtual_node.output_token_budget,
        full_content_artifact_types={"story_lld"},
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
        artifact_type=STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE,
        title=f"Implementation Plan — {story.title}",
        content_markdown=result.content_markdown,
        version_number=next_version_number,
        created_by_id=triggered_by.id,
    )
    db.add(story_artifact)
    db.flush()

    if result.needs_clarification:
        return StoryImplementationPlanResult(
            agent_run_used_mock=result.used_mock, needs_clarification=True, story_artifact=story_artifact, node_status=node.status
        )

    # Deliberately NOT completing/advancing the node here — see module
    # docstring. Acceptance is its own explicit action
    # (update_lane_node_status), not an automatic side effect of drafting.
    return StoryImplementationPlanResult(
        agent_run_used_mock=result.used_mock, needs_clarification=False, story_artifact=story_artifact,
        node_status=node.status,
    )
