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


class StoryType(str, enum.Enum):
    """Which Story Crafting mode produced this story — see
    app/models/story.py and RICH_DEFAULT_PROMPTS["story_crafting"]'s
    updated prompt. VERTICAL: end-to-end user-value stories (the existing
    default shape). HORIZONTAL: technical-layer stories (frontend/backend/
    database/integration/infrastructure/testing/documentation)."""

    VERTICAL = "VERTICAL"
    HORIZONTAL = "HORIZONTAL"


class StoryStatus(str, enum.Enum):
    """A persisted Story row's own lifecycle — separate from its delivery
    lane's WorkflowStatus (which tracks the lane's graph progress once one
    exists). See app/models/story.py."""

    PENDING = "PENDING"  # persisted from an approved backlog, no lane yet
    IN_SPRINT = "IN_SPRINT"  # assigned to a Sprint
    LANE_ACTIVE = "LANE_ACTIVE"  # a delivery lane has been created
    DONE = "DONE"  # the lane reached Release Ready


class SprintStatus(str, enum.Enum):
    """A Sprint's own lifecycle — see app/models/sprint.py. Only
    PLANNED -> ACTIVE -> COMPLETED is a normal progression (via
    POST /sprints/{id}/start and /complete); CANCELLED is reachable from
    either PLANNED or ACTIVE via a manual override, not a normal flow
    step (no dedicated "cancel" endpoint — same escape-hatch spirit as
    GraphEngineService.manual_override, but no route exposes it yet)."""

    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SprintStoryStatus(str, enum.Enum):
    """One story's membership in one sprint — see
    app/models/sprint_story.py. REMOVED is a soft-delete (DELETE
    /sprints/{id}/stories/{story_id} sets this rather than deleting the
    row), so a sprint's full membership history — including what was
    pulled back out — is never lost."""

    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    REMOVED = "REMOVED"


class StoryDeliveryLaneStatus(str, enum.Enum):
    """One story's own delivery lane (see app/models/story_delivery_lane.py)
    — a dedicated, self-contained graph per story, deliberately NOT the
    project-level WorkflowNode/WorkflowEdge graph engine (see
    app/services/story_delivery.py's module docstring for why)."""

    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"


class StoryDeliveryNodeStatus(str, enum.Enum):
    """One node in a story delivery lane — see
    app/models/story_delivery_node.py. LOCKED until its predecessor
    completes; the first node in every lane starts READY."""

    LOCKED = "LOCKED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"


class RepositoryFileEntryType(str, enum.Enum):
    """One entry in a RepositorySnapshot's file index — see
    app/models/repository.py's RepositoryFileIndex. Mirrors GitHub's own
    Git Trees API entry `type` field ("blob"/"tree"), renamed to something
    self-explanatory outside a Git-internals context."""

    FILE = "FILE"
    DIRECTORY = "DIRECTORY"


class ProjectExecutionProfileType(str, enum.Enum):
    """Which of the two profile flows produced/applies to one
    ProjectExecutionProfile — see app/models/project_execution_profile.py.
    NEW_PROJECT: the user selects an approved company stack/template and a
    profile is generated from it, before any scaffolding happens.
    EXISTING_REPOSITORY: the profile is detected from a real repository's
    already-committed configuration files (see
    app/services/execution_profile_detection.py) and proposed for review."""

    NEW_PROJECT = "NEW_PROJECT"
    EXISTING_REPOSITORY = "EXISTING_REPOSITORY"


class ProjectExecutionProfileSource(str, enum.Enum):
    """How one profile's field values were derived — distinct from
    ProjectExecutionProfileType (which flow this profile belongs to):
    a NEW_PROJECT profile is always TEMPLATE-sourced; an
    EXISTING_REPOSITORY profile is normally DETECTED, but MANUAL is
    reserved for a human-edited revision of either (see
    ProjectExecutionProfileStatus — a MANUAL profile is still a brand new
    version, never an in-place edit of an approved one)."""

    DETECTED = "DETECTED"
    TEMPLATE = "TEMPLATE"
    MANUAL = "MANUAL"


class ProjectExecutionProfileStatus(str, enum.Enum):
    """One profile version's place in its own approval lifecycle — see
    app/models/project_execution_profile.py's class docstring for the
    versioning rule this backs (immutable rows, at most one APPROVED +
    is_active=True per project at a time).

    DRAFT              — detected/generated, not yet submitted for approval.
    PENDING_APPROVAL    — submitted; awaiting a Project Owner decision.
    APPROVED            — a Project Owner approved it. Exactly the state a
                          "coding runtime" gate checks for (together with
                          is_active) before any execution may start — see
                          app/services/execution_profile_gate.py.
    REJECTED            — a Project Owner rejected it; terminal, not
                          resubmittable (propose a new version instead).
    SUPERSEDED          — was APPROVED, but a newer version has since been
                          approved in its place; kept for audit history,
                          no longer active.
    """

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class DataClassification(str, enum.Enum):
    """How sensitive this project's own data/code is — informs which
    tools/network access a coding runtime should be trusted with (e.g. a
    RESTRICTED project's runtime should never get outbound network access
    regardless of NetworkPolicy defaults). A profile-level declaration,
    not enforced by any runtime yet — see app/agent_runtime's own "no
    runtime connected yet" scope note (Phase 01)."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class NetworkPolicyDefault(str, enum.Enum):
    """The default posture for network access not explicitly listed in a
    NetworkPolicy's allow/deny domain lists — see
    app/schemas/project_execution_profile.py's NetworkPolicy. DENY is the
    fail-closed default, matching ScopePolicy/ToolPolicy's own
    deny_by_default convention in app/agent_runtime/policies.py (Phase 01)."""

    DENY = "DENY"
    ALLOW = "ALLOW"


class AgentJobStatus(str, enum.Enum):
    """One AgentJob's lifecycle state — see
    app/models/agent_job.py and app/services/agent_jobs/job_service.py.

    QUEUED              — created, not yet picked up by a dispatcher/worker.
    PREPARING            — a worker has claimed it; compiling/resolving
                          context (Phase 04 PromptCompiler) before any
                          model call.
    RUNNING              — a model/tool call is actually in flight.
    WAITING_INPUT         — stopped for a human clarification answer (see
                          app/agent_runtime's ClarificationRequest) — not
                          resumed in place; see continuation_of_job_id.
    WAITING_APPROVAL       — stopped for a human approval decision (e.g. an
                          Implementation Agent diff) — same continuation
                          rule as WAITING_INPUT.
    COMPLETED               — terminal success.
    FAILED                    — terminal failure; see failure_category for
                          transient vs. permanent.
    CANCELLED                  — terminal; a human/system requested
                          cancellation and it was honored.
    STALE                        — terminal; no heartbeat within the
                          configured window while RUNNING/PREPARING — see
                          job_service.py's find_stale_jobs. Mirrors
                          app.agent_runtime.ExecutionState.STALE's own
                          Phase 01 reservation for exactly this gap.
    """

    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class AgentJobEventType(str, enum.Enum):
    """One normalized runtime event kind — see
    app/models/agent_job.py's AgentJobEvent and
    app/services/agent_jobs/events.py. "Normalized" means every dispatcher
    backend (InlineJobDispatcher, CeleryJobDispatcher, any future one)
    emits the SAME event shapes regardless of which model/tool/provider
    actually produced the underlying activity — a caller polling
    GET /agent-jobs/{id}/events never needs to know which backend ran the
    job.
    """

    STATUS = "STATUS"
    PLAN_SUMMARY = "PLAN_SUMMARY"
    TOOL_REQUEST = "TOOL_REQUEST"
    TOOL_RESULT = "TOOL_RESULT"
    FILE_CHANGE = "FILE_CHANGE"
    COMMAND = "COMMAND"
    TEST_RESULT = "TEST_RESULT"
    USAGE = "USAGE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ARTIFACT = "ARTIFACT"
    WARNING = "WARNING"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"


class JobFailureCategory(str, enum.Enum):
    """Whether a FAILED AgentJob is worth retrying as-is — see
    app/services/agent_jobs/failure_classification.py. Mirrors
    app.agent_runtime.RuntimeFailure.retryable's same TRANSIENT/PERMANENT
    distinction (Phase 01), now applied at the job-persistence layer."""

    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"


class OutboxEntryStatus(str, enum.Enum):
    """One AgentJobOutboxEntry's delivery state — see
    app/services/agent_jobs/outbox.py's module docstring for the
    write-ahead pattern this backs."""

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class RuntimeRole(str, enum.Enum):
    """Phase 07's runtime/credential-broker/organization-project RBAC role
    set — see app/runtime_security/rbac.py. Deliberately a NEW, separate
    enum from UserRole (app/models/enums.py, used by
    app/services/permissions.py's existing per-stage content-authoring
    checks) and from ProjectRole (app/models/project.py's ProjectMember,
    which the Phase 00 baseline found is written once at project creation
    and never actually read/enforced anywhere) — this phase's own RBAC
    system is additive, not a replacement for either existing one; see
    app/runtime_security/__init__.py's module docstring for why.
    """

    ADMIN = "ADMIN"
    PROJECT_OWNER = "PROJECT_OWNER"
    ARCHITECT = "ARCHITECT"
    DEVELOPER = "DEVELOPER"
    REVIEWER = "REVIEWER"
    QA = "QA"
    AUDITOR = "AUDITOR"
    VIEWER = "VIEWER"


class RoleAssignmentScope(str, enum.Enum):
    """Whether a RuntimeRoleAssignment grants a role organization-wide or
    for exactly one project — see
    app/models/runtime_security.py's RuntimeRoleAssignment."""

    ORGANIZATION = "ORGANIZATION"
    PROJECT = "PROJECT"


class AuthenticationMethod(str, enum.Enum):
    """How an AuthenticatedActor's identity was established — see
    app/runtime_security/identity.py. Recorded on every authorization
    audit entry (never silently assumed) so an auditor can distinguish a
    real corporate sign-in from the local-development carve-out."""

    OIDC = "OIDC"
    LOCAL_DEV = "LOCAL_DEV"


class CredentialKind(str, enum.Enum):
    """What kind of credential app.runtime_security.credential_broker
    issued — recorded on every audit entry, never the credential value
    itself."""

    GITHUB_APP_INSTALLATION_TOKEN = "GITHUB_APP_INSTALLATION_TOKEN"
    GITHUB_PAT_FALLBACK = "GITHUB_PAT_FALLBACK"


class SensitiveActionKind(str, enum.Enum):
    """The four action kinds this phase requires human approval for,
    without exception — see
    app/runtime_security/authorization.py's AuthorizationService."""

    REPOSITORY_PUSH = "REPOSITORY_PUSH"
    PULL_REQUEST_CREATE = "PULL_REQUEST_CREATE"
    PULL_REQUEST_COMMENT = "PULL_REQUEST_COMMENT"
    INFRASTRUCTURE_ACTION = "INFRASTRUCTURE_ACTION"
