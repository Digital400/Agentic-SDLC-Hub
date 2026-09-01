"""Enums shared across models.

`WorkflowStatus` and `WorkflowAction` intentionally mirror
`packages/shared/src/workflow.ts` — keep the two in sync if either changes.
"""

import enum


class WorkflowStatus(str, enum.Enum):
    """A workflow node instance's state in the graph engine — see
    app/services/graph_engine.py's GraphEngineService, which is the only
    code that should transition a node between these.

    Replaces an earlier, smaller set (NOT_STARTED/IN_PROGRESS/REJECTED are
    gone) — see the graph-engine migration for how existing rows convert:
    NOT_STARTED -> LOCKED, IN_PROGRESS -> READY, REJECTED -> BLOCKED.
    """

    LOCKED = "LOCKED"  # prerequisites not yet satisfied; cannot run
    READY = "READY"  # prerequisites satisfied; not yet run
    RUNNING = "RUNNING"  # an agent run is currently executing for this node
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"  # agent asked a clarifying question
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"  # artifact submitted, review pending
    APPROVED = "APPROVED"  # reviewer approved — a satisfied predecessor for unlocking
    NEEDS_CHANGES = "NEEDS_CHANGES"  # reviewer asked for changes; rework, then resubmit
    BLOCKED = "BLOCKED"  # halted — a rejected review, or any other hard stop; see blocked_reason
    FAILED = "FAILED"  # the agent run itself errored out (not a review outcome)
    SKIPPED = "SKIPPED"  # deliberately bypassed (manual override) — a satisfied predecessor too
    COMPLETED = "COMPLETED"  # terminal success for a stage with no approval gate


class WorkflowAction(str, enum.Enum):
    DRAFT = "draft"
    EDIT = "edit"
    SUBMIT_FOR_REVIEW = "submit_for_review"
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"
    COMPLETE = "complete"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class ProjectRole(str, enum.Enum):
    """A user's role within a single project (not a global/system role)."""

    OWNER = "OWNER"
    CONTRIBUTOR = "CONTRIBUTOR"
    APPROVER = "APPROVER"
    VIEWER = "VIEWER"


class ArtifactStatus(str, enum.Enum):
    """An artifact's own review lifecycle — distinct from WorkflowStatus,
    which tracks the broader lifecycle of the WorkflowNode that owns it
    (which also covers non-artifact states like NOT_STARTED/BLOCKED)."""

    DRAFT = "DRAFT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    REJECTED = "REJECTED"


class ReviewStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    REJECTED = "REJECTED"


class AgentPromptRole(str, enum.Enum):
    """The three roles agents may play, per the product's AI agent principle."""

    DRAFT = "draft"
    IMPROVE = "improve"
    VALIDATE = "validate"


class AgentRunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ApprovalRecommendation(str, enum.Enum):
    """A validator agent's own recommendation — see
    app/services/validator_agent.py — distinct from an actual human
    ReviewStatus decision: this is advisory input a reviewer sees, not a
    decision by itself."""

    APPROVE = "APPROVE"
    REVISE = "REVISE"
    REJECT = "REJECT"


class LoopStepType(str, enum.Enum):
    """One step of an agent run's self-improvement loop — see
    app/services/loop_engine.py's LoopEngineService, the only code that
    should drive a run through these. PLAN and RETRIEVE_CONTEXT happen once
    per run; GENERATE_DRAFT/VALIDATE/IMPROVE repeat as an iteration cycle
    (GENERATE_DRAFT only on iteration 1, IMPROVE on every iteration after)
    until a stop condition is met; READY_FOR_REVIEW is always the loop's
    final step, win or lose."""

    PLAN = "PLAN"
    RETRIEVE_CONTEXT = "RETRIEVE_CONTEXT"
    GENERATE_DRAFT = "GENERATE_DRAFT"
    VALIDATE = "VALIDATE"
    IMPROVE = "IMPROVE"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"


class LoopStatus(str, enum.Enum):
    """Why an agent run's loop is in its current state — see
    LoopEngineService's module docstring for the exact stop conditions."""

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    # qualityScore >= threshold was reached.
    COMPLETED_QUALITY_MET = "COMPLETED_QUALITY_MET"
    # maxIterations was reached before quality threshold was met.
    COMPLETED_MAX_ITERATIONS = "COMPLETED_MAX_ITERATIONS"
    # Below threshold, but validation found no critical issues to improve —
    # looping further wouldn't be acting on anything, so it stops early.
    COMPLETED_NO_CRITICAL_ISSUES = "COMPLETED_NO_CRITICAL_ISSUES"
    # The draft asked a clarification question — nothing to validate/improve
    # until a human answers it, so the loop stops immediately.
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    FAILED = "FAILED"


class KnowledgeSourceType(str, enum.Enum):
    """Where a knowledge source's content originally came from."""

    PROJECT_ARTIFACT = "PROJECT_ARTIFACT"
    UPLOADED_DOCUMENT = "UPLOADED_DOCUMENT"
    EXTERNAL_LINK = "EXTERNAL_LINK"


class IntegrationProvider(str, enum.Enum):
    """External systems agents will eventually reach through MCP tools —
    see docs/architecture.md's MCP integrations section. Fixed set for now;
    revisit as a free-text field if/when integrations become pluggable."""

    JIRA = "JIRA"
    CONFLUENCE = "CONFLUENCE"
    GITHUB = "GITHUB"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    AZURE_DEVOPS = "AZURE_DEVOPS"


class IntegrationStatus(str, enum.Enum):
    """An integration's connection lifecycle. Every row is created
    NOT_CONNECTED today — no real MCP tool is wired up yet (see
    docs/architecture.md) — but the full lifecycle is modeled now so
    connecting one later doesn't require a schema change."""

    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


class UserRole(str, enum.Enum):
    """Global, coarse-grained functional role — see
    app/services/permissions.py for what each role may actually do. One
    role per user, not per-project; promote to a per-project assignment
    later if that's ever needed (nothing here assumes global-only)."""

    ADMIN = "ADMIN"
    BA = "BA"
    PRODUCT_OWNER = "PRODUCT_OWNER"
    ARCHITECT = "ARCHITECT"
    TECH_LEAD = "TECH_LEAD"
    DEVELOPER = "DEVELOPER"
    QA = "QA"
    DEVOPS = "DEVOPS"
    VIEWER = "VIEWER"


class KnowledgeSourceStatus(str, enum.Enum):
    """A source's ingestion lifecycle. No ingestion pipeline exists yet
    (see KnowledgeChunk's docstring) — sources are created straight into
    PENDING and stay there until that pipeline is built."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class KnowledgeContentType(str, enum.Enum):
    """What KIND of guidance a chunk represents — a chunk-level tag,
    distinct from KnowledgeSource.source_type (which tracks WHERE the
    content came from: an upload, a link, or a project artifact). Maps to
    the "sourceType" chunk-metadata requirement in the RAG improvement
    spec; named `content_type` here to avoid colliding with
    KnowledgeSource's own, differently-scoped `source_type` column.

    This is the fixed set RAG is required to support explicitly — see
    app/services/retrieval.py's module docstring."""

    COMPANY_STANDARD = "COMPANY_STANDARD"
    PAST_ARTIFACT = "PAST_ARTIFACT"
    UI_GUIDELINE = "UI_GUIDELINE"
    ARCHITECTURE_RULE = "ARCHITECTURE_RULE"
    TESTING_STANDARD = "TESTING_STANDARD"
    OTHER = "OTHER"


class ImplementationTaskArea(str, enum.Enum):
    """Which part of the system an ImplementationTask belongs to — see
    app/models/implementation_task.py. Fixed set per the Implementation
    Planner's spec; each maps 1:1 to an `assigned_agent_type` string
    (see app/services/implementation_planner.py's AREA_TO_AGENT_TYPE)."""

    BACKEND = "BACKEND"
    FRONTEND = "FRONTEND"
    DATABASE = "DATABASE"
    TESTING = "TESTING"
    INFRA = "INFRA"
    DOCS = "DOCS"


class ImplementationTaskRiskLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ImplementationTaskStatus(str, enum.Enum):
    """A single task's own lifecycle — distinct from WorkflowStatus (the
    owning Implementation Planning *stage*'s lifecycle) and ArtifactStatus
    (the plan document's own review lifecycle). No coding agent exists yet
    to advance a task past PENDING (see app/services/implementation_planner.py's
    module docstring) — the full lifecycle is modeled now so wiring one up
    later doesn't require a schema change, same reasoning as
    IntegrationStatus."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class ImplementationRunStatus(str, enum.Enum):
    """One Implementation Agent execution's own lifecycle — see
    app/models/implementation_run.py. Deliberately separate from
    AgentRunStatus: this run isn't keyed to a WorkflowNode/AgentPrompt
    role, so it doesn't share AgentRun's shape, just the same PENDING/
    RUNNING/COMPLETED/FAILED vocabulary every run-like model in this
    codebase uses."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ImplementationRunReviewStatus(str, enum.Enum):
    """A human's accept/reject decision on a COMPLETED run's proposed
    diff — see app/models/implementation_run.py's class docstring for why
    this is a full status of its own rather than piggybacking on the
    generic Review/ArtifactStatus machinery: no artifact exists for this
    output. Neither Accept nor Reject itself ever touches GitHub — only
    once ACCEPTED does a separate, explicit action become available
    (create a PR; see PullRequestLink and
    app/api/routes/implementation_runs.py's create_pull_request), and even
    then it only ever writes to a newly created feature branch, never the
    repository's default branch."""

    PENDING_REVIEW = "PENDING_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class PullRequestStatus(str, enum.Enum):
    """A GitHub pull request's own state, mirrored at creation time — see
    app/models/pull_request_link.py. No webhook exists to keep this in
    sync after creation, so it starts OPEN and stays whatever it was last
    set to; that's a disclosed gap, not a faked live sync."""

    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class TestRunStatus(str, enum.Enum):
    """One Testing Agent execution's own lifecycle — see
    app/models/test_run.py. Same PENDING/RUNNING/COMPLETED/FAILED
    vocabulary as ImplementationRunStatus/AgentRunStatus."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TestAgentType(str, enum.Enum):
    """Which of the five Testing Agent types produced a TestRun — see
    app/services/testing_agent.py. A human picks one when starting a run;
    nothing here infers it from ImplementationTask.area (a different
    axis — what part of the system vs. what kind of testing)."""

    UNIT = "UNIT"
    API = "API"
    UI = "UI"
    REGRESSION = "REGRESSION"
    SECURITY = "SECURITY"


class PRReviewRunStatus(str, enum.Enum):
    """One PR Review Agent execution's own lifecycle — see
    app/models/pr_review_run.py. Same PENDING/RUNNING/COMPLETED/FAILED
    vocabulary as every other run-like model in this codebase."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PRReviewRecommendation(str, enum.Enum):
    """A PRReviewRun's overall recommendation — never a merge decision.
    'Human reviewer decides final approval' happens on the real GitHub PR
    itself; this is advisory input to that human, not a gate this app
    enforces."""

    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT_ONLY = "COMMENT_ONLY"


class JiraSourceType(str, enum.Enum):
    """Which internal entity kind a JiraIssueLink was created from — see
    app/models/jira_issue_link.py. Epics have no dedicated model in this
    codebase (see Story.epic) — an EPIC link's source_key is the raw epic
    name string, grouped from stories at preview/push time."""

    EPIC = "EPIC"
    STORY = "STORY"
    IMPLEMENTATION_TASK = "IMPLEMENTATION_TASK"
    TESTING_BUG = "TESTING_BUG"


class MaintenanceRunStatus(str, enum.Enum):
    """One Maintenance Agent execution's own lifecycle — see
    app/models/maintenance_run.py. Same PENDING/RUNNING/COMPLETED/FAILED
    vocabulary as every other run-like model in this codebase."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RepositoryFileEntryType(str, enum.Enum):
    """One entry in a RepositorySnapshot's file index — see
    app/models/repository.py's RepositoryFileIndex. Mirrors GitHub's own
    Git Trees API entry `type` field ("blob"/"tree"), renamed to something
    self-explanatory outside a Git-internals context."""

    FILE = "FILE"
    DIRECTORY = "DIRECTORY"
