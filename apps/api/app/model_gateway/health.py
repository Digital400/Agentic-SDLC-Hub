"""ProviderHealthCache — removes a live connectivity check from every
single request's hot path.

THE GAP THIS CLOSES: the Phase 00 architecture baseline found that
app/services/ai_generation.py's get_active_provider() — called on the hot
path of every single agent-run/Approve request — performs a real network
probe (an HTTP GET for OpenRouter/NVIDIA, an Ollama client.list() call)
synchronously, every time. That behavior is INTENTIONALLY PRESERVED
UNCHANGED on the legacy path (see app/model_gateway/legacy_gateway.py's
own docstring — the legacy path is preserved byte-for-byte behind the
Settings.MODEL_GATEWAY_MODE flag). This module is what every NEW gateway
adapter uses instead: a probe result is cached for
Settings.MODEL_GATEWAY_HEALTH_CACHE_TTL_SECONDS, so at most one real probe
happens per TTL window, no matter how many requests arrive in it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone

from app.model_gateway.base import ProviderHealth


class ProviderHealthCache:
    """Process-local, thread-safe, TTL-based cache of one ProviderHealth
    result per provider key. Deliberately simple (no background refresh
    thread, no cross-process sharing) — a stale-by-at-most-TTL health
    result is an acceptable trade for removing the check from the hot
    path entirely; a background refresher would be a reasonable
    enhancement but isn't needed to satisfy this phase's requirement.
    """

    def __init__(self, ttl_seconds: float):
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cache: dict[str, ProviderHealth] = {}

    def get_or_check(self, provider_key: str, check_fn: Callable[[], ProviderHealth]) -> ProviderHealth:
        """Returns the cached result if it's still within the TTL window;
        otherwise runs `check_fn()` once, caches, and returns the fresh
        result. `check_fn` is only ever invoked while holding the lock and
        only when the cache entry is missing/stale — two callers racing
        on a cold cache still only trigger one real probe."""
        with self._lock:
            cached = self._cache.get(provider_key)
            if cached is not None and self._age_seconds(cached) < self._ttl_seconds:
                return cached
            fresh = check_fn()
            self._cache[provider_key] = fresh
            return fresh

    def invalidate(self, provider_key: str | None = None) -> None:
        """Force the next get_or_check to re-probe — `None` clears every
        entry. Mainly for tests; production code has no reason to call
        this on the happy path (letting the TTL expire naturally is the
        whole point)."""
        with self._lock:
            if provider_key is None:
                self._cache.clear()
            else:
                self._cache.pop(provider_key, None)

    @staticmethod
    def _age_seconds(health: ProviderHealth) -> float:
        return (datetime.now(timezone.utc) - health.checked_at).total_seconds()


# One process-wide cache, shared by every gateway adapter instance — a
# provider's real-world health doesn't vary per adapter instance, so
# sharing the cache (rather than one per instance) is what actually
# achieves "at most one probe per TTL window" across an entire process,
# not just within one adapter object's lifetime.
_SHARED_HEALTH_CACHE: ProviderHealthCache | None = None


def get_shared_health_cache() -> ProviderHealthCache:
    global _SHARED_HEALTH_CACHE
    if _SHARED_HEALTH_CACHE is None:
        from app.core.config import get_settings

        _SHARED_HEALTH_CACHE = ProviderHealthCache(ttl_seconds=get_settings().MODEL_GATEWAY_HEALTH_CACHE_TTL_SECONDS)
    return _SHARED_HEALTH_CACHE
