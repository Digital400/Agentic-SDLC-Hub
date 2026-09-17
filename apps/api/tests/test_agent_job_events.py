"""Tests for app/services/agent_jobs/events.py — the normalized event
constructors and the chain-of-thought scrub."""

from app.models.enums import AgentJobEventType
from app.services.agent_jobs.events import (
    NormalizedEvent,
    command_event,
    completed_event,
    scrub_chain_of_thought,
    status_event,
    usage_event,
)


# --- scrub_chain_of_thought ----------------------------------------------------------------


def test_scrubs_a_top_level_thinking_key():
    result = scrub_chain_of_thought({"content": "hello", "thinking": "secret reasoning trace"})
    assert result == {"content": "hello"}


def test_scrubs_case_insensitively():
    result = scrub_chain_of_thought({"Thinking": "x", "REASONING": "y", "content": "z"})
    assert result == {"content": "z"}


def test_scrubs_a_nested_key():
    result = scrub_chain_of_thought({"outer": {"inner_reasoning_trace": "secret", "keep": "this"}})
    assert result == {"outer": {"keep": "this"}}


def test_scrubs_within_a_list_of_dicts():
    result = scrub_chain_of_thought({"items": [{"chain_of_thought": "secret", "ok": 1}, {"ok": 2}]})
    assert result == {"items": [{"ok": 1}, {"ok": 2}]}


def test_scrubs_scratchpad_and_internal_monologue_markers():
    result = scrub_chain_of_thought({"scratchpad": "x", "internal_monologue": "y", "keep": True})
    assert result == {"keep": True}


def test_never_mutates_the_original_dict():
    original = {"thinking": "secret", "content": "hello"}
    scrub_chain_of_thought(original)
    assert original == {"thinking": "secret", "content": "hello"}  # unchanged


def test_a_field_merely_containing_thought_related_words_in_its_value_is_untouched():
    """Only KEY names are scrubbed — a legitimate field whose VALUE
    happens to mention "thinking" is not itself chain-of-thought and must
    not be dropped."""
    result = scrub_chain_of_thought({"summary": "The plan involves thinking about edge cases."})
    assert result == {"summary": "The plan involves thinking about edge cases."}


def test_clean_payload_is_unchanged():
    payload = {"status": "RUNNING", "detail": "starting up"}
    assert scrub_chain_of_thought(payload) == payload


# --- NormalizedEvent / constructors ---------------------------------------------------------


def test_normalized_event_applies_the_scrub_on_to_stored_payload():
    event = NormalizedEvent(AgentJobEventType.STATUS, {"status": "RUNNING", "thinking": "should be removed"})
    assert "thinking" not in event.to_stored_payload()


def test_status_event_shape():
    event = status_event(status="RUNNING", detail="started")
    assert event.event_type == AgentJobEventType.STATUS
    assert event.payload == {"status": "RUNNING", "detail": "started"}


def test_usage_event_carries_unknown_cost_as_none_not_zero():
    event = usage_event(prompt_tokens=10, completion_tokens=5, cached_tokens=0, cost_usd=None)
    assert event.payload["cost_usd"] is None


def test_command_event_requires_explicit_real_execution():
    event = command_event(command="pytest -q", exit_code=0, real_execution=True)
    assert event.payload["real_execution"] is True


def test_completed_event_shape():
    event = completed_event(summary="Done.")
    assert event.event_type == AgentJobEventType.COMPLETED
    assert event.payload == {"summary": "Done."}


def test_every_required_event_type_has_a_constructor():
    """Cross-check against the literal 13 event kinds this phase's
    instructions require."""
    from app.services.agent_jobs import events as events_module

    required = {
        "STATUS", "PLAN_SUMMARY", "TOOL_REQUEST", "TOOL_RESULT", "FILE_CHANGE", "COMMAND",
        "TEST_RESULT", "USAGE", "APPROVAL_REQUIRED", "ARTIFACT", "WARNING", "ERROR", "COMPLETED",
    }
    assert required == {t.value for t in AgentJobEventType}

    constructed_types = set()
    for name in ("status_event", "plan_summary_event", "tool_request_event", "tool_result_event", "file_change_event", "command_event", "test_result_event", "usage_event", "approval_required_event", "artifact_event", "warning_event", "error_event", "completed_event"):
        fn = getattr(events_module, name)
        assert callable(fn), f"missing constructor {name}"
