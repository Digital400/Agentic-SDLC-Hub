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
  leaving that node APPROVED and the next node (Problem Discovery) READY
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
    ArtifactStatus,
    ArtifactVersion,
    AuditLog,
    Integration,
    IntegrationProvider,
    IntegrationStatus,
    KnowledgeChunk,
    KnowledgeSource,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatus,
    Review,
    ReviewComment,
    ReviewStatus,
    User,
    UserRole,
    WorkflowStatus,
)
from app.services.audit import record_audit_log
from app.services.embeddings import embed_text
from app.services.graph_engine import GraphEngineService
from app.services.workflow_templates import generate_workflow_graph, load_workflow_template

SAMPLE_PROJECT_NAME = "Customer Loyalty Rewards Platform"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_user(db: Session, *, email: str, full_name: str, role: UserRole) -> User:
    """Users aren't owned by a project (a project's members reference them,
    but deleting a project doesn't delete its members' User rows), so
    re-running this script after a project was deleted and recreated must
    reuse existing users by email rather than re-insert them. `role` (see
    app/services/permissions.py) is applied even to an already-existing
    row, so re-running this after the User.role migration backfilled
    everyone to VIEWER still converges these three seed users to their
    intended role.
    """
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(email=email, full_name=full_name, role=role)
        db.add(user)
        db.flush()
    elif user.role != role:
        user.role = role
    return user


# Hand-authored default prompts for the five agents named in the Prompt
# Library spec — real prompt-engineering content, not placeholders. Every
# other agent still gets a generic, template-derived prompt (see
# _ensure_agent_definitions_and_prompts) so all 11 stages have one.
RICH_DEFAULT_PROMPTS: dict[str, dict] = {
    "requirement_intake": {
        "system_prompt": (
            "You are the Requirement Intake agent for Agentic SDLC Hub. Given a stakeholder's raw request, "
            "produce a concise Requirement Intake Summary that captures the stakeholder, the request, any known "
            "constraints, and a measurable success metric if one is available. Do not invent constraints or "
            "metrics that weren't stated — flag them as open questions instead."
        ),
        "output_format": "Markdown with headings: Stakeholder & Request, Constraints, Success Metric (or Open Questions if not yet known).",
        "validation_checklist": [
            "States who the stakeholder is",
            "Captures the request in one clear sentence",
            "Lists constraints or explicitly says none are known",
            "Includes a success metric or flags it as an open question",
        ],
    },
    "problem_discovery": {
        "system_prompt": (
            "You are the Problem Discovery agent. Given an approved Requirement Intake Summary, investigate and "
            "state the underlying problem clearly enough to design a solution against it: its impact, who it "
            "affects, and any constraints. Do not propose solutions — that's the next stage's job."
        ),
        "output_format": "Markdown with headings: Problem Statement, Impact, Affected Users, Constraints.",
        "validation_checklist": [
            "Problem is stated as a problem, not a solution",
            "Impact is quantified or clearly qualified",
            "Affected users/systems are named",
            "Traces back to the intake summary's request",
        ],
    },
    "solution_discovery": {
        "system_prompt": (
            "You are the Solution Discovery agent. Given an approved Problem Statement, propose 2-3 candidate "
            "solution approaches, weigh their trade-offs, and recommend one. Be explicit about why the "
            "recommended option was chosen over the alternatives."
        ),
        "output_format": "Markdown with headings: Candidate Options, Trade-offs, Recommendation.",
        "validation_checklist": [
            "At least two real alternatives are considered",
            "Trade-offs reference cost, risk, or timeline",
            "A single option is clearly recommended with rationale",
            "Recommendation directly addresses the problem statement",
        ],
    },
    "hld": {
        "system_prompt": (
            "You are the High-Level Design agent. Given the recommended solution option, define the "
            "system-level architecture: major components, how they interact, the data model at a high level, "
            "and key security considerations. Flag open questions rather than guessing at unresolved decisions."
        ),
        "output_format": "Markdown with headings: Overview, Architecture, Data Model, Security Considerations, Open Questions.",
        "validation_checklist": [
            "Every major component has a stated responsibility",
            "Component interactions are described, not just listed",
            "Security considerations are addressed explicitly",
            "Unresolved decisions are listed as open questions, not silently assumed",
        ],
    },
    "story_crafting": {
        "system_prompt": (
            "You are the Story Crafting agent. Given an approved High-Level Design, break it into implementable "
            "stories with clear acceptance criteria, sized so each can reasonably be completed within one "
            "implementation pass. Do not include design details already settled in the HLD — reference them "
            "instead."
        ),
        # This exact field set/labeling is required — it's parsed
        # programmatically by app/services/story_export.py for the Export
        # Stories feature (Markdown/CSV/JSON), not just read as prose. Keep
        # this in sync with packages/prompts/agents/story-crafting-agent.md.
        "output_format": (
            "Markdown: one `## Story: <title>` block per story, each with these exact bold-labeled fields, in "
            "this order: **Epic:**, **Feature:**, **User Story:** (as 'As a <role>, I want <capability>, so that "
            "<benefit>.'), **Priority:** (High/Medium/Low), **Dependencies:** (other story titles, or 'None.'), "
            "**Acceptance Criteria:** (a checklist), **Definition of Done:** (a checklist)."
        ),
        "validation_checklist": [
            "Every story has explicit acceptance criteria",
            "Stories are independently completable",
            "No story silently re-decides something already settled in the HLD",
            "Together, the stories cover the full HLD scope",
            "Every story states Epic, Feature, User Story, Priority, Dependencies, and Definition of Done",
        ],
    },
}


def _ensure_agent_definitions_and_prompts(db: Session, template: dict) -> dict[str, AgentDefinition]:
    """Ensure all 11 AgentDefinitions + an active v1 DRAFT AgentPrompt exist.

    Not project-owned data, so this runs unconditionally (unlike the rest
    of seed(), which is skipped once the sample project exists) and is
    idempotent by agent_key / (agent_definition, role, version).
    """
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
        stage_key = node_data["id"]
        agent = agent_definitions[node_data["agentKey"]]
        has_draft_prompt = any(p.role == AgentPromptRole.DRAFT and p.version == 1 for p in agent.prompts)
        if has_draft_prompt:
            continue

        rich = RICH_DEFAULT_PROMPTS.get(stage_key)
        db.add(
            AgentPrompt(
                agent_definition=agent,
                role=AgentPromptRole.DRAFT,
                version=1,
                name=f"{node_data['name']} — Draft Prompt",
                stage=stage_key,
                system_prompt=rich["system_prompt"] if rich else (
                    f"You are the {node_data['name']} agent. Given the following inputs: "
                    f"{', '.join(node_data['requiredInputs']) or 'none'}, draft a "
                    f"{node_data['outputArtifactType']}. {node_data['description']}"
                ),
                output_format=rich["output_format"] if rich else "Markdown document.",
                validation_checklist=rich["validation_checklist"] if rich else [
                    "Addresses all of the stage's required inputs",
                    "Uses valid Markdown formatting",
                ],
                is_active=True,  # the only version so far — active by definition
            )
        )
    db.flush()
    return agent_definitions


KNOWLEDGE_BASE_SAMPLES: list[dict] = [
    {
        "title": "Company Engineering Handbook",
        "category": "Company Policy",
        "source_type": KnowledgeSourceType.UPLOADED_DOCUMENT,
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            "All new services must expose a /health endpoint returning 200 when ready to serve traffic.",
            "Database migrations are reviewed the same way as code — no direct schema changes in production.",
        ],
    },
    {
        "title": "Loyalty Program Design Guidelines",
        "category": "Market Research",
        "source_type": KnowledgeSourceType.EXTERNAL_LINK,
        "file_url": "https://example.com/research/loyalty-programs-2026",
        "status": KnowledgeSourceStatus.INDEXED,
        "chunks": [
            "Points-based loyalty programs see the strongest repeat purchase impact when redemption is "
            "simple: customers should be able to see their points balance and redeem it in two taps or fewer.",
            "Competitor loyalty programs in retail typically award 1 point per $1 spent and set redemption "
            "thresholds low enough (around 100-200 points) that a customer's first reward feels achievable "
            "within a few purchases, which drives early engagement.",
            "Loyalty programs that expire points aggressively see higher churn; a rolling 12-month expiry "
            "window is a common balance between encouraging return visits and not feeling punitive.",
        ],
    },
    {
        "title": "Requirement Intake Summary — Customer Loyalty Rewards Platform",
        "category": "Project Artifact",
        "source_type": KnowledgeSourceType.PROJECT_ARTIFACT,
        "status": KnowledgeSourceStatus.FAILED,
        "chunks": [],
    },
]


def _ensure_knowledge_base_samples(db: Session, uploaded_by: User) -> int:
    """Ensure a few sample KnowledgeSource rows exist, one per source_type,
    so the Knowledge Base UI has something to show, and so agent runs
    against the sample project have something real to retrieve (see
    app/services/retrieval.py). Idempotent by title. Chunks are embedded
    immediately with the same app/services/embeddings.py function real
    ingestion would use — these aren't placeholder vectors.
    """
    created = 0
    for sample in KNOWLEDGE_BASE_SAMPLES:
        existing_source = db.query(KnowledgeSource).filter(KnowledgeSource.title == sample["title"]).first()
        if existing_source is not None:
            continue

        source = KnowledgeSource(
            title=sample["title"],
            category=sample["category"],
            source_type=sample["source_type"],
            file_url=sample.get("file_url"),
            status=sample["status"],
            uploaded_by=uploaded_by,
        )
        db.add(source)
        db.flush()

        for i, content in enumerate(sample["chunks"]):
            db.add(KnowledgeChunk(source=source, chunk_index=i, content=content, embedding=embed_text(content)))

        created += 1
    db.flush()
    return created


STORY_BACKLOG_DEMO_TITLE = f"Story Backlog — {SAMPLE_PROJECT_NAME}"

# Demo content for the Export Stories feature (see
# app/services/story_export.py) — hand-authored in the exact format the
# story-crafting-agent's prompt requires, so the parser has something real
# to work against without needing a live agent run first. Note this
# artifact is seeded APPROVED independent of the sample project's actual
# workflow progress (which stops at Problem Discovery) — it exists purely
# to demonstrate/exercise the export feature, not to claim the project has
# really reached Story Crafting.
STORY_BACKLOG_DEMO_CONTENT = """# Story Backlog — Customer Loyalty Rewards Platform

## Story: Display points balance on account home
**Epic:** Loyalty Points Program
**Feature:** Points Balance Display
**User Story:** As a customer, I want to see my current points balance on my account home screen, so that I always know how many points I have without digging through menus.
**Priority:** High
**Dependencies:** None.
**Acceptance Criteria:**
- [ ] Points balance is visible on the account home screen within one screen load, no extra taps.
- [ ] Balance updates within 5 minutes of a qualifying purchase.
- [ ] Balance displays "0 points" rather than a blank/error state for a new customer.
**Definition of Done:**
- [ ] Code reviewed and merged.
- [ ] Unit tests cover the zero-balance and stale-cache cases.
- [ ] Verified against the points ledger service in staging.

## Story: Redeem points for a discount at checkout
**Epic:** Loyalty Points Program
**Feature:** Points Redemption
**User Story:** As a customer, I want to redeem my points for a discount at checkout, so that I get value from my accumulated points in two taps or fewer.
**Priority:** High
**Dependencies:** Display points balance on account home.
**Acceptance Criteria:**
- [ ] Customer can apply available points as a discount in at most two taps from the cart screen.
- [ ] Partial redemption is supported when the order total is less than the full points value.
- [ ] Points balance and order total both reflect the redemption before payment is confirmed.
**Definition of Done:**
- [ ] Code reviewed and merged.
- [ ] Integration test covers full and partial redemption.
- [ ] Reviewed by Payments for correct ledger reconciliation.

## Story: Expire unused points after a rolling 12-month window
**Epic:** Loyalty Points Program
**Feature:** Points Expiry
**User Story:** As the business, I want unused points to expire on a rolling 12-month window, so that the points liability doesn't grow unbounded while still giving customers a fair amount of time to redeem.
**Priority:** Medium
**Dependencies:** Display points balance on account home.
**Acceptance Criteria:**
- [ ] Points earned more than 12 months ago are automatically excluded from the redeemable balance.
- [ ] Customer receives a notification 30 days before their oldest points expire.
- [ ] Expired points are recorded in the ledger for audit purposes, not silently dropped.
**Definition of Done:**
- [ ] Code reviewed and merged.
- [ ] Scheduled job tested against a seeded ledger spanning more than 12 months.
- [ ] Notification copy approved by Marketing.
"""


def _ensure_story_export_demo(db: Session, project: Project, author: User) -> bool:
    """Idempotent by title. Returns True if it created the demo artifact."""
    existing_artifact = db.query(Artifact).filter(Artifact.title == STORY_BACKLOG_DEMO_TITLE).first()
    if existing_artifact is not None:
        return False

    story_crafting_node = next((n for n in project.workflow_nodes if n.node_key == "story_crafting"), None)
    if story_crafting_node is None:
        return False

    artifact = Artifact(
        project=project,
        workflow_node=story_crafting_node,
        artifact_type=story_crafting_node.output_artifact_type,
        title=STORY_BACKLOG_DEMO_TITLE,
        status=ArtifactStatus.APPROVED,
        created_by=author,
    )
    db.add(artifact)
    db.flush()

    version = ArtifactVersion(
        artifact=artifact,
        version_number=1,
        content_markdown=STORY_BACKLOG_DEMO_CONTENT,
        created_by=author,
        change_summary="Demo backlog for the Export Stories feature.",
    )
    db.add(version)
    db.flush()
    artifact.current_version = version

    record_audit_log(
        db,
        project_id=project.id,
        actor_user_id=author.id,
        action="artifact.created",
        entity_type="Artifact",
        entity_id=artifact.id,
        extra_data={"workflow_node": story_crafting_node.node_key, "artifact_type": artifact.artifact_type, "seeded_demo": True},
    )
    db.flush()
    return True


INTEGRATION_PLACEHOLDERS: list[dict] = [
    {"integration_name": "Jira", "provider": IntegrationProvider.JIRA},
    {"integration_name": "Confluence", "provider": IntegrationProvider.CONFLUENCE},
    {"integration_name": "GitHub", "provider": IntegrationProvider.GITHUB},
    {"integration_name": "Slack", "provider": IntegrationProvider.SLACK},
    {"integration_name": "Azure DevOps", "provider": IntegrationProvider.AZURE_DEVOPS},
]


def _ensure_integration_placeholders(db: Session) -> int:
    """Ensures one placeholder Integration row per planned provider exists
    (see docs/architecture.md's MCP integrations section) — idempotent by
    provider. Every row is created NOT_CONNECTED with no config; nothing
    here ever actually connects (see app/models/integration.py)."""
    created = 0
    for placeholder in INTEGRATION_PLACEHOLDERS:
        existing = db.query(Integration).filter(Integration.provider == placeholder["provider"]).first()
        if existing is not None:
            continue
        db.add(
            Integration(
                integration_name=placeholder["integration_name"],
                provider=placeholder["provider"],
                status=IntegrationStatus.NOT_CONNECTED,
            )
        )
        created += 1
    db.flush()
    return created


def seed(db: Session) -> None:
    template = load_workflow_template()
    agent_definitions = _ensure_agent_definitions_and_prompts(db, template)

    # Not project-owned, so — like agent definitions/prompts above — this
    # runs unconditionally rather than being gated by the sample project
    # existence check below.
    kb_owner = _get_or_create_user(db, email="sampathisuru516@gmail.com", full_name="Suru Sampathi", role=UserRole.ADMIN)
    knowledge_sources_created = _ensure_knowledge_base_samples(db, kb_owner)
    integrations_created = _ensure_integration_placeholders(db)

    # Also not project-owned — ensured unconditionally so re-running this
    # script converges these three seed users' roles even when the sample
    # project already exists and the rest of this function is skipped below
    # (e.g. after the User.role migration backfilled everyone to VIEWER).
    contributor = _get_or_create_user(db, email="priya.dev@agentic-sdlc-hub.local", full_name="Priya Dev", role=UserRole.BA)
    approver = _get_or_create_user(
        db, email="alex.reviewer@agentic-sdlc-hub.local", full_name="Alex Reviewer", role=UserRole.PRODUCT_OWNER
    )

    existing = db.query(Project).filter(Project.name == SAMPLE_PROJECT_NAME).first()
    if existing is not None:
        story_demo_created = _ensure_story_export_demo(db, existing, kb_owner)
        db.commit()
        print(f"Sample project '{SAMPLE_PROJECT_NAME}' already exists (id={existing.id}); skipping project seed.")
        print(f"Agent definitions ensured: {len(agent_definitions)}.")
        print(f"Knowledge sources created: {knowledge_sources_created}.")
        print(f"Story export demo artifact created: {story_demo_created}.")
        print(f"Integration placeholders created: {integrations_created}.")
        return

    now = _now()

    # --- Users -----------------------------------------------------------
    owner = kb_owner

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
        status=ArtifactStatus.READY_FOR_REVIEW,
        created_by=contributor,
    )
    db.add(artifact)
    db.flush()

    v1 = ArtifactVersion(
        artifact=artifact,
        version_number=1,
        content_markdown=agent_run.output_text,
        # No agent-authorship link yet (AI drafting isn't wired up — see
        # docs/mvp-plan.md); recorded as created by whoever triggered the
        # draft request.
        created_by=contributor,
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
        content_markdown=v1.content_markdown
        + "\n**Success metric:** +15% repeat purchase rate within 2 quarters of launch.\n",
        created_by=contributor,
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

    artifact.status = ArtifactStatus.APPROVED
    # Real engine, not hand-rolled status assignments — see
    # app/services/graph_engine.py. mark_approved() is what a real
    # POST /reviews/{id}/approve call does; unlock_next_nodes() is what
    # actually decides problem_discovery_node should become READY (rather
    # than assuming it here), so this stays correct if the template ever
    # changes.
    graph_engine = GraphEngineService(db)
    graph_engine.mark_approved(intake_node)
    unlocked = graph_engine.unlock_next_nodes(intake_node)
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
        extra_data={"from": "WAITING_FOR_REVIEW", "to": WorkflowStatus.APPROVED.value},
        created_at=decided_at,
    )
    for node in unlocked:
        log(
            project=project,
            actor_user=approver,
            action="workflow_node.unlocked",
            entity_type="WorkflowNode",
            entity_id=node.id,
            extra_data={"from": "LOCKED", "to": WorkflowStatus.READY.value},
            created_at=decided_at,
        )

    story_demo_created = _ensure_story_export_demo(db, project, contributor)
    db.commit()

    print(f"Seeded sample project '{SAMPLE_PROJECT_NAME}' (id={project.id}).")
    print(f"  Users: owner={owner.email}, contributor={contributor.email}, approver={approver.email}")
    print(f"  Workflow nodes: {len(project.workflow_nodes)}, edges: {len(project.workflow_edges)}")
    print(f"  {intake_node.name}: {intake_node.status.value} -> {problem_discovery_node.name}: {problem_discovery_node.status.value}")
    print(f"  Knowledge sources created: {knowledge_sources_created}.")
    print(f"  Story export demo artifact created: {story_demo_created}.")


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
