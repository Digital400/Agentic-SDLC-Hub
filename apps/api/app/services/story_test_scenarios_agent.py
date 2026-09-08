"""Drafts a Story Test Scenarios document — the TEST_SCENARIOS
story-delivery-lane node's real output, scoped to exactly one story
(rule 1). Mirrors app/services/story_implementation_plan_agent.py's exact
shape and reuse pattern (same transient, never-persisted WorkflowNode
trick to call app/services/ai_generation.py's `generate()`).

RULE 2 — "based on the story, Story LLD, and implementation plan": both
documents are fetched and passed as full context, same as Story
Implementation Plan draws on the approved Story LLD.

RULE 4 — "Testing stage should use these scenarios later": wired into
app/services/testing_agent.py's run_testing_agent (a new, additive
`test_scenarios` parameter) via app/api/routes/test_runs.py's
start_test_run, which fetches the latest approved
STORY_TEST_SCENARIOS StoryArtifact the same way it already fetches the
Story LLD.

ACCEPT / REQUEST CHANGES: same pattern as IMPLEMENTATION_PLAN — drafting
here only moves TEST_SCENARIOS to IN_PROGRESS, never completes it.
Completion (== review/approval, rule 3 — "QA or Tech Lead can
review/approve") happens through
app/api/routes/stories.py's update_lane_node_status, gated to QA/Tech
Lead/Admin.
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
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE

# Add requirement — the new artifact type this module produces.
STORY_TEST_SCENARIOS_ARTIFACT_TYPE = "story_test_scenarios"

STORY_TEST_SCENARIOS_AGENT_KEY = "story-test-scenarios-agent"

# The 10-item output section list — kept here (not just in the seeded
# prompt) so app/db/seed.py's RICH_DEFAULT_PROMPTS can't silently drift
# apart from it independently. Order matches the request exactly.
STORY_TEST_SCENARIOS_SECTIONS = (
    "Acceptance Criteria Mapping",
    "Functional Test Scenarios",
    "Negative Test Scenarios",
    "Edge Cases",
    "Permission/Security Test Scenarios",
    "UI Test Scenarios",
    "API Test Scenarios",
    "Regression Test Areas",
    "Test Data Needed",
    "Expected Results",
)


class StoryTestScenariosError(Exception):
    """Raised when Story Test Scenarios drafting can't proceed — see
    run_story_test_scenarios_agent's preconditions."""


@dataclass
class StoryTestScenariosResult:
    agent_run_used_mock: bool
    needs_clarification: bool
    story_artifact: StoryArtifact | None
    node_status: StoryDeliveryNodeStatus


def _get_story_lld(db: Session, story: Story) -> str:
    artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_LLD_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    return artifact.content_markdown if artifact is not None else ""


def _get_implementation_plan(db: Session, story: Story) -> str:
    artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    return artifact.content_markdown if artifact is not None else ""


def get_latest_story_test_scenarios(db: Session, story_id) -> StoryArtifact | None:
    """Rule 4 helper — the latest STORY_TEST_SCENARIOS StoryArtifact for a
    story, if one exists. Used by app/api/routes/test_runs.py to feed
    Testing with the scenarios it should use."""
    return (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story_id, StoryArtifact.artifact_type == STORY_TEST_SCENARIOS_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )


def run_story_test_scenarios_agent(
    db: Session, *, node: StoryDeliveryNode, triggered_by: User, clarification_answers: str | None = None
) -> StoryTestScenariosResult:
    """Preconditions:
      1. story delivery lane exists — `node.lane` is always non-null by
         construction, checked explicitly for defense/clarity.
      2. Node isn't LOCKED — TEST_SCENARIOS sits after IMPLEMENTATION in
         the default sequence (see app/services/story_delivery.py), so a
         LOCKED node here already means Story LLD/Implementation Plan
         haven't been approved/accepted yet ("before or during
         implementation" — this stage's own position already guarantees
         both are available once it's reachable at all).
    """
    lane = node.lane
    if lane is None:
        raise StoryTestScenariosError(f"Delivery lane node {node.id} has no owning lane.")
    story: Story | None = lane.story
    if story is None:
        raise StoryTestScenariosError(f"Delivery lane {lane.id} has no owning story.")
    if node.status == StoryDeliveryNodeStatus.LOCKED:
        raise StoryTestScenariosError(f"Node {node.id} is LOCKED — Implementation must complete first.")

    project = db.get(Project, lane.project_id)
    if project is None:
        raise StoryTestScenariosError(f"Lane {lane.id}'s project no longer exists.")
    story_lld_content = _get_story_lld(db, story)
    implementation_plan_content = _get_implementation_plan(db, story)

    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == STORY_TEST_SCENARIOS_AGENT_KEY).first()
    if agent is None:
        raise StoryTestScenariosError(f"No agent is configured for agent_key '{STORY_TEST_SCENARIOS_AGENT_KEY}'.")
    active_prompt = next((p for p in agent.prompts if p.role == AgentPromptRole.DRAFT and p.is_active), None)
    if active_prompt is None:
        raise StoryTestScenariosError(f"No active draft prompt configured for agent '{STORY_TEST_SCENARIOS_AGENT_KEY}'.")

    # A transient, never-persisted WorkflowNode — see module docstring.
    virtual_node = WorkflowNode(
        project_id=project.id,
        node_key="story_test_scenarios",
        name=f"Test Scenarios — {story.title}",
        description=f"Test scenarios scoped to exactly one story: {story.title}.",
        agent_key=STORY_TEST_SCENARIOS_AGENT_KEY,
        required_inputs=["story_lld", "story_implementation_plan"],
        output_artifact_type=STORY_TEST_SCENARIOS_ARTIFACT_TYPE,
        requires_human_approval=True,
        allowed_actions=["draft"],
        status=WorkflowStatus.READY,
        context_token_budget=12000,
        output_token_budget=3072,
        full_content_artifact_types=["story_lld", "story_implementation_plan"],
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
        approved_artifact_content={"story_lld": story_lld_content, "story_implementation_plan": implementation_plan_content},
        approved_artifact_summaries={"story_lld": story_lld_content, "story_implementation_plan": implementation_plan_content},
        freeform_context={
            "story_title": story.title,
            "user_story": story.user_story,
            "acceptance_criteria": "; ".join(story.acceptance_criteria) or "None stated.",
            "technical_areas": ", ".join(story.technical_areas) or "Not stated — infer UI/API relevance from the LLD.",
            **({"clarification_answers": clarification_answers} if clarification_answers else {}),
        },
        context_token_budget=virtual_node.context_token_budget,
        output_token_budget=virtual_node.output_token_budget,
        full_content_artifact_types={"story_lld", "story_implementation_plan"},
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
        artifact_type=STORY_TEST_SCENARIOS_ARTIFACT_TYPE,
        title=f"Test Scenarios — {story.title}",
        content_markdown=result.content_markdown,
        version_number=next_version_number,
        created_by_id=triggered_by.id,
    )
    db.add(story_artifact)

    if result.needs_clarification:
        db.flush()
        return StoryTestScenariosResult(
            agent_run_used_mock=result.used_mock, needs_clarification=True, story_artifact=story_artifact, node_status=node.status
        )
    db.flush()

    # Deliberately NOT completing/advancing the node here — see module
    # docstring. Review/approval is its own explicit action
    # (update_lane_node_status), not an automatic side effect of drafting.
    return StoryTestScenariosResult(
        agent_run_used_mock=result.used_mock, needs_clarification=False, story_artifact=story_artifact,
        node_status=node.status,
    )
