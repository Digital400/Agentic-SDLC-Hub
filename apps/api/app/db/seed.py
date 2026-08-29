"""Seed data for one sample project.

Run with:

    python -m app.db.seed

Requires the schema to already exist — run `alembic upgrade head` first.
Safe to re-run: if the sample project already exists, seeding is skipped
rather than duplicated.

What this creates, end to end, so every one of the 13 models has at least
one real row and the MVP's main user journey (see docs/mvp-plan.md) is
visible in the data:

- 3 users (an owner, a contributor, an approver)
- 11 AgentDefinitions + one AgentPrompt each, one per default-workflow stage
- 1 project on the default SDLC workflow, with its 11 WorkflowNodes and
  11 WorkflowEdges generated from the template
- A completed pass through the first stage (Requirement Intake):
  an agent draft -> a human edit -> a submitted review -> an approval,
  leaving that node COMPLETED and the next node (Problem Discovery)
  IN_PROGRESS
- Audit log entries for the key events along the way
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import (
    AgentDefinition,
    AgentPrompt,
    AgentPromptRole,
    AgentRun,
    AgentRunStatus,
    Artifact,
    ArtifactVersion,
    AuditLog,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatus,
    Review,
    ReviewComment,
    ReviewStatus,
    User,
    WorkflowStatus,
)
from app.services.workflow_templates import generate_workflow_graph, load_workflow_template

SAMPLE_PROJECT_NAME = "Customer Loyalty Rewards Platform"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_user(db: Session, *, email: str, full_name: str) -> User:
    """Users aren't owned by a project (a project's members reference them,
    but deleting a project doesn't delete its members' User rows), so
    re-running this script after a project was deleted and recreated must
    reuse existing users by email rather than re-insert them.
    """
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(email=email, full_name=full_name)
        db.add(user)
        db.flush()
    return user


def seed(db: Session) -> None:
    existing = db.query(Project).filter(Project.name == SAMPLE_PROJECT_NAME).first()
    if existing is not None:
        print(f"Sample project '{SAMPLE_PROJECT_NAME}' already exists (id={existing.id}); skipping seed.")
        return

    now = _now()

    # --- Users -----------------------------------------------------------
    owner = _get_or_create_user(db, email="sampathisuru516@gmail.com", full_name="Suru Sampathi")
    contributor = _get_or_create_user(db, email="priya.dev@agentic-sdlc-hub.local", full_name="Priya Dev")
    approver = _get_or_create_user(db, email="alex.reviewer@agentic-sdlc-hub.local", full_name="Alex Reviewer")

    # --- Agent definitions + one draft prompt per default-workflow stage --
    # Same reasoning as users: AgentDefinition/AgentPrompt aren't
    # project-owned, so a re-run must reuse existing ones (agent_key and
    # (agent_definition, role, version) are both unique) rather than
    # re-insert them.
    template = load_workflow_template()
    agent_definitions: dict[str, AgentDefinition] = {}
    for node_data in template["nodes"]:
        agent_key = node_data["agentKey"]
        agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == agent_key).first()
        if agent is None:
            agent = AgentDefinition(
                agent_key=agent_key,
                name=node_data["name"] + " Agent",
                description=f"Drafts, improves, and validates the {node_data['outputArtifactType']} "
                f"produced by the {node_data['name']} stage.",
                model_name="stub-no-model-configured",  # no real AI calls yet
            )
            db.add(agent)
            db.flush()
        agent_definitions[agent_key] = agent

    for node_data in template["nodes"]:
        agent = agent_definitions[node_data["agentKey"]]
        has_draft_prompt = any(p.role == AgentPromptRole.DRAFT and p.version == 1 for p in agent.prompts)
        if not has_draft_prompt:
            db.add(
                AgentPrompt(
                    agent_definition=agent,
                    role=AgentPromptRole.DRAFT,
                    version=1,
                    template=(
                        f"You are the {node_data['name']} agent. Given the following inputs: "
                        f"{', '.join(node_data['requiredInputs']) or 'none'}, draft a "
                        f"{node_data['outputArtifactType']}. {node_data['description']}"
                    ),
                )
            )
    db.flush()

    # --- Project + membership + generated workflow graph ------------------
    project = Project(
        name=SAMPLE_PROJECT_NAME,
        description=(
            "Add a points-based loyalty rewards program to the customer mobile app: "
            "customers earn points on purchases and redeem them for discounts."
        ),
        business_owner="Marketing — Jordan Lee",
        workflow_template_id=template["id"],
        workflow_template_version=template["version"],
        current_stage=template["startNode"],
        status=ProjectStatus.ACTIVE,
        created_by=owner,
    )
    db.add(project)
    db.flush()

    db.add_all(
        [
            ProjectMember(project=project, user=owner, role=ProjectRole.OWNER),
            ProjectMember(project=project, user=contributor, role=ProjectRole.CONTRIBUTOR),
            ProjectMember(project=project, user=approver, role=ProjectRole.APPROVER),
        ]
    )

    generate_workflow_graph(db, project, template)
    db.flush()

    def log(**kwargs) -> AuditLog:
        """Create + immediately register an AuditLog row.

        Added to the session right away (rather than batched into a list
        for a later add_all) so the Project.audit_logs backref population
        that happens on construction doesn't warn about an object that
        isn't in the session yet.
        """
        entry = AuditLog(**kwargs)
        db.add(entry)
        return entry

    log(
        project=project,
        actor_user=owner,
        action="project.created",
        entity_type="Project",
        entity_id=project.id,
        extra_data={"workflow_template_id": template["id"], "workflow_template_version": template["version"]},
        created_at=now - timedelta(days=2, hours=1),
    )

    # --- Walk the first stage (Requirement Intake) through to approval ----
    intake_node = next(n for n in project.workflow_nodes if n.node_key == "requirement_intake")
    problem_discovery_node = next(n for n in project.workflow_nodes if n.node_key == "problem_discovery")
    intake_agent = agent_definitions["requirement-intake-agent"]
    intake_prompt = intake_agent.prompts[0]

    drafted_at = now - timedelta(days=2)
    agent_run = AgentRun(
        project=project,
        workflow_node=intake_node,
        agent_definition=intake_agent,
        agent_prompt=intake_prompt,
        triggered_by_user=contributor,
        action=AgentPromptRole.DRAFT,
        status=AgentRunStatus.COMPLETED,
        input_context={
            "stakeholder_request": (
                "Marketing wants a loyalty program to improve repeat purchase rate; "
                "no fixed launch date yet, but it should ship before the holiday season."
            )
        },
        output_text=(
            "# Requirement Intake Summary\n\n"
            "**Stakeholder:** Marketing\n\n"
            "**Request:** Introduce a points-based loyalty program in the customer "
            "mobile app so customers earn points on purchases and redeem them for "
            "discounts, to improve repeat purchase rate.\n\n"
            "**Constraints:** No fixed launch date; target shipping ahead of the "
            "holiday season.\n"
        ),
        started_at=drafted_at,
        completed_at=drafted_at,
    )
    db.add(agent_run)
    db.flush()

    log(
        project=project,
        actor_agent_run=agent_run,
        action="agent_run.completed",
        entity_type="AgentRun",
        entity_id=agent_run.id,
        extra_data={"workflow_node": intake_node.node_key, "action": "draft"},
        created_at=drafted_at,
    )

    artifact = Artifact(
        project=project,
        workflow_node=intake_node,
        artifact_type=intake_node.output_artifact_type,
        title=f"Requirement Intake Summary — {SAMPLE_PROJECT_NAME}",
        status=WorkflowStatus.WAITING_FOR_REVIEW,
    )
    db.add(artifact)
    db.flush()

    v1 = ArtifactVersion(
        artifact=artifact,
        version_number=1,
        content=agent_run.output_text,
        authored_by_agent_run=agent_run,
        change_summary="Initial AI-drafted intake summary.",
        created_at=drafted_at,
    )
    db.add(v1)
    db.flush()
    artifact.current_version = v1

    edited_at = now - timedelta(days=1, hours=6)
    v2 = ArtifactVersion(
        artifact=artifact,
        version_number=2,
        content=v1.content
        + "\n**Success metric:** +15% repeat purchase rate within 2 quarters of launch.\n",
        authored_by_user=contributor,
        change_summary="Clarified success metrics after stakeholder sync.",
        created_at=edited_at,
    )
    db.add(v2)
    db.flush()
    artifact.current_version = v2

    log(
        project=project,
        actor_user=contributor,
        action="artifact_version.created",
        entity_type="ArtifactVersion",
        entity_id=v2.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": 2},
        created_at=edited_at,
    )

    submitted_at = now - timedelta(days=1, hours=5)
    review = Review(
        artifact_version=v2,
        workflow_node=intake_node,
        reviewer=approver,
        status=ReviewStatus.PENDING,
        created_at=submitted_at,
    )
    db.add(review)
    db.flush()

    decided_at = now - timedelta(days=1)
    review.status = ReviewStatus.APPROVED
    review.decided_at = decided_at

    db.add(
        ReviewComment(
            review=review,
            author=approver,
            body=(
                "Looks good — clear problem framing and a measurable success "
                "metric. Approved to proceed to Problem Discovery."
            ),
            created_at=decided_at,
        )
    )

    artifact.status = WorkflowStatus.APPROVED
    intake_node.status = WorkflowStatus.COMPLETED
    problem_discovery_node.status = WorkflowStatus.IN_PROGRESS
    project.current_stage = problem_discovery_node.node_key

    log(
        project=project,
        actor_user=approver,
        action="review.approved",
        entity_type="Review",
        entity_id=review.id,
        extra_data={"artifact_id": str(artifact.id), "workflow_node": intake_node.node_key},
        created_at=decided_at,
    )
    log(
        project=project,
        actor_user=approver,
        action="workflow_node.status_changed",
        entity_type="WorkflowNode",
        entity_id=intake_node.id,
        extra_data={"from": "WAITING_FOR_REVIEW", "to": "COMPLETED"},
        created_at=decided_at,
    )
    log(
        project=project,
        actor_user=approver,
        action="workflow_node.status_changed",
        entity_type="WorkflowNode",
        entity_id=problem_discovery_node.id,
        extra_data={"from": "NOT_STARTED", "to": "IN_PROGRESS"},
        created_at=decided_at,
    )

    db.commit()

    print(f"Seeded sample project '{SAMPLE_PROJECT_NAME}' (id={project.id}).")
    print(f"  Users: owner={owner.email}, contributor={contributor.email}, approver={approver.email}")
    print(f"  Workflow nodes: {len(project.workflow_nodes)}, edges: {len(project.workflow_edges)}")
    print(f"  {intake_node.name}: {intake_node.status.value} -> {problem_discovery_node.name}: {problem_discovery_node.status.value}")


def main() -> None:
    db = SessionLocal()
    try:
        seed(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
