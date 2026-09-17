from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    DataClassification,
    ProjectExecutionProfileSource,
    ProjectExecutionProfileStatus,
    ProjectExecutionProfileType,
)


class ProjectExecutionProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One version of a project's coding-runtime execution profile —
    everything a future coding runtime (see app/agent_runtime, Phase 01;
    no runtime is connected to this yet) would need to know about how to
    build, test, and stay within this project's own guardrails, PLUS the
    guardrails themselves.

    VERSIONING (mirrors AgentPrompt — app/models/agent.py): rows are
    immutable once created; a "new" profile is always a new row with
    `version = previous_version + 1`, never an in-place edit of an
    existing one. At most one row per project may have
    `status == APPROVED and is_active == True` at a time — that row is
    what `execution_profile_gate.py`'s "a coding runtime cannot start
    without an approved active profile" rule checks for. Approving a new
    version demotes the previously-active one to SUPERSEDED (see
    app/services/execution_profile_service.py's approve()) — the full
    version history stays queryable per project for audit purposes
    (`GET /projects/{id}/execution-profiles`), independent of AuditLog,
    which separately records the who/when/why of each transition (see
    app/services/audit.py — every propose/approve/reject call here also
    writes an AuditLog row, same discipline as every other approval flow
    in this codebase).

    profile_type distinguishes which of the two required flows produced
    this row (NEW_PROJECT vs. EXISTING_REPOSITORY); `source` records how
    THIS version's field values were actually derived (DETECTED/TEMPLATE/
    MANUAL) — see ProjectExecutionProfileSource's docstring for why these
    are two different axes.
    """

    __tablename__ = "project_execution_profiles"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ProjectExecutionProfileStatus] = mapped_column(
        Enum(ProjectExecutionProfileStatus, native_enum=False, length=20, validate_strings=True),
        default=ProjectExecutionProfileStatus.DRAFT,
        nullable=False,
    )
    # Exactly one row per project may have is_active=True AND
    # status==APPROVED at once — enforced in
    # app/services/execution_profile_service.py, not a DB constraint
    # (same convention as AgentPrompt.is_active — see that model's
    # docstring for why this codebase's versioned-row pattern already
    # accepts an application-enforced invariant here rather than a
    # partial unique index).
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    profile_type: Mapped[ProjectExecutionProfileType] = mapped_column(
        Enum(ProjectExecutionProfileType, native_enum=False, length=20, validate_strings=True), nullable=False
    )
    source: Mapped[ProjectExecutionProfileSource] = mapped_column(
        Enum(ProjectExecutionProfileSource, native_enum=False, length=10, validate_strings=True), nullable=False
    )
    # Set only for a TEMPLATE-sourced (NEW_PROJECT) profile — which
    # approved company stack/template the user selected. See
    # app/services/execution_profile_templates.py's APPROVED_STACK_TEMPLATES.
    template_key: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # --- Repository identity -------------------------------------------------------
    # Nullable: a NEW_PROJECT profile proposed before scaffolding has
    # happened may have no Repository row yet (see
    # app/models/repository.py — Repository is created once GitHub is
    # actually connected, which for NEW_PROJECT may only happen AFTER
    # this profile is approved and scaffolding runs).
    repository_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True)
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="SET NULL"), nullable=True,
        doc="The snapshot detection was run against, for an EXISTING_REPOSITORY profile — None for NEW_PROJECT.",
    )
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Project structure -----------------------------------------------------------
    working_directories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    detected_languages: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    detected_frameworks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    package_manager: Mapped[str | None] = mapped_column(String(100), nullable=True)
    runtime_image: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="A container/runtime image reference, e.g. 'python:3.13-slim'. Never a value to be run — see execution_profile_detection.py's HARD RULE.")

    # --- Commands — every one of these is DATA a future runtime would
    # invoke; nothing in this codebase's Phase 03 scope ever runs any of
    # them itself (see execution_profile_detection.py's HARD RULE: safe
    # detection may only READ configuration files).
    install_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    lint_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    format_check_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    type_check_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_test_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_test_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    build_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Security-scan commands are APPROVED, not merely detected — a
    # resolver-level distinction from the other commands above (see
    # execution_profile_templates.py / the propose-from-detection service,
    # which only ever populates this from a fixed, reviewed allowlist of
    # known scanner invocations, never from anything found in the repo
    # itself).
    approved_security_scan_commands: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # --- Guardrails ------------------------------------------------------------------
    allowed_command_patterns: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    denied_command_patterns: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    denied_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Shape: {"default": "DENY"|"ALLOW", "allowed_domains": [...], "denied_domains": [...]}
    # — see app/schemas/project_execution_profile.py's NetworkPolicy for
    # the validated shape this column is expected to hold.
    network_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # NAMES ONLY — see the HARD RULE repeated on every layer that touches
    # this field (model, schema, detection service): this column must
    # never contain a variable's VALUE, only which names a runtime should
    # expect to have populated for it out-of-band (e.g. by its own secrets
    # manager). Enforced by app/schemas/project_execution_profile.py's
    # validator, not by this column's type alone.
    environment_variable_names: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    branch_naming_convention: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_convention: Mapped[str | None] = mapped_column(String(255), nullable=True)
    required_coding_skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_testing_skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Shape: [{"name": str, "description": str, "severity": "BLOCKING"|"ADVISORY", "command": str|None}, ...]
    # — deliberately the same shape as app/agent_runtime's RequiredCheck
    # (Phase 01), kept as its own JSON column rather than importing that
    # package's Pydantic model directly, since this is an ORM column, not
    # a wire contract, and the two packages should stay independently
    # versionable (see app/agent_runtime/__init__.py's own versioning
    # note).
    quality_gates: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    data_classification: Mapped[DataClassification] = mapped_column(
        Enum(DataClassification, native_enum=False, length=15, validate_strings=True),
        default=DataClassification.INTERNAL,
        nullable=False,
    )

    # --- Detection provenance ---------------------------------------------------------
    # Raw signals detection found (e.g. which config files were read, what
    # they contained) — kept for audit/debugging of "why was this
    # proposed", never itself interpreted by the approval gate. Empty for
    # a TEMPLATE-sourced profile.
    detection_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # --- Approval lifecycle -----------------------------------------------------------
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship("Project")
    repository: Mapped["Repository | None"] = relationship("Repository")
    repository_snapshot: Mapped["RepositorySnapshot | None"] = relationship("RepositorySnapshot")
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])
    approved_by: Mapped["User | None"] = relationship("User", foreign_keys=[approved_by_id])
    rejected_by: Mapped["User | None"] = relationship("User", foreign_keys=[rejected_by_id])
