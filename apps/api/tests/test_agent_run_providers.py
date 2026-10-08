"""Tests for GET /agent-runs/providers — the LLM dropdown's real backing
data (see app/schemas/agent_run.py's ProviderOptionRead and
app/api/routes/agent_runs.py's list_provider_options). Confirms it reports
real configured model names and real availability instead of a static
label list, and never leaks an actual key value.
"""

from app.api.routes.agent_runs import list_provider_options
from app.core.config import get_settings


def test_lists_all_seven_providers_in_order(db):
    options = list_provider_options(project_id=None, db=db)

    assert [o.value for o in options] == [
        "claude_agent_sdk", "anthropic", "gemini", "openrouter", "nvidia", "huggingface", "ollama",
    ]


def test_claude_agent_sdk_unavailable_when_flag_is_off(db, monkeypatch):
    monkeypatch.setenv("CLAUDE_AGENT_SDK_ENABLED", "false")
    get_settings.cache_clear()

    options = list_provider_options(project_id=None, db=db)

    sdk = next(o for o in options if o.value == "claude_agent_sdk")
    assert sdk.configured is False
    assert sdk.model is None
    assert "CLAUDE_AGENT_SDK_ENABLED" in sdk.unavailable_reason


def test_claude_agent_sdk_unavailable_when_project_has_no_repository(db, project, monkeypatch):
    monkeypatch.setenv("CLAUDE_AGENT_SDK_ENABLED", "true")
    get_settings.cache_clear()

    options = list_provider_options(project_id=project.id, db=db)

    sdk = next(o for o in options if o.value == "claude_agent_sdk")
    assert sdk.configured is False
    assert "repository" in sdk.unavailable_reason.lower()


def test_provider_reports_its_real_configured_model_name_and_no_key_leak(db, monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf_this_is_a_fake_test_key")
    monkeypatch.setenv("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct")
    get_settings.cache_clear()

    options = list_provider_options(project_id=None, db=db)

    hf = next(o for o in options if o.value == "huggingface")
    assert hf.model == "Qwen/Qwen2.5-Coder-32B-Instruct"
    assert hf.configured is True
    assert hf.unavailable_reason is None
    # The key's own value must never appear anywhere in the response.
    assert "hf_this_is_a_fake_test_key" not in [getattr(o, f) for o in options for f in ("value", "label", "model", "unavailable_reason")]


def test_provider_reports_unconfigured_when_its_key_is_unset(db, monkeypatch):
    # Settings reads apps/api/.env directly (see Config.env_file) — a real
    # developer .env can have a real ANTHROPIC_API_KEY on disk, which a bare
    # monkeypatch.delenv can't unset (there's nothing in os.environ to
    # delete; the value comes from the file, not the process environment).
    # Overriding to an explicit empty string DOES take precedence (pydantic
    # settings prioritizes a real process env var over the .env file), and
    # an empty string is exactly as falsy as None for this test's purposes.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()

    options = list_provider_options(project_id=None, db=db)

    anthropic = next(o for o in options if o.value == "anthropic")
    assert anthropic.configured is False
    assert "ANTHROPIC_API_KEY" in anthropic.unavailable_reason
    assert anthropic.model == get_settings().AI_MODEL


def test_ollama_always_reported_configured_no_key_required(db):
    options = list_provider_options(project_id=None, db=db)

    ollama = next(o for o in options if o.value == "ollama")
    assert ollama.configured is True
    assert ollama.unavailable_reason is None
    assert ollama.model == get_settings().OLLAMA_MODEL


def test_anthropic_and_claude_agent_sdk_offer_curated_claude_model_suggestions(db):
    from app.services.ai_generation import KNOWN_CLAUDE_MODELS

    options = list_provider_options(project_id=None, db=db)

    anthropic = next(o for o in options if o.value == "anthropic")
    sdk = next(o for o in options if o.value == "claude_agent_sdk")
    assert anthropic.available_models == list(KNOWN_CLAUDE_MODELS)
    assert sdk.available_models == list(KNOWN_CLAUDE_MODELS)
    assert "claude-opus-5" in anthropic.available_models
    assert "claude-sonnet-5" in anthropic.available_models


def test_open_ended_providers_offer_no_curated_models(db):
    options = list_provider_options(project_id=None, db=db)

    # OpenRouter is the one exception — it offers the configured default
    # plus one known-good free model as quick picks (see list_provider_options'
    # own comment on why: its free-tier lineup changes often, so this is a
    # suggestion, not a guarantee).
    for value in ("gemini", "nvidia", "huggingface", "ollama"):
        option = next(o for o in options if o.value == value)
        assert option.available_models == [], f"{value} should have no curated suggestions"


def test_openrouter_offers_its_configured_default_and_known_free_models(db):
    from app.core.config import get_settings

    options = list_provider_options(project_id=None, db=db)

    openrouter = next(o for o in options if o.value == "openrouter")
    assert get_settings().OPENROUTER_MODEL in openrouter.available_models
    # Both verified live against OpenRouter's own /api/v1/models — see
    # list_provider_options' own comment on why a model id here must be
    # checked against the real catalog, not guessed from a web search.
    assert "poolside/laguna-s-2.1:free" in openrouter.available_models
    assert "cohere/north-mini-code:free" in openrouter.available_models
    assert len(openrouter.available_models) == len(set(openrouter.available_models))  # no duplicates
