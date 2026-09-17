"""Scope, tool, check, budget, and approval policy contracts — the guardrails
a WorkPacket carries alongside its objective. See app/agent_runtime/__init__.py.

None of these policies is enforced by this package itself (hard rule 5 — no
runtime is connected yet); they are the DATA a future enforcement layer
would read. Phase 00's baseline (docs/architecture/universal-agent-runtime-
baseline.md section 5 and section 12 item 8) found this codebase currently
has context-window budgeting (TokenBudgetService) but NO spend-limit
enforcement — BudgetPolicy below is deliberately shaped to make that gap
closeable later without a contract change, not to claim the gap is already
closed.
"""

from pydantic import Field, model_validator

from app.agent_runtime.base import AgentRuntimeModel
from app.agent_runtime.enums import CheckSeverity


class ScopePolicy(AgentRuntimeModel):
    """What a task is, and is not, allowed to touch — file-path scope only
    in Phase 01 (no code-symbol-level scoping yet). `denied_paths` is
    intended to always win over `allowed_paths` on overlap, matching the
    fail-closed posture the rest of this codebase already uses for
    security-relevant checks (e.g. app/core/security.py's token handling)
    — this contract carries that DATA; per hard rule 5, no resolver/
    enforcement logic exists in this package yet to actually apply it.

    Glob syntax (`fnmatch`-style, e.g. `apps/api/app/services/**`) is the
    expected pattern shape; this contract does not itself validate glob
    syntax — that's a resolver-time concern, not a schema concern.
    """

    allowed_paths: list[str] = Field(
        default_factory=list,
        description="Glob patterns this task may create/modify/delete. Empty list means 'no explicit allow-list' — a resolver should treat that as deny-by-default unless deny_by_default is explicitly set False.",
    )
    denied_paths: list[str] = Field(
        default_factory=list,
        description="Glob patterns this task may never touch, regardless of allowed_paths. Always wins on overlap.",
    )
    deny_by_default: bool = Field(
        True,
        description="When true (default), a path matching neither list is denied — fail-closed. When false, an unmatched path is allowed (only denied_paths is enforced).",
    )
    max_files_changed: int | None = Field(None, ge=1, description="Optional cap on distinct file paths touched, independent of BudgetPolicy's cost/time caps.")


class ToolPolicy(AgentRuntimeModel):
    """Which named tools/actions a runtime may invoke while executing this
    packet — identity/allow-deny only. How MANY tool calls are permitted in
    total is a BudgetPolicy concern (max_tool_calls), not this one; keeping
    "which" and "how many" on separate contracts means a caller can tighten
    one without touching the other.
    """

    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Named tools/actions this task may invoke, e.g. 'read_file', 'run_tests', 'create_pull_request'. Empty means 'no explicit allow-list' — see deny_by_default.",
    )
    denied_tools: list[str] = Field(default_factory=list, description="Always wins over allowed_tools on overlap.")
    deny_by_default: bool = Field(True, description="Same fail-closed semantics as ScopePolicy.deny_by_default.")
    require_dry_run_first: bool = Field(
        False, description="Advisory flag: a resolver may require one dry-run/preview pass (e.g. a diff, never a real write) before any tool in allowed_tools may run for real.",
    )


class RequiredCheck(AgentRuntimeModel):
    """One check that must (BLOCKING) or should (ADVISORY) pass before this
    task's output is considered acceptable — e.g. "run the backend test
    suite," "no critical PR-review findings." Mirrors the
    test_command/required_evidence_section concepts already present in this
    codebase (ImplementationTask.test_expectation,
    GraphEngineService.validate_evidence_requirement — Phase 00 baseline
    section 11) as a reusable, structured type instead of a single free-text
    field.
    """

    name: str = Field(..., min_length=1, description="e.g. 'backend-test-suite', 'pr-review-no-critical-findings'.")
    description: str = Field(..., min_length=1)
    severity: CheckSeverity = CheckSeverity.BLOCKING
    command: str | None = Field(None, description="An executable command a runtime could run to evaluate this check, e.g. 'cd apps/api && pytest -q'. Optional — not every check is command-shaped.")


class BudgetPolicy(AgentRuntimeModel):
    """Every resource ceiling a WorkPacket's execution must respect — the
    "maximum cost, time, calls, tools and repair attempts" this phase's
    instructions require. All fields are optional (None = "no explicit
    ceiling from this packet"); a resolver applying this packet is expected
    to fall back to its own platform-wide default when a field is None,
    never to treat None as "unlimited" silently — that interpretation is a
    resolver policy decision, deliberately left outside this schema.

    `max_repair_attempts` mirrors app/services/loop_engine.py's
    DEFAULT_MAX_ITERATIONS (currently 3, hardcoded with no per-stage
    override — see the Phase 00 baseline section 6.2 and section 12 item 4)
    — this field is exactly the seam that gap would be closed through.
    """

    max_cost_usd: float | None = Field(None, ge=0)
    max_wall_clock_seconds: int | None = Field(None, ge=1)
    max_llm_calls: int | None = Field(None, ge=1, description="Maximum number of distinct model/completion calls, across every step of a run (mirrors summing across LoopEngineService iterations — Phase 00 baseline section 5).")
    max_tool_calls: int | None = Field(None, ge=1, description="Maximum number of tool/action invocations total, regardless of how many distinct tools are used.")
    max_distinct_tools: int | None = Field(None, ge=1, description="Maximum number of DISTINCT tools (by name) that may be invoked — narrower than ToolPolicy.allowed_tools, which only says which tools are permitted at all.")
    max_repair_attempts: int | None = Field(None, ge=0, description="Maximum number of improve/repair iterations after the first attempt — 0 means 'no repair, first attempt only.' Mirrors LoopEngineService's max_iterations.")
    max_context_tokens: int | None = Field(None, ge=1, description="Optional override of the context-window budget a resolver should apply — mirrors WorkflowNode.context_token_budget (Phase 00 baseline section 3.3).")
    max_output_tokens: int | None = Field(None, ge=1, description="Mirrors WorkflowNode.output_token_budget.")


class ApprovalPolicy(AgentRuntimeModel):
    """Who, if anyone, must sign off on this task's output before it is
    considered final — generalizes the per-stage STAGE_APPROVE_ROLES table
    (app/services/permissions.py, Phase 00 baseline section 10) and the
    Implementation Run accept/reject gate (section 7) into one reusable
    shape. `approval_roles` uses plain strings, not app.models.enums.UserRole
    directly — this package must stay importable without a hard dependency
    on the application's own ORM/enum layer (this is a wire contract, meant
    to also be consumable by an out-of-process runtime), so role names are
    validated as non-empty strings, not as members of that specific enum.
    """

    requires_human_approval: bool = Field(True, description="False only for a task type/stage explicitly configured to complete without a review gate — mirrors WorkflowNode.requires_human_approval.")
    approval_roles: list[str] = Field(
        default_factory=list,
        description="Role names permitted to approve this task's output, e.g. ['TECH_LEAD']. Empty with requires_human_approval=True means 'any authorized reviewer' — a resolver-level policy decision, not resolved here.",
    )
    escalation_roles: list[str] = Field(default_factory=list, description="Optional additional roles notified if approval is overdue — advisory only, not enforced by this contract.")

    @model_validator(mode="after")
    def _approval_roles_require_approval_flag(self) -> "ApprovalPolicy":
        if self.approval_roles and not self.requires_human_approval:
            raise ValueError("approval_roles was set but requires_human_approval is False — a policy naming approvers must also require approval.")
        return self
