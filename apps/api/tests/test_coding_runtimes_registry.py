"""Phase 08: 'Register OpenCode as an available but feature-flagged
runtime.' See app/services/coding_runtimes.py."""

from app.core.config import get_settings
from app.services.coding_runtimes import build_coding_runtime_registry


def test_opencode_is_registered_even_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "OPENCODE_RUNTIME_ENABLED", False, raising=False)
    registry = build_coding_runtime_registry()
    assert "opencode" in registry
    assert registry["opencode"].enabled is False


def test_opencode_capability_manifest_is_well_formed():
    registry = build_coding_runtime_registry()
    manifest = registry["opencode"].capability
    assert manifest.runtime_name == "opencode"
    assert manifest.max_context_tokens > 0
    assert manifest.supports_streaming is True


def test_opencode_enabled_flag_reflects_settings(monkeypatch):
    monkeypatch.setattr(get_settings(), "OPENCODE_RUNTIME_ENABLED", True, raising=False)
    registry = build_coding_runtime_registry()
    assert registry["opencode"].enabled is True
