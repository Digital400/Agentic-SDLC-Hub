"""Cross-project AI Ops metrics — powers GET /ops/summary.

Aggregates across every project rather than one at a time, since this
dashboard's whole point is company-wide visibility into agent activity and
the human approval gate (see docs/product-vision.md's human-in-the-loop
principle) — a per-project view already exists on the Project Workspace
page. Computed in Python rather than pushed into SQL aggregates: at this
project's current scale (a handful of projects, dozens of runs) that's
simpler and easier to reason about than JSON-path aggregate expressions,
and can be revisited if the data volume ever makes that matter.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import timezone

from sqlalchemy.orm import Session

from app.models import (
    AgentRun,
    AgentRunLoopEvent,
    AgentRunStatus,
    LoopStatus,
    LoopStepType,
    Review,
    ReviewStatus,
    WorkflowNode,
    WorkflowStatus,
)

# How many rows to return for the two run-level lists — enough to be
# useful without the dashboard payload growing unbounded as history piles up.
RECENT_RUNS_LIMIT = 25
RECENT_FAILURES_LIMIT = 25
# How many rows for the two "most common" leaderboards — a top-10 is what
# a tech lead actually scans; the long tail isn't worth the payload.
TOP_N_LIMIT = 10


@dataclass
class AgentRunRow:
    id: str
    project_id: str
    project_name: str
    workflow_stage_name: str
    agent_key: str
    action: str
    status: str
    duration_seconds: float | None
    total_tokens: int | None
    cost: float | None
    error_message: str | None
    created_at: str


@dataclass
class StagePerformance:
    node_key: str
    stage_name: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float | None  # None when this stage has had zero runs
    avg_duration_seconds: float | None
    # Added for the AI Ops dashboard upgrade — see OpsSummary's own
    # company-wide equivalents for what "None" means in each case.
    total_cost: float = 0.0
    avg_quality_score: float | None = None
    avg_iterations: float | None = None


@dataclass
class CostByProject:
    project_id: str
    project_name: str
    total_cost: float
    run_count: int


@dataclass
class ValidationIssueFrequency:
    """One distinct critical-issue message a validator agent raised (see
    app/services/validator_agent.py), and how many VALIDATE steps raised
    it — across every iteration of every run, not just each run's final
    verdict, since a recurring issue across many drafts is exactly the
    signal a tech lead wants (e.g. "prompts keep missing risk coverage")."""

    message: str
    count: int


@dataclass
class RagSourceUsage:
    """One Knowledge Base source and how many agent runs actually cited
    it (see AgentRun.retrieved_sources) — which sources are pulling their
    weight vs. sitting unused."""

    source_title: str
    count: int


@dataclass
class BlockedWorkflowRow:
    project_id: str
    project_name: str
    node_key: str
    stage_name: str
    blocked_reason: str | None
    updated_at: str


@dataclass
class OpsSummary:
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    # None only when there have been zero runs at all — with at least one
    # run, both rates are always computable (they're complements of each
    # other only when every run resolves to COMPLETED/FAILED, which isn't
    # guaranteed — a run can also be PENDING/RUNNING mid-flight).
    success_rate: float | None = None
    failure_rate: float | None = None
    avg_duration_seconds: float | None = None
    total_tokens: int = 0
    # Average of token_usage.total_tokens across runs that actually have a
    # token count (a run that failed before generation has none) — a
    # straight total_tokens/total_runs would understate the average by
    # diluting it with runs that never got that far.
    avg_tokens_per_run: float | None = None
    total_cost: float = 0.0
    # Average of the latest VALIDATE step's quality_score (see
    # app/services/validator_agent.py) across every run that reached one —
    # None until at least one DRAFT run has gone through the Loop Engine.
    avg_quality_score: float | None = None
    # Decided-review rates — PENDING reviews are excluded from the
    # denominator since they haven't resolved one way or the other yet.
    approval_rate: float | None = None
    rejection_rate: float | None = None
    decided_review_count: int = 0
    # Placeholder per the product spec: how often a human hand-edits an
    # agent's draft rather than re-running the agent isn't something this
    # schema can tell apart yet — ArtifactVersion has no agent-run
    # authorship link (see app/services/ai_generation.py's note on this
    # same gap), so there's no reliable signal to compute a real rate from.
    # Surfaced as None with an explanatory note rather than a fabricated
    # number — the UI renders this as "Not tracked yet".
    human_change_rate: None = None
    human_change_rate_note: str = (
        "Not tracked yet — requires linking an artifact version to the agent run "
        "(or human edit) that produced it, which isn't recorded today."
    )
    blocked_workflow_count: int = 0
    recent_runs: list[AgentRunRow] = field(default_factory=list)
    recent_failures: list[AgentRunRow] = field(default_factory=list)
    stage_performance: list[StagePerformance] = field(default_factory=list)
    cost_by_project: list[CostByProject] = field(default_factory=list)
    validation_issue_frequency: list[ValidationIssueFrequency] = field(default_factory=list)
    rag_source_usage: list[RagSourceUsage] = field(default_factory=list)
    blocked_workflows: list[BlockedWorkflowRow] = field(default_factory=list)


def _duration_seconds(run: AgentRun) -> float | None:
    if run.started_at is None or run.completed_at is None:
        return None
    return (run.completed_at - run.started_at).total_seconds()


def _to_row(run: AgentRun) -> AgentRunRow:
    return AgentRunRow(
        id=str(run.id),
        project_id=str(run.project_id),
        project_name=run.project.name,
        workflow_stage_name=run.workflow_node.name,
        agent_key=run.agent_definition.agent_key,
        action=run.action.value,
        status=run.status.value,
        duration_seconds=_duration_seconds(run),
        total_tokens=(run.token_usage or {}).get("total_tokens"),
        cost=run.cost,
        error_message=run.error_message,
        created_at=run.created_at.astimezone(timezone.utc).isoformat(),
    )


def build_ops_summary(db: Session) -> OpsSummary:
    summary = OpsSummary()

    runs = db.query(AgentRun).order_by(AgentRun.created_at.desc()).all()
    summary.total_runs = len(runs)
    summary.successful_runs = sum(1 for r in runs if r.status == AgentRunStatus.COMPLETED)
    summary.failed_runs = sum(1 for r in runs if r.status == AgentRunStatus.FAILED)
    summary.total_tokens = sum((r.token_usage or {}).get("total_tokens", 0) for r in runs)
    summary.total_cost = round(sum(r.cost or 0.0 for r in runs), 6)

    if summary.total_runs > 0:
        summary.success_rate = round(summary.successful_runs / summary.total_runs, 4)
        summary.failure_rate = round(summary.failed_runs / summary.total_runs, 4)

    durations = [d for r in runs if (d := _duration_seconds(r)) is not None]
    summary.avg_duration_seconds = round(sum(durations) / len(durations), 2) if durations else None

    token_counts = [t for r in runs if (t := (r.token_usage or {}).get("total_tokens")) is not None]
    summary.avg_tokens_per_run = round(sum(token_counts) / len(token_counts), 1) if token_counts else None

    quality_scores = [r.loop_quality_score for r in runs if r.loop_quality_score is not None]
    summary.avg_quality_score = round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else None

    summary.recent_runs = [_to_row(r) for r in runs[:RECENT_RUNS_LIMIT]]
    summary.recent_failures = [_to_row(r) for r in runs if r.status == AgentRunStatus.FAILED][:RECENT_FAILURES_LIMIT]

    # Cost by project — sorted highest-spend first, since that's what a
    # budget-conscious tech lead scans for first.
    cost_by_project_totals: dict[str, dict] = {}
    for run in runs:
        key = str(run.project_id)
        entry = cost_by_project_totals.setdefault(key, {"name": run.project.name, "cost": 0.0, "count": 0})
        entry["cost"] += run.cost or 0.0
        entry["count"] += 1
    summary.cost_by_project = sorted(
        (
            CostByProject(project_id=pid, project_name=v["name"], total_cost=round(v["cost"], 6), run_count=v["count"])
            for pid, v in cost_by_project_totals.items()
        ),
        key=lambda c: c.total_cost,
        reverse=True,
    )

    # Most common validation issues — across every VALIDATE step of every
    # loop iteration (not just each run's final verdict), so a
    # recurring-but-eventually-fixed issue still shows up as recurring.
    validate_events = db.query(AgentRunLoopEvent).filter(AgentRunLoopEvent.step == LoopStepType.VALIDATE).all()
    issue_counts: Counter[str] = Counter()
    for event in validate_events:
        issue_counts.update(event.validation_issues or [])
    summary.validation_issue_frequency = [
        ValidationIssueFrequency(message=message, count=count)
        for message, count in issue_counts.most_common(TOP_N_LIMIT)
    ]

    # Most used RAG sources — which Knowledge Base sources agent runs
    # actually cited (see app/services/retrieval.py), across every run.
    source_counts: Counter[str] = Counter()
    for run in runs:
        source_counts.update(entry["source_title"] for entry in (run.retrieved_sources or []))
    summary.rag_source_usage = [
        RagSourceUsage(source_title=title, count=count) for title, count in source_counts.most_common(TOP_N_LIMIT)
    ]

    decided_reviews = (
        db.query(Review.status).filter(Review.status != ReviewStatus.PENDING).all()
    )
    decided_count = len(decided_reviews)
    summary.decided_review_count = decided_count
    if decided_count > 0:
        approved = sum(1 for (s,) in decided_reviews if s == ReviewStatus.APPROVED)
        rejected = sum(1 for (s,) in decided_reviews if s == ReviewStatus.REJECTED)
        summary.approval_rate = round(approved / decided_count, 4)
        summary.rejection_rate = round(rejected / decided_count, 4)

    blocked_nodes = db.query(WorkflowNode).filter(WorkflowNode.status == WorkflowStatus.BLOCKED).all()
    summary.blocked_workflow_count = len(blocked_nodes)
    summary.blocked_workflows = [
        BlockedWorkflowRow(
            project_id=str(n.project_id),
            project_name=n.project.name,
            node_key=n.node_key,
            stage_name=n.name,
            blocked_reason=n.blocked_reason,
            updated_at=n.updated_at.astimezone(timezone.utc).isoformat(),
        )
        for n in blocked_nodes
    ]

    # Stage performance — grouped by node_key across every project's
    # workflow graph, so e.g. "hld" aggregates that stage's runs company-wide
    # rather than per-project.
    nodes = db.query(WorkflowNode).all()
    # (name, order_index) per stage key — order_index lets stages sort in
    # actual workflow order rather than alphabetically; different projects
    # generated from the same template agree on it, so the first node seen
    # for a given key is representative.
    stage_info_by_key: dict[str, tuple[str, int]] = {}
    for node in nodes:
        stage_info_by_key.setdefault(node.node_key, (node.name, node.order_index))

    runs_by_stage_key: dict[str, list[AgentRun]] = {}
    node_key_by_node_id = {node.id: node.node_key for node in nodes}
    for run in runs:
        key = node_key_by_node_id.get(run.workflow_node_id)
        if key is None:
            continue
        runs_by_stage_key.setdefault(key, []).append(run)

    stage_performance = []
    for node_key, (stage_name, _order) in sorted(stage_info_by_key.items(), key=lambda kv: kv[1][1]):
        stage_runs = runs_by_stage_key.get(node_key, [])
        stage_total = len(stage_runs)
        stage_success = sum(1 for r in stage_runs if r.status == AgentRunStatus.COMPLETED)
        stage_failed = sum(1 for r in stage_runs if r.status == AgentRunStatus.FAILED)
        stage_durations = [d for r in stage_runs if (d := _duration_seconds(r)) is not None]
        stage_quality_scores = [r.loop_quality_score for r in stage_runs if r.loop_quality_score is not None]
        # Iteration count only means something for a run that actually
        # went through the Loop Engine (see app/services/loop_engine.py) —
        # a run whose loop_status is still NOT_STARTED (non-DRAFT actions)
        # never set loop_iteration to anything meaningful.
        stage_iterations = [r.loop_iteration for r in stage_runs if r.loop_status != LoopStatus.NOT_STARTED]
        stage_performance.append(
            StagePerformance(
                node_key=node_key,
                stage_name=stage_name,
                total_runs=stage_total,
                successful_runs=stage_success,
                failed_runs=stage_failed,
                success_rate=round(stage_success / stage_total, 4) if stage_total > 0 else None,
                avg_duration_seconds=round(sum(stage_durations) / len(stage_durations), 2) if stage_durations else None,
                total_cost=round(sum(r.cost or 0.0 for r in stage_runs), 6),
                avg_quality_score=round(sum(stage_quality_scores) / len(stage_quality_scores), 4)
                if stage_quality_scores
                else None,
                avg_iterations=round(sum(stage_iterations) / len(stage_iterations), 2) if stage_iterations else None,
            )
        )
    summary.stage_performance = stage_performance

    return summary
