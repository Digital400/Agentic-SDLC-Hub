"""Tests for app/runtime_security/security_gate.py — "Keep all external
coding runtimes disabled until this security gate passes," and its wiring
into app/services/agent_jobs's select_dispatcher (Phase 06's own
"celery" == external-runtime boundary)."""

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.runtime_security.security_gate import SecurityGateNotPassedError, SecurityGateService, require_security_gate_passed


def _settings(**overrides) -> Settings:
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


_FULLY_CONFIGURED = dict(
    EXTERNAL_CODING_RUNTIMES_ENABLED=True,
    OIDC_ISSUER="https://issuer.example.com", OIDC_AUDIENCE="client-id",
    CELERY_BROKER_URL="redis://localhost:6379/0",
    GITHUB_APP_ID="123", GITHUB_APP_PRIVATE_KEY="pem", GITHUB_APP_INSTALLATION_ID="456",
)


def test_fully_configured_settings_pass():
    status = SecurityGateService(_settings(**_FULLY_CONFIGURED)).check()
    assert status.passed is True
    assert status.reasons == []


def test_default_settings_fail_on_every_dimension():
    status = SecurityGateService(_settings()).check()
    assert status.passed is False
    assert len(status.reasons) >= 4


def test_flag_off_alone_fails_even_if_everything_else_is_configured():
    overrides = dict(_FULLY_CONFIGURED)
    overrides["EXTERNAL_CODING_RUNTIMES_ENABLED"] = False
    status = SecurityGateService(_settings(**overrides)).check()
    assert status.passed is False
    assert any("EXTERNAL_CODING_RUNTIMES_ENABLED" in r for r in status.reasons)


def test_entra_id_configuration_also_satisfies_the_identity_requirement():
    overrides = dict(_FULLY_CONFIGURED)
    del overrides["OIDC_ISSUER"]
    del overrides["OIDC_AUDIENCE"]
    overrides["ENTRA_TENANT_ID"] = "tenant"
    overrides["ENTRA_CLIENT_ID"] = "client"
    status = SecurityGateService(_settings(**overrides)).check()
    assert status.passed is True


def test_no_identity_provider_at_all_fails():
    overrides = dict(_FULLY_CONFIGURED)
    del overrides["OIDC_ISSUER"]
    del overrides["OIDC_AUDIENCE"]
    status = SecurityGateService(_settings(**overrides)).check()
    assert status.passed is False
    assert any("identity provider" in r for r in status.reasons)


def test_no_github_app_fails_even_with_everything_else_configured():
    overrides = dict(_FULLY_CONFIGURED)
    overrides["GITHUB_APP_ID"] = None
    status = SecurityGateService(_settings(**overrides)).check()
    assert status.passed is False
    assert any("GitHub App" in r for r in status.reasons)


def test_require_security_gate_passed_raises_with_every_reason(monkeypatch=None):
    with pytest.raises(SecurityGateNotPassedError) as exc_info:
        require_security_gate_passed(_settings())
    assert len(exc_info.value.reasons) >= 4


def test_require_security_gate_passed_is_silent_when_fully_configured():
    require_security_gate_passed(_settings(**_FULLY_CONFIGURED))  # must not raise


# --- Integration with Phase 06's dispatcher selection --------------------------------------


def test_celery_mode_is_refused_by_select_dispatcher_when_gate_fails(monkeypatch):
    import app.services.agent_jobs as aj
    from app.runtime_security.security_gate import SecurityGateNotPassedError

    monkeypatch.setattr(aj, "get_settings", lambda: _settings(AGENT_JOB_DISPATCHER_MODE="celery"))
    with pytest.raises(SecurityGateNotPassedError):
        aj.select_dispatcher()


def test_inline_mode_never_consults_the_security_gate_at_all(monkeypatch):
    """The default, preserved-synchronous-mode path is never gated —
    confirmed by making the gate raise if it's ever even constructed."""
    import app.services.agent_jobs as aj
    import app.runtime_security.security_gate as gate_module

    def _explode(*a, **k):
        raise AssertionError("security gate must not be consulted for inline mode")

    monkeypatch.setattr(gate_module, "require_security_gate_passed", _explode)
    monkeypatch.setattr(aj, "get_settings", lambda: _settings(AGENT_JOB_DISPATCHER_MODE="inline"))

    dispatcher = aj.select_dispatcher()
    assert dispatcher.name == "inline"
