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

from dataclasses import dataclass, field
from datetime import timezone

from sqlalchemy.orm import Session

from app.models import AgentRun, AgentRunStatus, Review, ReviewStatus, WorkflowNode, WorkflowStatus

# How many rows to return for the two run-level lists — enough to be
# useful without the dashboard payload growing unbounded as history piles up.
RECENT_RUNS_LIMIT = 25
RECENT_FAILURES_LIMIT = 25


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


@dataclass
class OpsSummary:
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    avg_duration_seconds: float | None = None
    total_tokens: int = 0
    total_cost: float = 0.0
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

    durations = [d for r in runs if (d := _duration_seconds(r)) is not None]
    summary.avg_duration_seconds = round(sum(durations) / len(durations), 2) if durations else None

    summary.recent_runs = [_to_row(r) for r in runs[:RECENT_RUNS_LIMIT]]
    summary.recent_failures = [_to_row(r) for r in runs if r.status == AgentRunStatus.FAILED][:RECENT_FAILURES_LIMIT]

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

    summary.blocked_workflow_count = (
        db.query(WorkflowNode).filter(WorkflowNode.status == WorkflowStatus.BLOCKED).count()
    )

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
        stage_performance.append(
            StagePerformance(
                node_key=node_key,
                stage_name=stage_name,
                total_runs=stage_total,
                successful_runs=stage_success,
                failed_runs=stage_failed,
                success_rate=round(stage_success / stage_total, 4) if stage_total > 0 else None,
                avg_duration_seconds=round(sum(stage_durations) / len(stage_durations), 2) if stage_durations else None,
            )
        )
    summary.stage_performance = stage_performance

    return summary
