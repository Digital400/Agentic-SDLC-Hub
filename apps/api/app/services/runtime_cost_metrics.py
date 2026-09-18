"""Phase 17: persist a RoutingDecision, and the six measurements the
phase names: "cost per approved artifact, cost per accepted patch, cost
per merged story, premium-runtime percentage, cache savings,
failed-attempt cost."

Every aggregate function here reads real, already-persisted
RuntimeRoutingDecision rows — none of them estimate or fabricate a
number when there is no data; they return 0.0 / None as documented per
function, matching this migration's "never fabricate" rule applied to
metrics instead of execution evidence.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.runtime_routing_decision import RuntimeRoutingDecision
from app.services.runtime_cost_router import RoutingDecision, _PREMIUM_CODING_MODEL


def persist_routing_decision(db: Session, *, project_id: uuid.UUID, task_type: str, decision: RoutingDecision) -> RuntimeRoutingDecision:
    is_premium = decision.selected_runtime == _PREMIUM_CODING_MODEL[0] and decision.selected_model == _PREMIUM_CODING_MODEL[1]
    row = RuntimeRoutingDecision(
        project_id=project_id, task_type=task_type,
        requested_runtime=decision.requested_runtime, requested_model=decision.requested_model,
        candidates_json=[asdict(c) for c in decision.candidates],
        selected_runtime=decision.selected_runtime, selected_model=decision.selected_model, is_premium=is_premium,
        estimated_cost_usd=decision.estimated_cost_usd, actual_cost_usd=decision.actual_cost_usd,
        escalation_reason=decision.escalation_reason, cost_owner=decision.cost_owner,
        stop_for_human=decision.stop_for_human, stop_reason=decision.stop_reason,
    )
    db.add(row)
    db.flush()
    return row


def _cost_per(db: Session, project_id: uuid.UUID, outcome_column) -> float | None:
    """Sum of actual_cost_usd across decisions whose outcome flag is
    True, divided by the count of such decisions. None (not 0.0) when
    there are zero qualifying decisions — "no data" is not the same
    number as "zero cost"."""
    rows = (
        db.query(RuntimeRoutingDecision)
        .filter(RuntimeRoutingDecision.project_id == project_id, outcome_column.is_(True))
        .all()
    )
    if not rows:
        return None
    total = sum(r.actual_cost_usd or 0.0 for r in rows)
    return total / len(rows)


def cost_per_approved_artifact(db: Session, project_id: uuid.UUID) -> float | None:
    return _cost_per(db, project_id, RuntimeRoutingDecision.artifact_approved)


def cost_per_accepted_patch(db: Session, project_id: uuid.UUID) -> float | None:
    return _cost_per(db, project_id, RuntimeRoutingDecision.patch_accepted)


def cost_per_merged_story(db: Session, project_id: uuid.UUID) -> float | None:
    return _cost_per(db, project_id, RuntimeRoutingDecision.story_merged)


def premium_runtime_percentage(db: Session, project_id: uuid.UUID) -> float | None:
    """Percentage (0-100) of non-stopped decisions that selected the
    premium candidate. None when there are zero decisions to measure."""
    total = db.query(func.count(RuntimeRoutingDecision.id)).filter(
        RuntimeRoutingDecision.project_id == project_id, RuntimeRoutingDecision.stop_for_human.is_(False),
    ).scalar()
    if not total:
        return None
    premium = db.query(func.count(RuntimeRoutingDecision.id)).filter(
        RuntimeRoutingDecision.project_id == project_id, RuntimeRoutingDecision.is_premium.is_(True),
    ).scalar()
    return (premium / total) * 100


def cache_savings(db: Session, project_id: uuid.UUID) -> float:
    """Sum of cache_savings_usd across every decision — 0.0 (not None)
    is a meaningful "no savings recorded yet" default here, since this
    is a sum, not an average requiring a non-empty denominator."""
    total = db.query(func.coalesce(func.sum(RuntimeRoutingDecision.cache_savings_usd), 0.0)).filter(
        RuntimeRoutingDecision.project_id == project_id
    ).scalar()
    return float(total or 0.0)


def failed_attempt_cost(db: Session, project_id: uuid.UUID) -> float:
    """Sum of actual_cost_usd spent on decisions whose execution
    ultimately failed — real, wasted spend. 0.0 when none failed."""
    rows = (
        db.query(RuntimeRoutingDecision)
        .filter(RuntimeRoutingDecision.project_id == project_id, RuntimeRoutingDecision.execution_failed.is_(True))
        .all()
    )
    return sum(r.actual_cost_usd or 0.0 for r in rows)
