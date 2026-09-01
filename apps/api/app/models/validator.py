from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ValidatorDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Configuration for one workflow stage's independent validator agent —
    see app/services/validator_agent.py, called by
    app/services/loop_engine.py's LoopEngineService after every
    GENERATE_DRAFT/IMPROVE step.

    Deliberately a separate concept from AgentPrompt/AgentPromptRole.VALIDATE
    (the drafting agent's own optional self-check role, human-triggered as
    a top-level run action): a validator here is a second, independent
    agent that always runs automatically as part of the loop, scoring the
    draft rather than producing content. One per workflow stage — matched
    by `stage` (a WorkflowNode.node_key) — with a stage-less fallback (see
    validator_agent.run_validator) when a stage has none configured yet.

    No real AI call is required — falls back to a deterministic heuristic
    when no provider is configured (see get_active_provider), same
    fallback convention as AgentDefinition/mock_agent.py.
    """

    __tablename__ = "validator_definitions"
    __table_args__ = (UniqueConstraint("stage"),)

    validator_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # WorkflowNode.node_key this validator checks drafts for.
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # The loop's stop-condition threshold for this stage (see
    # app/services/loop_engine.py's DEFAULT_QUALITY_THRESHOLD) — a stage
    # whose output matters more can require a higher bar before its loop
    # stops on "quality met".
    quality_threshold: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)
    # This stage's own rubric — independent of AgentPrompt.validation_checklist
    # (the drafting agent's self-check): this is what the separate
    # validator agent checks completeness against.
    criteria: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
