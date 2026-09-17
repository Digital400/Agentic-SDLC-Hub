"""Enums for the Work Packet / Execution Result contracts — see
app/agent_runtime/__init__.py.

None of these name a vendor or provider (contrast with, say,
app.services.ai_generation.AIProvider, which is intentionally NOT reused
here — that's an internal implementation-selection type, not a wire
contract, and it's exactly the kind of vendor-shaped detail hard rule 1
keeps out of this package).
"""

import enum


class WorkPacketTaskType(str, enum.Enum):
    """What kind of unit of work a WorkPacket describes. Deliberately a
    closed set for Phase 01 (adding a new task type is a schema change, not
    a free-text field) — every value here maps to an existing stage/agent
    already inspected in the Phase 00 baseline
    (docs/architecture/universal-agent-runtime-baseline.md):
    REQUIREMENT_ANALYSIS/PROBLEM_DISCOVERY/SOLUTION_DISCOVERY/HLD -> the
    project-level workflow stages; STORY_CRAFTING_VERTICAL/HORIZONTAL ->
    StoryType.VERTICAL/HORIZONTAL (app/models/enums.py); STORY_LLD ->
    app/services/story_lld_agent.py; IMPLEMENTATION_PLAN -> the
    implementation_planning stage; IMPLEMENT_STORY -> the Implementation
    Agent (app/services/implementation_agent.py); TEST_STORY -> the Testing
    Agent (app/services/testing_agent.py); PR_REVIEW -> the PR Review Agent
    (app/services/pr_review_agent.py); INFRASTRUCTURE_CHANGE -> the
    infrastructure/infrastructure_planning stages; MAINTENANCE_ANALYSIS ->
    the maintenance stage.
    """

    REQUIREMENT_ANALYSIS = "REQUIREMENT_ANALYSIS"
    PROBLEM_DISCOVERY = "PROBLEM_DISCOVERY"
    SOLUTION_DISCOVERY = "SOLUTION_DISCOVERY"
    HLD = "HLD"
    STORY_CRAFTING_VERTICAL = "STORY_CRAFTING_VERTICAL"
    STORY_CRAFTING_HORIZONTAL = "STORY_CRAFTING_HORIZONTAL"
    STORY_LLD = "STORY_LLD"
    IMPLEMENTATION_PLAN = "IMPLEMENTATION_PLAN"
    IMPLEMENT_STORY = "IMPLEMENT_STORY"
    TEST_STORY = "TEST_STORY"
    PR_REVIEW = "PR_REVIEW"
    INFRASTRUCTURE_CHANGE = "INFRASTRUCTURE_CHANGE"
    MAINTENANCE_ANALYSIS = "MAINTENANCE_ANALYSIS"


class ExecutionState(str, enum.Enum):
    """Where one ExecutionResult is in its lifecycle. A superset of this
    codebase's existing per-feature status enums (AgentRunStatus,
    ImplementationRunStatus, TestRunStatus, PRReviewRunStatus — all
    PENDING/RUNNING/COMPLETED/FAILED today) plus three states none of them
    currently model explicitly:
      CLARIFICATION_REQUIRED — mirrors the existing needs_clarification /
        LoopStatus.WAITING_FOR_INPUT outcome (see the Phase 00 baseline,
        section 6.1) promoted to a first-class top-level state here.
      WAITING_APPROVAL — mirrors ImplementationRunReviewStatus.PENDING_REVIEW
        (Phase 00 baseline section 7), generalized to every task type.
      STALE — deliberately new: the Phase 00 baseline (section 9.1)
        confirmed no staleness detection exists anywhere in this codebase
        today. Reserving this state here is a schema-only placeholder for
        that gap, not a claim that any runtime currently sets it.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class ChangeType(str, enum.Enum):
    """Mirrors the existing _VALID_CHANGE_TYPES set in
    app/services/implementation_agent.py — kept as a real enum here since
    this is a typed wire contract, not an internal dict key."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class CheckSeverity(str, enum.Enum):
    """Whether a RequiredCheck failing blocks completion (BLOCKING) or is
    informational only (ADVISORY) — mirrors the "critical vs. suggestion"
    distinction already present in ValidatorResult
    (app/services/validator_agent.py's critical_issues vs. suggestions)."""

    BLOCKING = "BLOCKING"
    ADVISORY = "ADVISORY"


class TestResult(str, enum.Enum):
    """Mirrors testing_agent.py's _VALID_RESULTS — an unparseable/missing
    result must be coerced to FAIL by whatever produces a TestEvidence row,
    never silently treated as PASS (same honesty rule as the existing
    Testing Agent, Phase 00 baseline section 8)."""

    PASS_ = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class FailureCategory(str, enum.Enum):
    """A coarse, vendor-neutral classification of why an ExecutionResult
    reached FAILED — enough for a caller to decide "retry as-is," "retry
    with a smaller scope," or "needs a human," without leaking any
    provider-specific exception type into this contract (see
    app.services.ai_generation.AIGenerationError, which is exactly the kind
    of vendor/implementation-shaped detail this enum exists to abstract
    away from)."""

    PROVIDER_ERROR = "PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CANCELLED_BY_CALLER = "CANCELLED_BY_CALLER"
    UNKNOWN = "UNKNOWN"
