"""AI Ops Dashboard endpoint — company-wide agent/review metrics.

Covers exactly one read: GET /ops/summary. All the actual aggregation
logic lives in app/services/ops_metrics.py, kept separate so it can be
unit-tested (or reused by a future scheduled report) without importing
FastAPI machinery.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.ops import OpsSummaryRead
from app.services.ops_metrics import build_ops_summary

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/summary", response_model=OpsSummaryRead)
def get_ops_summary(db: Session = Depends(get_db)) -> OpsSummaryRead:
    return OpsSummaryRead.model_validate(build_ops_summary(db))
