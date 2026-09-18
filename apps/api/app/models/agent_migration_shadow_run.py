from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AgentMigrationShadowRun(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Phase 16: one side-by-side comparison of a legacy agent call against
    its v2 (WorkPacket/PromptCompiler/ExecutionResult) replacement — "run
    legacy and new versions in shadow mode. Compare quality, clarification
    rate, tokens, cost and human acceptance."

    The legacy result is always what's actually persisted as the real
    Artifact/ArtifactVersion (this table never changes what a human sees)
    — see app/services/agent_migration_shadow.py's module docstring for
    exactly how this row is produced and why `human_acceptance` starts
    NULL and is only ever filled in later, out of band.
    """

    __tablename__ = "agent_migration_shadow_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    node_key: Mapped[str] = mapped_column(String(100), nullable=False)

    legacy_total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    legacy_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    legacy_needs_clarification: Mapped[bool] = mapped_column(Boolean, nullable=False)
    legacy_content_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    v2_total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    v2_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    v2_needs_clarification: Mapped[bool] = mapped_column(Boolean, nullable=False)
    v2_content_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    v2_repair_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # A trivial, deterministic quality signal only — "did both paths agree
    # on whether clarification was needed." A real quality score requires
    # either a human rating or an LLM-judge pass, neither of which this
    # phase adds (disclosed in the architecture doc's Remaining risks);
    # this field is honestly named for what it actually measures.
    clarification_outcomes_matched: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # NULL until a human reviews the v2 draft out of band (e.g. via a
    # future admin comparison UI) and records ACCEPTED/REJECTED here —
    # never fabricated or inferred from any automatic signal.
    human_acceptance: Mapped[str | None] = mapped_column(String(20), nullable=True)

    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    project: Mapped["Project"] = relationship("Project")
