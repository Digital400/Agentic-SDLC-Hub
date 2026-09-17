"""Tests for app/model_gateway/health.py's ProviderHealthCache — the
mechanism that removes a live connectivity check from the request hot
path (this phase's explicit requirement)."""

from datetime import datetime, timedelta, timezone

from app.model_gateway.base import ProviderHealth
from app.model_gateway.health import ProviderHealthCache


def _health(healthy: bool = True, checked_at: datetime | None = None) -> ProviderHealth:
    return ProviderHealth(provider="test", healthy=healthy, checked_at=checked_at or datetime.now(timezone.utc))


def test_first_call_always_performs_a_real_check():
    cache = ProviderHealthCache(ttl_seconds=60)
    calls = []

    def check():
        calls.append(1)
        return _health()

    cache.get_or_check("provider-a", check)
    assert len(calls) == 1


def test_second_call_within_ttl_does_not_re_check():
    cache = ProviderHealthCache(ttl_seconds=60)
    calls = []

    def check():
        calls.append(1)
        return _health()

    cache.get_or_check("provider-a", check)
    cache.get_or_check("provider-a", check)
    cache.get_or_check("provider-a", check)
    assert len(calls) == 1  # this IS "removed from the hot path" — 3 requests, 1 real probe


def test_call_after_ttl_expires_re_checks():
    cache = ProviderHealthCache(ttl_seconds=0.05)
    calls = []

    def check():
        calls.append(1)
        return _health()

    cache.get_or_check("provider-a", check)
    import time

    time.sleep(0.1)
    cache.get_or_check("provider-a", check)
    assert len(calls) == 2


def test_different_providers_are_cached_independently():
    cache = ProviderHealthCache(ttl_seconds=60)
    a_calls, b_calls = [], []
    cache.get_or_check("provider-a", lambda: (a_calls.append(1), _health())[1])
    cache.get_or_check("provider-b", lambda: (b_calls.append(1), _health())[1])
    cache.get_or_check("provider-a", lambda: (a_calls.append(1), _health())[1])
    assert len(a_calls) == 1
    assert len(b_calls) == 1


def test_invalidate_one_provider_forces_a_recheck():
    cache = ProviderHealthCache(ttl_seconds=60)
    calls = []
    cache.get_or_check("provider-a", lambda: (calls.append(1), _health())[1])
    cache.invalidate("provider-a")
    cache.get_or_check("provider-a", lambda: (calls.append(1), _health())[1])
    assert len(calls) == 2


def test_invalidate_all_clears_every_entry():
    cache = ProviderHealthCache(ttl_seconds=60)
    calls = []
    cache.get_or_check("provider-a", lambda: (calls.append(1), _health())[1])
    cache.get_or_check("provider-b", lambda: (calls.append(1), _health())[1])
    cache.invalidate()
    cache.get_or_check("provider-a", lambda: (calls.append(1), _health())[1])
    cache.get_or_check("provider-b", lambda: (calls.append(1), _health())[1])
    assert len(calls) == 4


def test_cached_result_reflects_original_unhealthy_status():
    cache = ProviderHealthCache(ttl_seconds=60)
    cache.get_or_check("provider-a", lambda: _health(healthy=False))
    result = cache.get_or_check("provider-a", lambda: _health(healthy=True))  # should not even be called
    assert result.healthy is False
