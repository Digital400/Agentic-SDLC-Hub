"""ModelPolicy — every governance rule for a scope's model usage
(company-wide, or one project), and the router that applies it. See
app/model_gateway/router.py for how default_alias/fallback_aliases/
max_calls/timeout are actually exercised end-to-end.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.model_gateway.aliases import ModelAlias


class EscalationCondition(BaseModel):
    """One rule for moving to a stronger alias mid-flow — e.g. a
    validator's low quality score, or a drafting call that itself asked
    for clarification, escalating from sdlc-small to sdlc-standard rather
    than retrying the same weak tier. `trigger` is a plain string key a
    caller checks against (this package doesn't interpret triggers
    itself — see router.py's docstring for why)."""

    model_config = ConfigDict(extra="forbid")

    trigger: str = Field(..., min_length=1, description="e.g. 'needs_clarification', 'low_quality_score', 'validation_critical_issue'.")
    escalate_to_alias: ModelAlias
    description: str = Field("", description="Human-readable rationale, for audit/debugging.")


class ModelPolicy(BaseModel):
    """One governance policy — company-wide by default, or scoped to one
    project (see app/model_gateway/budget.py's BudgetScope for the
    company/project split this same distinction extends to spend
    tracking).
    """

    model_config = ConfigDict(extra="forbid")

    policy_key: str = Field(..., min_length=1)

    allowed_aliases: list[ModelAlias] = Field(..., min_length=1)
    default_alias: ModelAlias
    fallback_aliases: list[ModelAlias] = Field(default_factory=list, description="Tried in order if default_alias fails or its provider is unhealthy — see router.py.")

    max_input_tokens: int = Field(..., ge=1)
    max_output_tokens: int = Field(..., ge=1)
    max_calls: int | None = Field(None, ge=1, description="Ceiling on total attempts across default_alias + every fallback_aliases entry combined, for one router.generate() call — None means no explicit ceiling from this policy.")
    max_cost_usd: float | None = Field(None, ge=0)
    timeout_seconds: float = Field(..., gt=0)
    temperature: float | None = Field(None, ge=0, le=2)
    structured_output_required: bool = False
    data_region_restriction: str | None = Field(None, description="e.g. 'US', 'EU' — an adapter/router MAY use this to refuse a provider known to process data outside the named region; this package doesn't itself maintain a provider-to-region table (see router.py's docstring).")
    escalation_conditions: list[EscalationCondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_and_fallbacks_are_allowed(self) -> "ModelPolicy":
        if self.default_alias not in self.allowed_aliases:
            raise ValueError(f"default_alias '{self.default_alias.value}' must be one of allowed_aliases {[a.value for a in self.allowed_aliases]}.")
        not_allowed = [a for a in self.fallback_aliases if a not in self.allowed_aliases]
        if not_allowed:
            raise ValueError(f"fallback_aliases contains alias(es) not in allowed_aliases: {[a.value for a in not_allowed]}.")
        for condition in self.escalation_conditions:
            if condition.escalate_to_alias not in self.allowed_aliases:
                raise ValueError(f"escalation_conditions targets alias '{condition.escalate_to_alias.value}', which is not in allowed_aliases.")
        return self


# A sensible, conservative default — every alias allowed, general-purpose
# tiers as the default/fallback chain (coding/embedding aliases are opt-in
# per-caller, not part of the default fallback chain, since falling back
# from a general drafting call to a coding-specialized model would be a
# meaningless substitution).
DEFAULT_MODEL_POLICY = ModelPolicy(
    policy_key="default",
    allowed_aliases=list(ModelAlias),
    default_alias=ModelAlias.SDLC_STANDARD,
    fallback_aliases=[ModelAlias.SDLC_SMALL],
    max_input_tokens=8000,
    max_output_tokens=2048,
    max_calls=3,
    max_cost_usd=None,
    timeout_seconds=60.0,
    temperature=None,
    structured_output_required=False,
    data_region_restriction=None,
    escalation_conditions=[],
)
