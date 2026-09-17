"""FakeModelGateway — a fully deterministic, in-process ModelGateway for
tests. Never makes a network call; every behavior (content, token counts,
cost, failures, simulated latency) is configured explicitly by the test
that constructs it.

Configurable failure modes are what make app/model_gateway/router.py's
retry/fallback/timeout behavior testable without touching a real network
or the real Ollama/OpenRouter/LiteLLM adapters — see
tests/test_model_gateway_router.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, GatewayResponse, ModelGateway, ModelGatewayError, ModelGatewayTimeoutError, ProviderHealth
from app.model_gateway.cost import Cost


@dataclass
class FakeCallOutcome:
    """One scripted outcome for one call. `raises` takes priority over
    everything else when set."""

    content_markdown: str = "# Fake Output\n\nDeterministic test content."
    needs_clarification: bool = False
    clarification_questions: list[str] = field(default_factory=list)
    prompt_tokens: int = 100
    completion_tokens: int = 50
    cached_tokens: int = 0
    cost: Cost = field(default_factory=Cost.free)
    raises: Exception | None = None  # e.g. ModelGatewayTimeoutError("simulated timeout")


class FakeModelGateway(ModelGateway):
    """`outcomes_by_alias` scripts what happens per alias, consumed
    front-to-back on each successive call to that alias (so a test can
    script "fails twice, then succeeds" for retry tests). An alias with no
    outcomes left, or never configured, raises ModelGatewayError — a
    test's own scripting mistake should be loud, not silently ignored.
    `health_by_provider` lets a test control check_health()'s result
    independent of whether generate() itself would succeed."""

    name = "fake"

    def __init__(
        self,
        *,
        outcomes_by_alias: dict[ModelAlias, list[FakeCallOutcome]] | None = None,
        default_outcome: FakeCallOutcome | None = None,
        healthy: bool = True,
        model_name_by_alias: dict[ModelAlias, str] | None = None,
    ):
        self._outcomes_by_alias = {alias: list(outcomes) for alias, outcomes in (outcomes_by_alias or {}).items()}
        self._default_outcome = default_outcome
        self._healthy = healthy
        self._model_name_by_alias = model_name_by_alias or {}
        self.call_log: list[ModelAlias] = []  # every alias actually attempted, in order — for asserting retry/fallback behavior

    def generate(self, request: GatewayRequest) -> GatewayResponse:
        self.call_log.append(request.alias)
        outcome = self._next_outcome(request.alias)

        if outcome.raises is not None:
            raise outcome.raises

        model_name = self._model_name_by_alias.get(request.alias, f"fake-model-for-{request.alias.value}")
        return GatewayResponse(
            content_markdown=outcome.content_markdown,
            needs_clarification=outcome.needs_clarification,
            clarification_questions=list(outcome.clarification_questions),
            requested_alias=request.alias,
            requested_provider="fake",
            requested_model=model_name,
            actual_provider="fake",
            actual_model=model_name,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            cached_tokens=outcome.cached_tokens,
            total_tokens=outcome.prompt_tokens + outcome.completion_tokens,
            cost=outcome.cost,
            used_mock=True,
            latency_seconds=0.0,
        )

    def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider="fake", healthy=self._healthy, checked_at=datetime.now(timezone.utc), detail=None if self._healthy else "simulated unhealthy")

    def _next_outcome(self, alias: ModelAlias) -> FakeCallOutcome:
        queue = self._outcomes_by_alias.get(alias)
        if queue:
            return queue.pop(0)
        if self._default_outcome is not None:
            return self._default_outcome
        raise ModelGatewayError(f"FakeModelGateway has no scripted outcome for alias '{alias.value}' — configure outcomes_by_alias or default_outcome.")
