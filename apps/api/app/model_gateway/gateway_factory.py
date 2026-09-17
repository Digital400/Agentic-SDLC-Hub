"""select_gateway() — the one place a caller resolves
Settings.MODEL_GATEWAY_MODE/MODEL_GATEWAY_BACKEND into a concrete
ModelGateway instance. See app/model_gateway/__init__.py.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.model_gateway.base import ModelGateway


class UnknownGatewayBackendError(Exception):
    pass


def select_gateway() -> ModelGateway:
    settings = get_settings()

    if settings.MODEL_GATEWAY_MODE == "legacy":
        from app.model_gateway.legacy_gateway import LegacyModelGateway

        return LegacyModelGateway()

    backend = settings.MODEL_GATEWAY_BACKEND
    if backend == "litellm_proxy":
        from app.model_gateway.litellm_gateway import LiteLLMProxyGateway

        return LiteLLMProxyGateway()
    if backend == "openrouter":
        from app.model_gateway.openrouter_gateway import OpenRouterGateway

        return OpenRouterGateway()
    if backend == "ollama":
        from app.model_gateway.ollama_gateway import LocalOllamaGateway

        return LocalOllamaGateway()
    if backend == "fake":
        from app.model_gateway.fake_gateway import FakeModelGateway

        return FakeModelGateway()  # an empty fake with no scripted outcomes — a deployment must not land here by accident; see Settings.MODEL_GATEWAY_BACKEND's own docstring

    raise UnknownGatewayBackendError(f"Unknown MODEL_GATEWAY_BACKEND '{backend}'.")
