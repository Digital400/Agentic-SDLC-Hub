"""default_job_handler — the reference AgentJobDispatcher handler,
wiring together every prior phase's additive architecture for the first
time: app.agent_runtime's WorkPacket/ExecutionResult (Phase 01),
app.services.execution_profile_service's approved ProjectExecutionProfile
(Phase 03), app.prompt_compiler's PromptCompiler (Phase 04), and
app.model_gateway's PolicyDrivenRouter (Phase 05).

Registered under the name "default" (see dispatcher.py's
register_handler) — a caller submitting a job without naming a different
handler gets this one. Still fully additive: nothing outside
app/api/routes/agent_jobs.py calls this handler, and that route is itself
new (Phase 00's existing agent-run routes are untouched).

COOPERATIVE CANCELLATION: checked at each major step boundary (never
mid-model-call — this codebase has no way to interrupt an in-flight
provider request) — see `_check_cancelled`.

NO CHAIN-OF-THOUGHT: every event this handler emits goes through
job_service.record_event, which itself calls
app.services.agent_jobs.events.scrub_chain_of_thought on every payload
before persisting it — this handler doesn't need its own scrubbing logic,
but also never reads or forwards any provider "thinking" field in the
first place (mirrors app.services.ai_generation.py's own Phase 00
finding: only `response.content` text blocks are ever read from
Anthropic, never a thinking block).
"""

from __future__ import annotations

from app.agent_runtime import ExecutionResult, ExecutionState, RuntimeCapabilityManifest
from app.model_gateway import DEFAULT_MODEL_POLICY, GatewayRequest, PolicyDrivenRouter, select_gateway
from app.model_gateway.base import ModelGatewayError
from app.models import AgentJob
from app.prompt_compiler import PromptCompiler
from app.prompt_compiler.lint import PromptLintError
from app.services.agent_jobs.dispatcher import register_handler
from app.services.agent_jobs.events import (
    completed_event,
    error_event,
    plan_summary_event,
    usage_event,
)
from app.services.agent_jobs.job_service import AgentJobService
from app.services.execution_profile_service import ProjectExecutionProfileService

# A generic, provider-neutral capability manifest — the handler doesn't
# know in advance which concrete ModelGateway backend is active (that's
# Settings.MODEL_GATEWAY_BACKEND's own concern, Phase 05); this describes
# what the handler itself needs from whichever one is selected.
_DEFAULT_CAPABILITY = RuntimeCapabilityManifest(
    runtime_name="agent-job-handler",
    max_context_tokens=8000,
    max_output_tokens=2048,
    supported_tool_categories=["file_read", "file_write", "shell_command"],
    supports_structured_output=True,
    response_formats=["structured_json", "markdown_with_clarification_header"],
)


class JobCancelledError(Exception):
    """Raised internally by _check_cancelled — caught by
    default_job_handler itself (never propagates to the dispatcher, since
    a cancellation is not a failure to classify/retry)."""


def _check_cancelled(service: AgentJobService, job: AgentJob) -> None:
    service.db.refresh(job, attribute_names=["cancellation_requested"])
    if job.cancellation_requested:
        raise JobCancelledError()


@register_handler("default")
def default_job_handler(service: AgentJobService, job: AgentJob) -> None:
    try:
        _check_cancelled(service, job)
        work_packet = service.parse_work_packet(job)

        profile_service = ProjectExecutionProfileService(service.db)
        profile_row = profile_service.get_active_profile(job.project_id)
        if profile_row is None:
            raise ValueError(
                f"Project {job.project_id} has no approved, active ProjectExecutionProfile — "
                "propose one and get Project Owner approval before running this job (see "
                "app/services/execution_profile_service.py, Phase 03)."
            )
        from app.schemas.project_execution_profile import ProjectExecutionProfileRead

        profile = ProjectExecutionProfileRead.model_validate(profile_row)

        _check_cancelled(service, job)
        service.mark_running(job)

        compiled = PromptCompiler().compile(work_packet=work_packet, capability=_DEFAULT_CAPABILITY, profile=profile)
        service.record_event(job, plan_summary_event(summary=compiled.short_system_instruction))

        _check_cancelled(service, job)

        gateway = select_gateway()
        router = PolicyDrivenRouter(gateway, DEFAULT_MODEL_POLICY)
        request = GatewayRequest(
            alias=DEFAULT_MODEL_POLICY.default_alias, system_prompt=compiled.short_system_instruction,
            user_content=compiled.task_instruction, output_token_budget=compiled.runtime_configuration["max_output_tokens"],
        )
        response = router.generate(request)

        service.record_event(job, usage_event(
            prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens,
            cached_tokens=response.cached_tokens, cost_usd=response.cost.to_report_value(),
        ))

        if response.needs_clarification:
            service.mark_waiting_input(job)
            return

        execution_result = ExecutionResult(
            result_id=job.id,
            packet_id=work_packet.packet_id,
            state=ExecutionState.COMPLETED,
            summary=response.content_markdown[:500],
        )
        service.record_event(job, completed_event(summary=execution_result.summary or ""))
        service.mark_completed(job, result=execution_result.model_dump(mode="json"))

    except JobCancelledError:
        service.mark_cancelled(job)
    except (ModelGatewayError, PromptLintError) as exc:
        service.record_event(job, error_event(message=str(exc), category=type(exc).__name__))
        raise
