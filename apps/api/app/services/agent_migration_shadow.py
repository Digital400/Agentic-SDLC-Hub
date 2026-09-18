"""Phase 16: "Run legacy and new versions in shadow mode. Compare
quality, clarification rate, tokens, cost and human acceptance. Enable
only this target agent through its runtime configuration."

`run_requirement_intake_shadow_comparison` calls BOTH the legacy
`ai_generation.generate()` path and the new v2 path
(`app/services/requirement_intake_agent.py`) and records one
`AgentMigrationShadowRun` row comparing them. The LEGACY result is always
what this function returns for the caller to actually persist — nothing
about this function changes what a human ever sees, matching
`REQUIREMENT_INTAKE_AGENT_V2_MODE`'s "shadow" (default-safe) behavior.

**Not wired into the live `POST /agent-runs` route in this phase** — see
docs/architecture/agent-migration-requirement-intake.md's "Remaining
risks" for why: inserting a second, comparison-only LLM call into an
already-tested, production-facing route under this session's time
constraints is a real risk this phase declines to take casually. This
module is complete and independently tested; wiring it into
`start_agent_run` is a deliberately deferred, disclosed follow-up, not a
silently-skipped requirement.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AgentPrompt, AgentPromptRole, Project, WorkflowNode
from app.models.agent_migration_shadow_run import AgentMigrationShadowRun
from app.services import ai_generation
from app.services.requirement_intake_agent import run_requirement_intake_agent_v2


class ShadowComparisonError(Exception):
    pass


def run_requirement_intake_shadow_comparison(
    db: Session,
    *,
    project: Project,
    node: WorkflowNode,
    active_prompt: AgentPrompt,
    freeform_context: dict[str, str],
    context_token_budget: int,
    output_token_budget: int,
    triggered_by_user_id,
) -> ai_generation.AgentGenerationResult:
    """Runs the legacy generate() call (returned to the caller, unchanged
    behavior) and the v2 call (recorded, never returned), then persists
    one comparison row. Never raises on a v2-side failure — a broken
    shadow path must never take down the real, legacy-served run; any v2
    exception is caught and recorded as a zero-signal comparison row
    instead of propagating."""
    legacy_result = ai_generation.generate(
        project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=active_prompt,
        approved_artifact_content={}, approved_artifact_summaries={}, freeform_context=freeform_context,
        context_token_budget=context_token_budget, output_token_budget=output_token_budget,
    )

    try:
        v2_outcome = run_requirement_intake_agent_v2(project, freeform_context, triggered_by_user_id, output_token_budget)
        v2_tokens = v2_outcome.execution_result.usage.total_tokens
        v2_cost = v2_outcome.execution_result.usage.cost_usd
        v2_needs_clarification = v2_outcome.needs_clarification
        v2_content_length = len(v2_outcome.content_markdown)
        v2_repair_attempted = v2_outcome.repair_attempted
    except Exception as exc:  # noqa: BLE001 — deliberately broad: a shadow-path failure must never propagate.
        v2_tokens, v2_cost, v2_needs_clarification, v2_content_length, v2_repair_attempted = 0, 0.0, False, 0, False
        extra_data = {"v2_error": str(exc)}
    else:
        extra_data = None

    shadow_row = AgentMigrationShadowRun(
        project_id=project.id, node_key=node.node_key,
        legacy_total_tokens=legacy_result.total_tokens, legacy_cost_usd=legacy_result.cost or 0.0,
        legacy_needs_clarification=legacy_result.needs_clarification, legacy_content_length=len(legacy_result.content_markdown),
        v2_total_tokens=v2_tokens, v2_cost_usd=v2_cost, v2_needs_clarification=v2_needs_clarification,
        v2_content_length=v2_content_length, v2_repair_attempted=v2_repair_attempted,
        clarification_outcomes_matched=(legacy_result.needs_clarification == v2_needs_clarification),
        extra_data=extra_data,
    )
    db.add(shadow_row)
    db.flush()

    return legacy_result
