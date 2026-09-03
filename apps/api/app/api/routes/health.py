from fastapi import APIRouter

from app.core.config import get_settings
from app.services.ai_generation import get_active_provider

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """Basic liveness check for the API."""
    return {"status": "ok"}


@router.get("/health/provider-debug")
def provider_debug() -> dict[str, object]:
    """TEMPORARY diagnostic — never returns a key's actual value, only
    whether each is non-empty, plus which provider get_active_provider()
    resolves to in *this* running process. Added to pin down a real,
    repeatedly-reported bug where the live server kept resolving to the
    mock provider despite a valid key in apps/api/.env."""
    settings = get_settings()
    return {
        "active_provider": get_active_provider(),
        "anthropic_key_set": bool(settings.ANTHROPIC_API_KEY),
        "gemini_key_set": bool(settings.GEMINI_API_KEY),
        "openrouter_key_set": bool(settings.OPENROUTER_API_KEY),
        "openrouter_key_len": len(settings.OPENROUTER_API_KEY or ""),
        "nvidia_key_set": bool(settings.NVIDIA_API_KEY),
    }
