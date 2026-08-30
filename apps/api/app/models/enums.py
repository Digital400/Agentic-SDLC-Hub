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
