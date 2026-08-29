from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """Basic liveness check for the API."""
    return {"status": "ok"}
