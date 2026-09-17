"""Normalized runtime event shapes + the chain-of-thought scrub every
event payload passes through before it is ever persisted. See
app/models/agent_job.py's AgentJobEvent and
app/services/agent_jobs/job_service.py's record_event.

WHY "NORMALIZED": every dispatcher backend (InlineJobDispatcher,
CeleryJobDispatcher, any future one) and every job handler emits events
through the SAME thirteen kinds (app.models.enums.AgentJobEventType) with
the SAME payload shapes here — a caller polling
GET /agent-jobs/{id}/events never needs backend- or handler-specific
parsing logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import AgentJobEventType

# HARD RULE ("do not expose chain-of-thought"): any of these keys, found
# ANYWHERE in an event payload (including nested dicts), is stripped
# before persistence — never partially redacted, never stored "just in
# case." This is deliberately broad (substring match on the key name, not
# an exact list) so a handler that accidentally names a field
# "model_thinking" or "internal_reasoning_trace" is caught too, not just
# the exact literal "thinking"/"reasoning".
_CHAIN_OF_THOUGHT_KEY_MARKERS = ("thinking", "reasoning", "chain_of_thought", "scratchpad", "internal_monologue")


def scrub_chain_of_thought(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively removes any key matching a chain-of-thought marker
    (see module docstring) from `payload`, returning a new dict — never
    mutates the input, so a caller that still holds a reference to the
    original object doesn't get a surprising in-place edit."""

    def _scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: _scrub(v)
                for k, v in value.items()
                if not any(marker in k.lower() for marker in _CHAIN_OF_THOUGHT_KEY_MARKERS)
            }
        if isinstance(value, list):
            return [_scrub(v) for v in value]
        return value

    return _scrub(payload)


@dataclass
class NormalizedEvent:
    """What a job handler actually constructs and hands to
    AgentJobService.record_event — that method is what calls
    scrub_chain_of_thought on `.payload` before writing a row, so a
    handler author doesn't have to remember to call it themselves."""

    event_type: AgentJobEventType
    payload: dict[str, Any] = field(default_factory=dict)

    def to_stored_payload(self) -> dict[str, Any]:
        return scrub_chain_of_thought(self.payload)


# --- Convenience constructors — one per event kind, so a handler author
# builds a NormalizedEvent from named arguments instead of hand-assembling
# a payload dict and risking a typo'd key name. Every field name below is
# part of this event kind's stable, documented shape.


def status_event(*, status: str, detail: str | None = None) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.STATUS, {"status": status, "detail": detail})


def plan_summary_event(*, summary: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.PLAN_SUMMARY, {"summary": summary})


def tool_request_event(*, tool_name: str, arguments_summary: str) -> NormalizedEvent:
    """`arguments_summary` — deliberately a SUMMARY, not the raw
    arguments blob: a tool call's raw arguments can itself carry
    provider-side reasoning framing in some runtimes; a handler is
    expected to have already reduced this to a plain description."""
    return NormalizedEvent(AgentJobEventType.TOOL_REQUEST, {"tool_name": tool_name, "arguments_summary": arguments_summary})


def tool_result_event(*, tool_name: str, success: bool, result_summary: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.TOOL_RESULT, {"tool_name": tool_name, "success": success, "result_summary": result_summary})


def file_change_event(*, path: str, change_type: str, summary: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.FILE_CHANGE, {"path": path, "change_type": change_type, "summary": summary})


def command_event(*, command: str, exit_code: int | None, real_execution: bool) -> NormalizedEvent:
    """`real_execution` mirrors app.agent_runtime.CommandEvidence's own
    HARD RULE (Phase 01) — never defaulted, always explicit."""
    return NormalizedEvent(AgentJobEventType.COMMAND, {"command": command, "exit_code": exit_code, "real_execution": real_execution})


def test_result_event(*, name: str, result: str, real_execution: bool) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.TEST_RESULT, {"name": name, "result": result, "real_execution": real_execution})


def usage_event(*, prompt_tokens: int, completion_tokens: int, cached_tokens: int, cost_usd: float | None) -> NormalizedEvent:
    """`cost_usd=None` means unknown — see app.model_gateway.cost.Cost's
    own HARD RULE (Phase 05); this event never coerces that to 0.0."""
    return NormalizedEvent(AgentJobEventType.USAGE, {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "cached_tokens": cached_tokens, "cost_usd": cost_usd})


def approval_required_event(*, summary: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.APPROVAL_REQUIRED, {"summary": summary})


def artifact_event(*, artifact_type: str, reference: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.ARTIFACT, {"artifact_type": artifact_type, "reference": reference})


def warning_event(*, message: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.WARNING, {"message": message})


def error_event(*, message: str, category: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.ERROR, {"message": message, "category": category})


def completed_event(*, summary: str) -> NormalizedEvent:
    return NormalizedEvent(AgentJobEventType.COMPLETED, {"summary": summary})
