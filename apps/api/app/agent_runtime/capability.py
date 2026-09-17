"""RuntimeCapabilityManifest — what a concrete runtime declares it can
actually do, independent of any one WorkPacket. See
app/agent_runtime/__init__.py's package docstring for this package's five
hard rules; this contract is a Phase 04 addition (PromptCompiler input),
not a Phase 01 contract, but lives here because it's the same kind of
thing WorkPacket is — a vendor-neutral wire contract, versioned the same
way (see `schema_version`).

Deliberately separate from WorkPacket: a WorkPacket describes ONE task; a
RuntimeCapabilityManifest describes what ONE runtime environment (however
long-lived — a single execution, a persistent agent process, a CI
container image) is capable of, and is typically supplied once per
runtime rather than once per task. app/prompt_compiler.PromptCompiler
combines both — see that package's module docstring.
"""

from pydantic import Field

from app.agent_runtime.base import CURRENT_SCHEMA_VERSION, SchemaVersion, VendorExtensible


class RuntimeCapabilityManifest(VendorExtensible):
    """No vendor-mandatory field (hard rule 1) — `runtime_name` is a
    free-text label a runtime chooses for itself (e.g. "claude-code",
    "generic-ci-container"), not a closed enum of known vendors; anything
    vendor-specific about HOW a capability is realized belongs in
    `extensions`, never as a new field here.
    """

    schema_version: SchemaVersion = CURRENT_SCHEMA_VERSION
    runtime_name: str = Field(..., min_length=1, description="A free-text, self-declared runtime label — not a closed vendor enum. See class docstring.")

    max_context_tokens: int = Field(..., ge=1, description="This runtime's own hard context-window ceiling — PromptCompiler trims to min(this, BudgetPolicy.max_context_tokens).")
    max_output_tokens: int = Field(..., ge=1)

    supported_tool_categories: list[str] = Field(
        default_factory=list,
        description="Coarse categories this runtime can actually invoke, e.g. 'file_read', 'file_write', 'shell_command', 'network_request' — PromptCompiler only ever instructs the runtime to use a tool category both this manifest AND the ProjectExecutionProfile/ToolPolicy allow (the intersection, never either alone).",
    )
    supports_structured_output: bool = Field(False, description="Whether this runtime can reliably emit schema-conformant JSON (vs. only free-form Markdown) — affects which response_format PromptCompiler selects.")
    supports_streaming: bool = Field(False, description="Advisory only — no contract here changes shape based on streaming; informational for a caller's own transport choice.")
    response_formats: list[str] = Field(
        default_factory=list,
        description="Which response contracts this runtime can follow, e.g. 'markdown_with_clarification_header' (see ai_generation.py's existing two-part contract, Phase 00 baseline section 6.1) or 'structured_json' (see implementation_agent.py/testing_agent.py/pr_review_agent.py's shared JSON contract, Phase 00 baseline section 1.4).",
    )
