"""ExecutionResult — the top-level "here is what happened" contract — and
its evidence sub-contracts. See app/agent_runtime/__init__.py.

HONESTY RULE (carried forward from the Phase 00 baseline's confirmed
finding that this codebase has no test-execution sandbox —
docs/architecture/universal-agent-runtime-baseline.md section 8): every
evidence contract that could be mistaken for "this was really executed"
(CommandEvidence, TestEvidence) carries an explicit `real_execution: bool`
field. A resolver populating one of these from a reasoning-based assessment
(no sandbox, no real CI run) MUST set `real_execution=False` — this
contract does not, and cannot, verify that claim itself, but it makes the
distinction a required, typed field instead of buried prose, exactly as
testing_agent.py's rendered Markdown already does today for its own
disclosure.
"""

import uuid
from datetime import datetime

from pydantic import Field

from app.agent_runtime.base import CURRENT_SCHEMA_VERSION, SchemaVersion, VendorExtensible
from app.agent_runtime.enums import ChangeType, ExecutionState, FailureCategory, TestResult


class FileChangeEvidence(VendorExtensible):
    """One proposed or applied file change — mirrors
    implementation_agent.py's ProposedFileChange. `content_ref` exists so a
    large file's full after-content can be stored/fetched out-of-band
    (mirroring hard rule 3's "reference, don't duplicate" principle applied
    to OUTPUT evidence, not just upstream input) while `diff` stays a
    bounded, human-readable unified diff suitable for direct display."""

    path: str = Field(..., min_length=1)
    change_type: ChangeType
    summary: str = Field(..., min_length=1)
    diff: str | None = Field(None, description="A bounded unified diff for human review — mirrors implementation_agent.py's diff_text per file.")
    content_ref: str | None = Field(None, description="A pointer to where this file's full proposed content can be fetched, if it was stored out-of-band rather than inlined as `diff`.")


class CommandEvidence(VendorExtensible):
    """One command a resolver ran (or reasoned about) while executing a
    task — e.g. a test command, a lint command. See module docstring's
    HONESTY RULE for `real_execution`."""

    command: str = Field(..., min_length=1)
    real_execution: bool = Field(..., description="True only if this command was actually run in a real sandbox/CI environment. False for a reasoning-based assessment — see module docstring.")
    exit_code: int | None = None
    duration_seconds: float | None = Field(None, ge=0)
    stdout_excerpt: str | None = Field(None, max_length=8000, description="Bounded — never the full raw log. Must never contain a secret; see test_no_contract_field_is_secret_shaped.")
    stderr_excerpt: str | None = Field(None, max_length=8000)


class TestEvidence(VendorExtensible):
    """One test's result — mirrors testing_agent.py's TestExecuted. See
    module docstring's HONESTY RULE for `real_execution`; an unparseable or
    missing result must be recorded as FAIL, never silently upgraded to
    PASS (same rule testing_agent.py's own _run_real_agent already
    enforces, Phase 00 baseline section 8)."""

    name: str = Field(..., min_length=1)
    result: TestResult
    real_execution: bool = Field(..., description="True only if this test was actually run. False for a reasoning-based assessment.")
    criterion_id: str | None = Field(None, description="Optional link back to a WorkPacket.acceptance_criteria[].id this test verifies.")
    notes: str | None = None
    duration_seconds: float | None = Field(None, ge=0)


class UsageEvidence(VendorExtensible):
    """Actual resource consumption for one ExecutionResult — the same
    shape BudgetPolicy caps, so a caller can directly compare usage against
    budget field-by-field. Mirrors AgentRun's token_usage/cost/
    estimated_context_tokens fields (Phase 00 baseline section 5), but
    provider-neutral: no field here is specific to any one of the five
    providers ai_generation.py currently supports."""

    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cost_usd: float = Field(0.0, ge=0, description="0.0 is a valid, meaningful value (e.g. a free-tier provider) — not a sentinel for 'unknown'. A resolver that cannot determine cost should omit this field's producer from schema-version-compatible output entirely rather than guess 0.0 as a stand-in for unknown, per this phase's 'never fabricate' principle.")
    llm_calls_made: int = Field(0, ge=0)
    tool_calls_made: int = Field(0, ge=0)
    distinct_tools_used: list[str] = Field(default_factory=list)
    wall_clock_seconds: float | None = Field(None, ge=0)
    repair_attempts_used: int = Field(0, ge=0, description="Mirrors AgentRun.loop_iteration / LoopResult.iterations_run — how many improve/repair passes this execution actually took, comparable against BudgetPolicy.max_repair_attempts.")


class RuntimeFailure(VendorExtensible):
    """Why an ExecutionResult reached FAILED — see enums.FailureCategory's
    docstring for why this is a closed, vendor-neutral category set rather
    than a passthrough of a provider's own exception type/message shape."""

    category: FailureCategory
    message: str = Field(..., min_length=1, description="Human-readable — must never include a secret (a token, key, or Authorization header value), mirroring the existing discipline in ai_generation.py's provider functions (Phase 00 baseline section 7's 'never the token' pattern).")
    retryable: bool = Field(..., description="Whether retrying the same WorkPacket unmodified might succeed — e.g. True for a transient provider timeout, False for a SCOPE_VIOLATION that needs a corrected packet.")


class ClarificationRequest(VendorExtensible):
    """Mirrors ai_generation.py's needs_clarification/
    clarification_questions contract (Phase 00 baseline section 6.1),
    generalized to every task_type rather than just the Markdown
    draft/clarification response format."""

    questions: list[str] = Field(..., min_length=1, description="At least one question — an empty ClarificationRequest is not meaningful.")
    blocking: bool = Field(True, description="True (the default, and the only behavior the current codebase implements — Phase 00 baseline section 6.1) means execution cannot proceed until answered.")
    context: str | None = Field(None, description="Optional free-text explaining why clarification is needed, beyond the questions themselves.")


class ExecutionResult(VendorExtensible):
    """The top-level "here is what happened" envelope for one WorkPacket's
    execution — referenced back to its packet by `packet_id`, never
    embedding the WorkPacket itself (that would duplicate the packet's own
    upstream-artifact references transitively).

    Exactly one of `failure` / `clarification` is meaningful depending on
    `state` (FAILED -> failure populated; CLARIFICATION_REQUIRED ->
    clarification populated); both are Optional and this contract does not
    itself enforce that correlation — a resolver-time invariant, not a
    schema-time one, matching how AgentRun's own error_message/
    loop_status fields already coexist without a schema-level constraint
    tying them together (Phase 00 baseline section 5-6).
    """

    schema_version: SchemaVersion = CURRENT_SCHEMA_VERSION
    result_id: uuid.UUID
    packet_id: uuid.UUID = Field(..., description="References the WorkPacket this result belongs to — see WorkPacket.packet_id.")
    state: ExecutionState

    summary: str | None = Field(None, description="A short human-readable outcome summary — not the full output content, which stays out of this contract per hard rule 3's spirit (output content belongs in FileChangeEvidence/an artifact, referenced or bounded, not inlined here as free text).")
    file_changes: list[FileChangeEvidence] = Field(default_factory=list)
    commands: list[CommandEvidence] = Field(default_factory=list)
    test_evidence: list[TestEvidence] = Field(default_factory=list)
    usage: UsageEvidence = Field(default_factory=UsageEvidence)

    failure: RuntimeFailure | None = Field(None, description="Populated when state == FAILED.")
    clarification: ClarificationRequest | None = Field(None, description="Populated when state == CLARIFICATION_REQUIRED.")

    started_at: datetime | None = None
    completed_at: datetime | None = None
