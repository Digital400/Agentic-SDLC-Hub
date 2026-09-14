"""Implementation Agent execution endpoints.

Covers: start a run for one approved ImplementationTask (requirement 1's
three gates enforced here — LLD approved, Implementation Plan approved,
GitHub repo snapshot exists), fetch one run, record a human's
accept/reject review decision on a completed run's proposed diff, and —
only once ACCEPTED — create a real GitHub pull request from it.

HARD RULE: generating a run and Accepting/Rejecting it never itself writes
to GitHub in any way. A real write only happens through
`create_pull_request` below, which is only reachable once
`review_status == ACCEPTED`, and even then only ever creates a fresh
feature branch and commits/PRs against that — never this repository's
default branch (see app/services/github_integration.py's write methods,
none of which is ever called here with anything else).
"""

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import decrypt_repository_token
from app.core.config import get_settings
from app.core.database import get_db
from app.models import (
    GithubSetupOption,
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationRunStatus,
    ImplementationTask,
    ImplementationTaskStatus,
    KnowledgeContentType,
    Project,
    ProjectCodingStandard,
    ProjectEngineeringSetup,
    ProjectGuardrail,
    PullRequestLink,
    PullRequestStatus,
    Repository,
    RepositorySnapshot,
    Story,
    StoryArtifact,
    StoryDeliveryNodeStatus,
    User,
    WorkflowNode,
    WorkflowStatus,
)
from app.schemas.implementation_run import (
    CreatePullRequestRequest,
    ImplementationRunRead,
    ReviewImplementationRunRequest,
    StartImplementationRunRequest,
)
from app.services.agent_context_builder import EngineeringSetupContext, build_engineering_setup_context
from app.services.audit import record_audit_log
from app.services import github_integration as github_api
from app.services.github_integration import GitHubIntegrationError
from app.services.graph_engine import GraphEngineService
from app.services.implementation_agent import SUPPORTED_AREAS, run_implementation_agent
from app.services.permissions import require_can_edit_stage
from app.services.repo_context_builder import RepoContextBuilderService
from app.services.retrieval import retrieve_relevant_chunks
from app.services.story_export import Story as StoryDataclass
from app.services.story_export import find_related_story
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE
from app.services.story_test_scenarios_agent import get_latest_story_test_scenarios

router = APIRouter(prefix="/implementation-runs", tags=["implementation-runs"])


def _resolve_repository_for_task(db: Session, project: Project, task: ImplementationTask) -> Repository | None:
    """Multi-repo support — a project may have more than one connected
    Repository (see Repository.is_primary). A task that explicitly names
    one (task.repository_id, set via PATCH /implementation-tasks/{id}/
    repository) always uses that; otherwise falls back to the project's
    primary repository. The final `.order_by(created_at.desc())` fallback
    is only a defensive safety net for a project whose repositories all
    somehow have is_primary=False (shouldn't happen — every repo-creation/
    deletion path in github_integration.py keeps exactly one primary — but
    this must never leave a single-repo project unable to resolve one)."""
    if task.repository_id is not None:
        repository = db.get(Repository, task.repository_id)
        if repository is not None and repository.project_id == project.id:
            return repository
        # The task's chosen repository was removed from the project since
        # it was assigned — fall through to the primary repo rather than
        # failing the run outright.
    return (
        db.query(Repository)
        .filter(Repository.project_id == project.id)
        .order_by(Repository.is_primary.desc(), Repository.created_at.desc())
        .first()
    )


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> ImplementationRun:
    run = db.get(ImplementationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation run {run_id} not found")
    return run


def _get_pull_request_link(db: Session, run_id: uuid.UUID) -> PullRequestLink | None:
    return db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == run_id).first()


def _build_prior_story_task_context(db: Session, earlier_siblings: list[ImplementationTask]) -> str:
    """Full-stack-per-story: a summary of every earlier-ordered sibling
    task's latest Accepted run — what area, what it did, and which files
    it touched — so the next area's agent builds on real, already-
    committed work instead of guessing or recreating it. Empty for a
    single-area story or the first task in a sequence (both the common
    case — most stories still get exactly one task, unchanged)."""
    if not earlier_siblings:
        return ""
    blocks = []
    for sibling in earlier_siblings:
        accepted_run = (
            db.query(ImplementationRun)
            .filter(
                ImplementationRun.implementation_task_id == sibling.id,
                ImplementationRun.review_status == ImplementationRunReviewStatus.ACCEPTED,
            )
            .order_by(ImplementationRun.created_at.desc())
            .first()
        )
        if accepted_run is None:
            continue  # defensive — sibling.status == COMPLETED already guarantees this exists
        changed_paths = ", ".join(c["path"] for c in accepted_run.proposed_file_changes) or "(no files recorded)"
        blocks.append(
            f"## {sibling.area.value}: {sibling.title}\n"
            f"{accepted_run.explanation or '(no explanation recorded)'}\n"
            f"Files changed: {changed_paths}"
        )
    return "\n\n".join(blocks)


def _get_open_task_pull_request(db: Session, task: ImplementationTask) -> PullRequestLink | None:
    """This task's own still-open PR from an earlier run, if any.

    For a story-scoped task, "this task" means the whole STORY, not just
    this one area: full-stack-per-story (see app/api/routes/stories.py's
    _ensure_story_implementation_tasks) can give one story several
    sibling tasks (DATABASE, BACKEND, FRONTEND, ...) that are all really
    one piece of work, meant to land on one shared branch/PR — so this
    looks up by story_id, not implementation_task_id, whenever the task
    has one. A legacy/project-level task (story_id is None) keeps the
    original per-task lookup.

    Either way, once a PR exists a later Accepted run — from this same
    task regenerated, OR from the next sibling task in sequence — lands
    as more commits on that SAME PR, never a second, competing one. Only
    OPEN counts — a MERGED/CLOSED PR is done; a run accepted after that
    starts a fresh PR, same as today."""
    query = db.query(PullRequestLink).filter(PullRequestLink.status == PullRequestStatus.OPEN)
    if task.story_id is not None:
        query = query.filter(PullRequestLink.story_id == task.story_id)
    else:
        query = query.filter(PullRequestLink.implementation_task_id == task.id)
    return query.order_by(PullRequestLink.created_at.desc()).first()


_STANDARDS_CONTENT_TYPES = [KnowledgeContentType.COMPANY_STANDARD, KnowledgeContentType.ARCHITECTURE_RULE]


def _fetch_standards_chunks(db: Session, project: Project, task: ImplementationTask):
    """Requirement 4 — "coding standards from RAG." A transient,
    never-persisted WorkflowNode stands in for retrieve_relevant_chunks'
    required `node` parameter (it only ever reads plain string attributes
    off it — see app/services/story_lld_agent.py's module docstring for
    the same reuse pattern) so this works identically whether `task` is
    story-scoped or project-level, with no real WorkflowNode required.
    Same try/except-swallow contract as pr_review_runs.py's own
    _fetch_standards_chunks: RAG input is additive, never blocks a run."""
    virtual_node = WorkflowNode(
        project_id=project.id, node_key="implementation", name="Implementation",
        description="Coding standards retrieval for an implementation run.",
        agent_key="implementation-agent", required_inputs=[], output_artifact_type="code_change",
        requires_human_approval=False, allowed_actions=["draft"], status=WorkflowStatus.READY,
        order_index=0, position_x=0, position_y=0,
    )
    try:
        return retrieve_relevant_chunks(
            db, project=project, node=virtual_node, freeform_context={"query": f"{task.title} {task.description}"},
            approved_inputs={}, content_types=_STANDARDS_CONTENT_TYPES, top_k=5,
        )
    except Exception:  # noqa: BLE001 — RAG input is additive, never blocks a run
        return []


def _fetch_engineering_setup_context(db: Session, project: Project) -> EngineeringSetupContext:
    """Agent Context Builder, agent_type="implementation" — coding
    standards, AI guardrails, GitHub repository config (branch naming
    pattern, PR target branch), build/test commands, and Code Runner
    restrictions (see app/services/agent_context_builder.py). Distinct
    from _fetch_standards_chunks' RAG-retrieved COMPANY_STANDARD content
    above, which stays semantically retrieved, not exhaustively included.
    A project with no ProjectEngineeringSetup row returns an empty
    context, same as before this feature existed (rule 10)."""
    return build_engineering_setup_context(db, project=project, agent_type="implementation", output_token_budget=2000)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, *, max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (slug or "task")[:max_len]


def _derive_project_key(project_name: str) -> str:
    """No persisted project-key field exists anywhere in this codebase
    (checked) — this is a disclosed, deterministic heuristic used only for
    display (PR titles/commit messages), never stored as if it were a real
    identifier. Multi-word names take up to the first 4 words' initials
    ("Agentic SDLC Hub" -> "ASH"); a single word takes its first 6
    characters, both uppercased."""
    words = re.findall(r"[A-Za-z0-9]+", project_name)
    if len(words) >= 2:
        return "".join(w[0] for w in words[:4]).upper()
    if words:
        return words[0][:6].upper()
    return "PROJ"


@router.post("", response_model=ImplementationRunRead, status_code=status.HTTP_201_CREATED)
def start_implementation_run(payload: StartImplementationRunRequest, db: Session = Depends(get_db)) -> ImplementationRunRead:
    task = db.get(ImplementationTask, payload.implementation_task_id)
    if task is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"implementation_task_id {payload.implementation_task_id} does not match an existing implementation task",
        )
    project = db.get(Project, task.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Task {task.id}'s project no longer exists.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user"
        )

    # Story-level implementation workflow, requirement 1 — a story-scoped
    # task (task.story_id is set) belongs to that story's own delivery
    # lane, not the project-level WorkflowNode graph a legacy
    # (task.story_id is None) task uses. Branch once, here, rather than
    # scattering `if task.story_id` checks through the rest of this
    # function — project-level behavior below this block is byte-for-byte
    # unchanged from before this feature.
    story_id: uuid.UUID | None = None
    lane_id: uuid.UUID | None = None
    story_lld_artifact_id: uuid.UUID | None = None
    story: StoryDataclass | None = None
    jira_issue_key: str | None = None
    # Story Code Implementation Agent — additive agent input, story-scoped
    # runs only (see below); a project-level run leaves both "".
    implementation_plan_summary: str = ""
    test_scenarios_summary: str = ""
    # Full-stack-per-story sequencing — see _build_prior_story_task_context.
    prior_story_task_context: str = ""

    if task.story_id is not None:
        story_row = db.get(Story, task.story_id)
        if story_row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Task {task.id}'s story no longer exists.")
        lane = story_row.delivery_lane
        if lane is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Story {story_row.id} has no delivery lane.")

        implementation_lane_node = next((n for n in lane.nodes if n.node_key == "IMPLEMENTATION"), None)
        lld_review_node = next((n for n in lane.nodes if n.node_key == "LLD_REVIEW"), None)
        if implementation_lane_node is None or lld_review_node is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Lane {lane.id} is missing its IMPLEMENTATION/LLD_REVIEW nodes.")

        require_can_edit_stage(triggered_by, "implementation")

        # Requirement 2 / rule — implementation cannot start before Story
        # LLD is approved. LLD_REVIEW completing IS that approval (a Tech
        # Lead-only transition — see app/api/routes/stories.py's
        # update_lane_node_status).
        if lld_review_node.status != StoryDeliveryNodeStatus.COMPLETED:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Cannot start implementation — this story's Story LLD has not been approved yet."
            )
        if implementation_lane_node.status == StoryDeliveryNodeStatus.LOCKED:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Node {implementation_lane_node.id} is LOCKED.")

        # Full-stack-per-story sequencing (see app/api/routes/stories.py's
        # _ensure_story_implementation_tasks/_current_story_task): a story
        # can have several tasks, one per area, meant to run in DATABASE ->
        # BACKEND -> FRONTEND order so each area's agent builds on the
        # previous one's already-committed code (a migration exists before
        # the API queries the column it added; the endpoint exists before
        # the UI calls it). A task can't start until every earlier-ordered
        # sibling task for this story has an Accepted run.
        sibling_tasks = (
            db.query(ImplementationTask)
            .filter(ImplementationTask.story_id == story_row.id)
            .order_by(ImplementationTask.order_index)
            .all()
        )
        earlier_siblings = [t for t in sibling_tasks if t.order_index < task.order_index]
        unfinished_siblings = [t for t in earlier_siblings if t.status != ImplementationTaskStatus.COMPLETED]
        if unfinished_siblings:
            names = ", ".join(f"{t.area.value} ({t.title})" for t in unfinished_siblings)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Cannot start the {task.area.value} task yet — this story's earlier task(s) must be run and "
                f"Accepted first: {names}.",
            )
        prior_story_task_context = _build_prior_story_task_context(db, earlier_siblings)

        story_lld_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == story_row.id, StoryArtifact.artifact_type == STORY_LLD_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        lld_summary = story_lld_artifact.content_markdown if story_lld_artifact is not None else ""
        story_lld_artifact_id = story_lld_artifact.id if story_lld_artifact is not None else None

        # Story Code Implementation Agent — Implementation Plan/Test
        # Scenarios as additive agent input (precondition 2's "Implementation
        # Plan exists" is already guaranteed structurally by
        # implementation_lane_node's own LOCKED check above — IMPLEMENTATION
        # can't be reachable at all until IMPLEMENTATION_PLAN is accepted).
        plan_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == story_row.id, StoryArtifact.artifact_type == STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        implementation_plan_summary = plan_artifact.content_markdown if plan_artifact is not None else ""
        test_scenarios_artifact = get_latest_story_test_scenarios(db, story_row.id)
        test_scenarios_summary = test_scenarios_artifact.content_markdown if test_scenarios_artifact is not None else ""

        story_id = story_row.id
        lane_id = lane.id
        jira_issue_key = story_row.jira_issue_key
        # The story dataclass run_implementation_agent expects — built
        # straight from the real Story row rather than re-parsed out of a
        # backlog document (find_related_story's own use case), since a
        # story-scoped task always has one already.
        story = StoryDataclass(
            title=story_row.title, epic=story_row.epic, feature=story_row.feature, user_story=story_row.user_story,
            priority=story_row.priority, dependencies=story_row.dependencies,
            acceptance_criteria=story_row.acceptance_criteria, definition_of_done=story_row.definition_of_done,
        )

        if implementation_lane_node.status == StoryDeliveryNodeStatus.READY:
            implementation_lane_node.status = StoryDeliveryNodeStatus.IN_PROGRESS
            implementation_lane_node.started_at = datetime.now(timezone.utc)

        graph_gate_reasons: list[str] = []
    else:
        implementation_node = (
            db.query(WorkflowNode)
            .filter(
                WorkflowNode.project_id == project.id,
                WorkflowNode.node_key == "implementation",
                WorkflowNode.story_id.is_(None),
            )
            .first()
        )
        if implementation_node is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no implementation stage.")
        require_can_edit_stage(triggered_by, implementation_node.node_key)

        # Requirement 1, gates 1 & 2 — LLD approved + Implementation Plan
        # approved. `implementation`'s own required_inputs already lists
        # both artifact types (see workflows/sdlc-workflow.json), so this
        # one call enforces both, exactly like
        # generate_implementation_plan's own gate.
        graph_engine = GraphEngineService(db)
        validation = graph_engine.validate_can_run(project=project, node=implementation_node, freeform_context={})
        graph_gate_reasons = validation.reasons
        lld_summary = validation.approved_artifact_summaries.get("lld_document", "")
        story_backlog_content = validation.approved_artifact_content.get("story_backlog", "")
        story = find_related_story(story_backlog_content, task.linked_story)

    if graph_gate_reasons:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start — " + "; ".join(graph_gate_reasons) + ".")

    if task.area not in SUPPORTED_AREAS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No {task.area.value} Implementation Agent is available yet — only "
            f"{', '.join(sorted(a.value for a in SUPPORTED_AREAS))} are implemented.",
        )

    # Project Engineering Setup rule 4 — "Code Implementation requires
    # GitHub repo config." A project with no ProjectEngineeringSetup row
    # at all is ungated here (rule 10 — every project created before this
    # feature existed keeps working exactly as before); one that
    # explicitly chose SKIP_FOR_NOW during setup is blocked with a message
    # pointing at *why*, rather than the generic "no repository" message
    # below, which doesn't explain that this was a deliberate choice.
    engineering_setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project.id).first()
    if (
        engineering_setup is not None
        and engineering_setup.repository_config is not None
        and engineering_setup.repository_config.option == GithubSetupOption.SKIP_FOR_NOW
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot start — this project's engineering setup skipped GitHub. Connect a repository "
            "(Settings → Integrations → GitHub, or update the engineering setup) before running Implementation.",
        )

    # Requirement 1, gate 3 — a GitHub repo snapshot exists. Not an
    # artifact type, so not part of required_inputs; resolved the same way
    # preview_repo_context already does.
    repository = _resolve_repository_for_task(db, project, task)
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start — connect a GitHub repository first.")
    snapshot = (
        db.query(RepositorySnapshot)
        .filter(RepositorySnapshot.repository_id == repository.id)
        .order_by(RepositorySnapshot.created_at.desc())
        .first()
    )
    if snapshot is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot start — create a repository snapshot first.")

    # Story Code Implementation Agent, precondition 4 — "Code runner
    # workspace can be created." A cheap, non-destructive check: the
    # configured workspace root must itself be creatable/writable. This
    # does NOT create this run's own per-run workspace yet — that only
    # happens once a human has accepted the drafted patch (see
    # app/services/code_runner.py's CodeRunnerService.create_workspace,
    # called from the separate apply-via-code-runner action below).
    try:
        get_settings().CODE_RUNNER_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot start — the code runner workspace root isn't writable: {exc}") from exc

    try:
        github_token = decrypt_repository_token(repository)
    except HTTPException:
        github_token = None  # degrades to summary-only context, same as preview_repo_context

    run = ImplementationRun(
        project_id=project.id,
        implementation_task_id=task.id,
        repository_snapshot_id=snapshot.id,
        triggered_by_user_id=triggered_by.id,
        story_id=story_id,
        lane_id=lane_id,
        story_lld_artifact_id=story_lld_artifact_id,
        assigned_user_id=triggered_by.id,
        agent_type=task.assigned_agent_type,
        assigned_agent_key=task.assigned_agent_type,
        status=ImplementationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    task.status = ImplementationTaskStatus.IN_PROGRESS
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.started",
        entity_type="ImplementationRun", entity_id=run.id,
        extra_data={
            "implementation_task_id": str(task.id), "area": task.area.value, "agent_type": task.assigned_agent_type,
            "story_id": str(story_id) if story_id else None, "lane_id": str(lane_id) if lane_id else None,
        },
    )

    try:
        repo_context = RepoContextBuilderService(db).build(task=task, snapshot=snapshot, github_token=github_token)
        standards_chunks = _fetch_standards_chunks(db, project, task)
        engineering_setup = _fetch_engineering_setup_context(db, project)
        if engineering_setup.snapshot:
            run.engineering_setup_context_snapshot = engineering_setup.snapshot
        result = run_implementation_agent(
            task=task, repo_context=repo_context, story=story, lld_summary=lld_summary,
            standards_chunks=standards_chunks, jira_issue_key=jira_issue_key,
            implementation_plan_summary=implementation_plan_summary, test_scenarios_summary=test_scenarios_summary,
            engineering_setup_context=engineering_setup.context_text,
            prior_story_task_context=prior_story_task_context,
        )
    except Exception as exc:  # noqa: BLE001 — anything unexpected fails this run cleanly, never a bare 500
        run.status = ImplementationRunStatus.FAILED
        run.error_message = str(exc)
        run.completed_at = datetime.now(timezone.utc)
        task.status = ImplementationTaskStatus.PENDING  # retryable
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.failed",
            entity_type="ImplementationRun", entity_id=run.id, extra_data={"error": str(exc)},
        )
        db.commit()
        db.refresh(run)
        return ImplementationRunRead.from_orm_run(run)

    run.proposed_file_changes = [
        {"path": c.path, "change_type": c.change_type, "summary": c.summary, "after_content": c.after_content}
        for c in result.proposed_file_changes
    ]
    run.diff_text = result.diff_text
    run.explanation = result.explanation
    run.test_command = result.test_command
    run.risks = result.risks
    run.pr_description = result.pr_description
    run.used_mock = result.used_mock
    run.token_usage = {
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.total_tokens,
    }
    run.cost = result.cost
    run.status = ImplementationRunStatus.COMPLETED
    run.review_status = ImplementationRunReviewStatus.PENDING_REVIEW
    run.completed_at = datetime.now(timezone.utc)
    # task.status stays IN_PROGRESS — it only reaches COMPLETED once a PR
    # is actually created and merged (still outside this app's control —
    # merging is a separate, human GitHub action this codebase never takes).

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.completed",
        entity_type="ImplementationRun", entity_id=run.id,
        extra_data={
            "implementation_task_id": str(task.id), "used_mock": result.used_mock,
            "file_count": len(result.proposed_file_changes), "token_usage": run.token_usage,
        },
    )

    db.commit()
    db.refresh(run)
    return ImplementationRunRead.from_orm_run(run)


@router.get("/{run_id}", response_model=ImplementationRunRead)
def get_implementation_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> ImplementationRunRead:
    run = _get_run_or_404(db, run_id)
    return ImplementationRunRead.from_orm_run(run, pull_request=_get_pull_request_link(db, run.id))


@router.post("/{run_id}/review", response_model=ImplementationRunRead)
def review_implementation_run(
    run_id: uuid.UUID, payload: ReviewImplementationRunRequest, db: Session = Depends(get_db)
) -> ImplementationRunRead:
    run = _get_run_or_404(db, run_id)
    if run.status != ImplementationRunStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Run {run_id} is {run.status.value}, not COMPLETED — nothing to review yet.")

    reviewer = db.get(User, payload.reviewed_by_user_id)
    if reviewer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reviewed_by_user_id {payload.reviewed_by_user_id} does not match an existing user")

    run.review_status = (
        ImplementationRunReviewStatus.ACCEPTED if payload.decision == "ACCEPTED" else ImplementationRunReviewStatus.REJECTED
    )
    run.reviewed_by_user_id = reviewer.id
    run.reviewed_at = datetime.now(timezone.utc)
    run.review_comment = payload.comment

    if payload.decision == "ACCEPTED":
        # Full-stack-per-story sequencing (see start_implementation_run's
        # own gate below): a story-scoped task's status flips to COMPLETED
        # here — the one signal _current_story_task (app/api/routes/
        # stories.py) and this run's own sequencing gate both read to know
        # this area is done and the next one in order may start.
        task = db.get(ImplementationTask, run.implementation_task_id)
        if task is not None:
            task.status = ImplementationTaskStatus.COMPLETED

    # SECURITY / SCOPE: neither branch below touches GitHub in any way —
    # see module docstring. Accepting only records a human's sign-off; a
    # real write only ever happens via create_pull_request below.
    record_audit_log(
        db, project_id=run.project_id, actor_user_id=reviewer.id,
        action="implementation_run.accepted" if payload.decision == "ACCEPTED" else "implementation_run.rejected",
        entity_type="ImplementationRun", entity_id=run.id, extra_data={"comment": payload.comment},
    )

    db.commit()
    db.refresh(run)
    return ImplementationRunRead.from_orm_run(run)


# --- Create PR from an accepted run (requirements 1-8 of the GitHub branch/PR task) ---


def _pr_body(*, project, task, run: ImplementationRun) -> str:
    risk_lines = [f"- {r}" for r in run.risks] if run.risks else ["- (none noted)"]
    parts = [
        run.explanation or "(no explanation provided)",
        "",
        f"**Test command:** `{run.test_command}`" if run.test_command else "",
        "",
        "**Risks:**",
        *risk_lines,
        "",
        "---",
        f"_Opened by the Implementation Agent — project `{project.id}`, task `{task.id}`, run `{run.id}`._",
    ]
    return "\n".join(p for p in parts if p is not None and p != "")


def _push_run_onto_existing_pr(
    db: Session, *, run: ImplementationRun, task: ImplementationTask, project: Project, existing: PullRequestLink, triggered_by: User,
) -> ImplementationRunRead:
    """A regenerated, re-Accepted run for a task that already has an open
    PR: push this run's changes as more commits onto that SAME branch —
    never a second branch/PR — then point `existing` at this run (so "the
    open PR" always reflects the latest accepted regeneration) and leave
    everything else about the row (pr_number, pr_url, branch_name)
    untouched, since it's still the same PR. A short comment on the PR
    itself records that a regeneration landed and why, so the PR's own
    timeline stays honest about what changed and when — same
    never-silent principle as every other write in this module."""
    repository = db.get(Repository, existing.repository_id)
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"PR #{existing.pr_number}'s repository no longer exists.")
    try:
        github_token = decrypt_repository_token(repository)
    except HTTPException as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot update the pull request — " + str(exc.detail)) from exc

    commit_message = f"Regenerate: {task.title} (run {str(run.id)[:8]})"
    try:
        for change in run.proposed_file_changes:
            path, change_type = change["path"], change["change_type"]
            if change_type == "delete":
                sha = github_api.get_file_sha(github_token, repository.owner, repository.name, path, existing.branch_name)
                if sha is None:
                    continue  # nothing to delete — already absent on this branch
                github_api.delete_file(
                    github_token, repository.owner, repository.name, path,
                    message=commit_message, branch=existing.branch_name, sha=sha,
                )
            else:
                sha = github_api.get_file_sha(github_token, repository.owner, repository.name, path, existing.branch_name)
                github_api.create_or_update_file(
                    github_token, repository.owner, repository.name, path,
                    content=change.get("after_content") or "", message=commit_message, branch=existing.branch_name, sha=sha,
                )
        github_api.create_issue_comment(
            github_token, repository.owner, repository.name, existing.pr_number,
            body=(
                f"**Regenerated by the Implementation Agent** — run `{run.id}`.\n\n"
                f"{run.explanation or '(no explanation provided)'}\n\n"
                "This pushed updated commits onto this same PR rather than opening a new one."
            ),
        )
    except GitHubIntegrationError as exc:
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.pr_update_failed",
            entity_type="ImplementationRun", entity_id=run.id,
            extra_data={"pr_number": existing.pr_number, "branch_name": existing.branch_name, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to update the existing pull request: {exc}") from exc

    existing.implementation_run_id = run.id
    # Full-stack-per-story: this push may come from a different sibling
    # task than whichever one originally opened this PR (e.g. the
    # BACKEND task pushing onto a PR the DATABASE task opened) — the link
    # always reflects whichever task's run most recently landed on it.
    existing.implementation_task_id = task.id
    existing.commit_message = commit_message
    db.flush()

    # SECURITY: never the token — only non-secret PR metadata.
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.pr_updated",
        entity_type="PullRequestLink", entity_id=existing.id,
        extra_data={
            "owner": repository.owner, "name": repository.name, "branch_name": existing.branch_name,
            "pr_number": existing.pr_number, "pr_url": existing.pr_url,
        },
    )

    db.commit()
    db.refresh(run)
    db.refresh(existing)
    return ImplementationRunRead.from_orm_run(run, pull_request=existing)


@router.post("/{run_id}/create-pull-request", response_model=ImplementationRunRead, status_code=status.HTTP_201_CREATED)
def create_pull_request(
    run_id: uuid.UUID, payload: CreatePullRequestRequest, db: Session = Depends(get_db)
) -> ImplementationRunRead:
    run = _get_run_or_404(db, run_id)
    task = db.get(ImplementationTask, run.implementation_task_id)
    if task is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Run {run_id}'s implementation task no longer exists.")
    project = db.get(Project, run.project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Run {run_id}'s project no longer exists.")

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    # HARDENING FIX: a story-scoped task (task.story_id set) belongs to
    # that story's own StoryDeliveryNode-based lane, not the project-level
    # WorkflowNode graph — there is no WorkflowNode row with node_key=
    # "implementation" and story_id=task.story_id for it to find (lanes
    # are materialized entirely in StoryDeliveryNode, a different table;
    # see app/services/story_delivery.py). The unconditional lookup this
    # used to be always returned None for a story-scoped task, so
    # create_pull_request 400'd on every single story-level PR — same
    # branch-once convention start_implementation_run above already
    # established for exactly this reason.
    implementation_node = None
    if task.story_id is None:
        implementation_node = (
            db.query(WorkflowNode)
            .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "implementation", WorkflowNode.story_id.is_(None))
            .first()
        )
        if implementation_node is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no implementation stage.")
    require_can_edit_stage(triggered_by, "implementation")

    # RULE: user must approve the patch before PR creation.
    if run.status != ImplementationRunStatus.COMPLETED or run.review_status != ImplementationRunReviewStatus.ACCEPTED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run {run_id} must be COMPLETED and ACCEPTED before a pull request can be created "
            f"(currently status={run.status.value}, review_status={run.review_status.value}).",
        )
    if _get_pull_request_link(db, run.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A pull request already exists for run {run_id}.")

    # Every run against this task is the same piece of work regenerated,
    # not a separate change — and for a story-scoped task, every sibling
    # task of the same story (full-stack-per-story — see
    # _get_open_task_pull_request's own docstring) is really one piece of
    # work too. If an earlier run (this task's own, or an earlier sibling
    # task's) already opened a PR that's still OPEN, this Accepted run's
    # changes land as more commits on THAT SAME PR, not a second,
    # competing one — see _push_run_onto_existing_pr below. A run whose
    # task/story has no open PR yet (first run ever, or the prior PR was
    # merged/closed) falls through to the existing create-a-new-PR flow
    # unchanged.
    existing_task_pr = _get_open_task_pull_request(db, task)
    if existing_task_pr is not None:
        return _push_run_onto_existing_pr(db, run=run, task=task, project=project, existing=existing_task_pr, triggered_by=triggered_by)

    # MULTI-REPO CORRECTNESS: the PR must land in the exact repository the
    # run's proposed changes were generated against — not "the project's
    # current latest/primary repo," which can now be a different
    # repository if a second one was connected (or the primary changed)
    # after this run's snapshot was taken. run.repository_snapshot_id is
    # always set at run creation (start_implementation_run requires a
    # snapshot to exist), so this is authoritative; _resolve_repository_for_task
    # is only a defensive fallback for the pathological case of a run
    # whose snapshot was somehow deleted since.
    repository = run.repository_snapshot.repository if run.repository_snapshot is not None else None
    if repository is None:
        repository = _resolve_repository_for_task(db, project, task)
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot create a pull request — no GitHub repository is configured.")

    try:
        github_token = decrypt_repository_token(repository)
    except HTTPException as exc:
        # Unlike the run-generation path, a real write cannot silently
        # degrade to "no token" — surface the failure plainly.
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot create a pull request — " + str(exc.detail)) from exc

    # Project Engineering Setup rule 8 — "PR creation requires branch
    # naming pattern and target branch." When a repository_config exists
    # (from the Create Project wizard's Step 3), its own pattern/target
    # branch drive PR creation instead of the hardcoded default below —
    # payload.base_branch still wins if the caller passed one explicitly,
    # same precedence as before this feature. A project with no
    # engineering setup (or a repository_config-less one) keeps today's
    # exact behavior (rule 10).
    engineering_setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project.id).first()
    repo_config = engineering_setup.repository_config if engineering_setup is not None else None
    branch_naming_pattern = repo_config.branch_naming_pattern if repo_config is not None else "agent/{task}-{run_id}"
    configured_target_branch = repo_config.target_branch if repo_config is not None else None

    base_branch = payload.base_branch or configured_target_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    try:
        branch_name = branch_naming_pattern.format(task=_slugify(task.title), run_id=str(run.id)[:8], story=_slugify(task.linked_story or ""))
    except (KeyError, IndexError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This project's branch naming pattern ({branch_naming_pattern!r}) uses a placeholder other than "
            "{task}, {run_id}, or {story} — fix it in the project's engineering setup before creating a pull request.",
        )
    # RULE: never push to main — defense in depth; unreachable by
    # construction since branch_name always includes a run-id suffix, but
    # checked explicitly rather than trusted implicitly.
    if branch_name == repository.default_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Refusing to use the repository's default branch as the feature branch.")

    project_key = _derive_project_key(project.name)
    commit_message = f"[{project_key}] {task.title}"
    pr_title = f"[{project_key}] {task.title}"[:255]

    try:
        github_api.create_branch(github_token, repository.owner, repository.name, new_branch=branch_name, base_ref=base_branch)
        for change in run.proposed_file_changes:
            path, change_type = change["path"], change["change_type"]
            if change_type == "delete":
                sha = github_api.get_file_sha(github_token, repository.owner, repository.name, path, branch_name)
                if sha is None:
                    continue  # nothing to delete — already absent on this branch
                github_api.delete_file(
                    github_token, repository.owner, repository.name, path,
                    message=commit_message, branch=branch_name, sha=sha,
                )
            else:
                sha = github_api.get_file_sha(github_token, repository.owner, repository.name, path, branch_name)
                github_api.create_or_update_file(
                    github_token, repository.owner, repository.name, path,
                    content=change.get("after_content") or "", message=commit_message, branch=branch_name, sha=sha,
                )
        pr = github_api.create_pull_request(
            github_token, repository.owner, repository.name,
            title=pr_title, head=branch_name, base=base_branch, body=_pr_body(project=project, task=task, run=run),
        )
    except GitHubIntegrationError as exc:
        # SCOPE: the branch (and any commits already made to it) may still
        # exist on GitHub — no automatic rollback. Recorded so a human can
        # find and clean it up; no PullRequestLink is persisted.
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.pr_creation_failed",
            entity_type="ImplementationRun", entity_id=run.id, extra_data={"branch_name": branch_name, "error": str(exc)},
        )
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub pull request creation failed: {exc}") from exc

    link = PullRequestLink(
        project_id=project.id,
        workflow_node_id=implementation_node.id if implementation_node is not None else None,
        implementation_task_id=task.id,
        implementation_run_id=run.id,
        repository_id=repository.id,
        story_id=task.story_id,
        branch_name=branch_name,
        base_branch=base_branch,
        pr_number=pr.number,
        pr_url=pr.html_url,
        created_by_agent=True,
        triggered_by_user_id=triggered_by.id,
        commit_message=commit_message,
    )
    db.add(link)
    db.flush()

    # SECURITY: never the token — only non-secret PR metadata.
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_run.pr_created",
        entity_type="PullRequestLink", entity_id=link.id,
        extra_data={
            "owner": repository.owner, "name": repository.name, "branch_name": branch_name, "base_branch": base_branch,
            "pr_number": pr.number, "pr_url": pr.html_url,
        },
    )

    db.commit()
    db.refresh(run)
    db.refresh(link)
    return ImplementationRunRead.from_orm_run(run, pull_request=link)
