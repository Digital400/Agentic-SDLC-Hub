"""Integration tests for app/services/ai_generation.py's Phase 05
strangler-migration branch — Settings.MODEL_GATEWAY_MODE == "gateway".
The default ("legacy") behavior is covered exhaustively by
tests/test_ai_generation_providers.py and every other existing
ai_generation test — this file only exercises the NEW branch.
"""

from types import SimpleNamespace

import pytest

import app.model_gateway as mg
from app.core.config import Settings
from app.model_gateway.base import ModelGatewayError
from app.model_gateway.cost import Cost
from app.model_gateway.fake_gateway import FakeCallOutcome, FakeModelGateway
from app.services import ai_generation


def _settings(**overrides) -> SimpleNamespace:
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _gateway_mode(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(MODEL_GATEWAY_MODE="gateway"))
    yield


def _install_fake(monkeypatch, gateway: FakeModelGateway) -> None:
    monkeypatch.setattr(mg, "select_gateway", lambda: gateway)


def test_generate_raw_text_routes_through_the_gateway_when_flag_is_on(monkeypatch):
    _install_fake(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(content_markdown="{}\n---\nGateway says hi.")))
    result = ai_generation.generate_raw_text(system_prompt="sys", user_content="hi", output_token_budget=100)
    assert result == "Gateway says hi."


def test_clarification_is_detected_from_a_gateway_backed_by_a_non_parsing_adapter(monkeypatch):
    """The new adapters (ollama/openrouter/litellm) return raw, unparsed
    provider text — ai_generation.py's own _generate_via_gateway must
    still detect the two-part contract from that raw text."""
    outcome = FakeCallOutcome(content_markdown='{"needs_clarification": true, "clarification_questions": ["What is the deadline?"]}\n---\n')
    _install_fake(monkeypatch, FakeModelGateway(default_outcome=outcome))
    result = ai_generation._generate_via_gateway(system_prompt="sys", user_content="hi", output_token_budget=100)
    assert result.needs_clarification is True
    assert result.clarification_questions == ["What is the deadline?"]
    assert ai_generation.CLARIFICATION_MARKER in result.content_markdown


def test_an_adapter_that_already_parsed_clarification_is_trusted_directly(monkeypatch):
    """LegacyModelGateway-shaped adapters set needs_clarification=True
    themselves (no '---' marker in content_markdown at all) — this must
    not be lost by re-parsing raw text that was never two-part-shaped."""
    from dataclasses import replace

    class _PreParsedGateway(FakeModelGateway):
        def generate(self, request):
            response = super().generate(request)
            return replace(response, needs_clarification=True, clarification_questions=["Pre-parsed question?"], content_markdown="ignored raw text")

    _install_fake(monkeypatch, _PreParsedGateway(default_outcome=FakeCallOutcome()))
    result = ai_generation._generate_via_gateway(system_prompt="sys", user_content="hi", output_token_budget=100)
    assert result.needs_clarification is True
    assert result.clarification_questions == ["Pre-parsed question?"]


def test_known_cost_flows_through_as_a_plain_float(monkeypatch):
    _install_fake(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(content_markdown="{}\n---\nok", cost=Cost.of(0.0042))))
    result = ai_generation._generate_via_gateway(system_prompt="sys", user_content="hi", output_token_budget=100)
    assert result.cost == 0.0042


def test_unknown_cost_flows_through_as_none_never_as_zero(monkeypatch):
    _install_fake(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(content_markdown="{}\n---\nok", cost=Cost.unknown())))
    result = ai_generation._generate_via_gateway(system_prompt="sys", user_content="hi", output_token_budget=100)
    assert result.cost is None
    assert result.cost != 0.0


def test_token_counts_are_carried_through(monkeypatch):
    _install_fake(monkeypatch, FakeModelGateway(default_outcome=FakeCallOutcome(content_markdown="{}\n---\nok", prompt_tokens=123, completion_tokens=45)))
    result = ai_generation._generate_via_gateway(system_prompt="sys", user_content="hi", output_token_budget=100)
    assert result.prompt_tokens == 123
    assert result.completion_tokens == 45
    assert result.total_tokens == 168


def test_gateway_failure_is_surfaced_as_ai_generation_error(monkeypatch):
    _install_fake(monkeypatch, FakeModelGateway())  # no outcomes configured at all -> every alias in the fallback chain fails
    with pytest.raises(ai_generation.AIGenerationError):
        ai_generation.generate_raw_text(system_prompt="sys", user_content="hi", output_token_budget=100)


def test_legacy_mode_is_unaffected_by_this_branch_existing(monkeypatch):
    """Sanity check that the strangler branch really is opt-in: with the
    flag left at its real default, generate_raw_text still goes straight
    through the real _generate_with_anthropic dispatch, never touching
    select_gateway() at all.

    Uses a real-provider-configured setup (not the mock provider) —
    generate_raw_text has no mock branch of its own by design (see its
    own docstring: "Never called when the mock provider is active"), so
    this stays within that function's actual documented contract rather
    than exercising a path it was never meant to serve."""
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(MODEL_GATEWAY_MODE="legacy", ANTHROPIC_API_KEY="sk-ant-fake"))

    def _fake_anthropic_call(system_prompt, user_content, output_token_budget):
        return ai_generation.AgentGenerationResult(content_markdown="Hello from the real dispatch table.", needs_clarification=False)

    monkeypatch.setattr(ai_generation, "_generate_with_anthropic", _fake_anthropic_call)

    def _select_gateway_should_not_be_called():
        raise AssertionError("select_gateway() must not be called on the legacy path")

    monkeypatch.setattr(mg, "select_gateway", lambda: _select_gateway_should_not_be_called())

    result = ai_generation.generate_raw_text(system_prompt="sys", user_content="hi", output_token_budget=100)
    assert result == "Hello from the real dispatch table."
