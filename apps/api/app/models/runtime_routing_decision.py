from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class RuntimeRoutingDecision(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One persisted RuntimeCostRouter.route() call — "record requested
    runtime/model, candidate options, rejection reasons, selected
    runtime/model, estimated cost, actual cost, escalation reason, cost
    owner" (Phase 17's own literal requirement), plus the fields
    app/services/runtime_cost_metrics.py's aggregate functions read.

    `actual_cost_usd` starts NULL and is filled in by a caller once the
    selected runtime actually finishes (this table is written at
    decision time, before execution) — never fabricated ahead of time.
    `artifact_approved`/`patch_accepted`/`story_merged` similarly start
    False and are set by whatever downstream event later confirms them,
    for the cost-per-outcome metrics.
    """

    __tablename__ = "runtime_routing_decisions"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)

    requested_runtime: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(100), nullable=False)
    candidates_json: Mapped[list] = mapped_column(JSON, nullable=False)

    selected_runtime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    selected_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cache_savings_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_owner: Mapped[str] = mapped_column(String(30), nullable=False)

    stop_for_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Downstream outcome flags — all start False, set later by whatever
    # event actually confirms them (never inferred at decision time).
    artifact_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    patch_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    story_merged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    execution_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    project: Mapped["Project"] = relationship("Project")
