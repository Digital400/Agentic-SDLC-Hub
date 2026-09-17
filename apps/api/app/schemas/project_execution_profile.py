"""ProjectExecutionProfile contracts — see
app/models/project_execution_profile.py for the versioning/approval model
these wrap, and app/services/execution_profile_service.py for the
propose/approve/reject logic that produces/consumes them.

HARD RULE, repeated at every layer that touches environment variables
(model column doc, this schema's validator, and
app/services/execution_profile_detection.py's own docstring): a profile
may only ever carry environment-variable NAMES, never values. This is
enforced structurally here, not just documented — see
EnvironmentVariableName's pattern constraint below.
"""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    DataClassification,
    NetworkPolicyDefault,
    ProjectExecutionProfileSource,
    ProjectExecutionProfileStatus,
    ProjectExecutionProfileType,
)

# A plain environment-variable NAME (POSIX shell identifier shape) — never
# permits '=', whitespace, or anything else that would let a VALUE (or a
# NAME=VALUE pair) slip through disguised as a name. See module docstring.
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NetworkPolicy(BaseModel):
    """Outbound network access policy for a future coding runtime — data
    only in Phase 03, not enforced by any runtime yet (see
    app/agent_runtime's own Phase 01 scope note). `default` mirrors
    ScopePolicy/ToolPolicy's fail-closed `deny_by_default` convention in
    app/agent_runtime/policies.py — DENY unless a domain is explicitly
    allow-listed."""

    model_config = ConfigDict(extra="forbid")

    default: NetworkPolicyDefault = NetworkPolicyDefault.DENY
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list, description="Always wins over allowed_domains on overlap.")


class QualityGate(BaseModel):
    """One quality gate a profile requires — same shape as
    app/agent_runtime's RequiredCheck (Phase 01), kept as an independent
    schema rather than importing that package directly (see
    ProjectExecutionProfile.quality_gates' column docstring for why)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    severity: Literal["BLOCKING", "ADVISORY"] = "BLOCKING"
    command: str | None = None


def _validate_env_var_names(names: list[str]) -> list[str]:
    for name in names:
        if "=" in name:
            raise ValueError(f"environment_variable_names entry '{name}' contains '=' — only bare NAMES are allowed, never a NAME=VALUE pair or a value.")
        if not _ENV_VAR_NAME_RE.match(name):
            raise ValueError(f"environment_variable_names entry '{name}' is not a valid identifier-shaped name (letters, digits, underscore; cannot start with a digit).")
    return names


class ProjectExecutionProfileBase(BaseModel):
    """Fields shared by every write path (detect-propose, template-propose,
    and the eventual manual-edit path) — factored out so
    ProjectExecutionProfileRead doesn't repeat every Field(...) constraint
    a second time."""

    model_config = ConfigDict(extra="forbid")

    default_branch: str | None = None
    working_directories: list[str] = Field(default_factory=list)
    detected_languages: list[str] = Field(default_factory=list)
    detected_frameworks: list[str] = Field(default_factory=list)
    package_manager: str | None = None
    runtime_image: str | None = None

    install_command: str | None = None
    lint_command: str | None = None
    format_check_command: str | None = None
    type_check_command: str | None = None
    unit_test_command: str | None = None
    integration_test_command: str | None = None
    build_command: str | None = None
    approved_security_scan_commands: list[str] = Field(default_factory=list)

    allowed_command_patterns: list[str] = Field(default_factory=list)
    denied_command_patterns: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    network_policy: NetworkPolicy = Field(default_factory=NetworkPolicy)
    environment_variable_names: list[str] = Field(default_factory=list, description="NAMES ONLY — see module docstring's HARD RULE. Never a value.")

    branch_naming_convention: str | None = None
    commit_convention: str | None = None
    required_coding_skills: list[str] = Field(default_factory=list)
    required_testing_skills: list[str] = Field(default_factory=list)
    quality_gates: list[QualityGate] = Field(default_factory=list)
    data_classification: DataClassification = DataClassification.INTERNAL

    @field_validator("environment_variable_names")
    @classmethod
    def _no_values_in_env_var_names(cls, v: list[str]) -> list[str]:
        return _validate_env_var_names(v)


class ProjectExecutionProfileRead(ProjectExecutionProfileBase):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    status: ProjectExecutionProfileStatus
    is_active: bool
    profile_type: ProjectExecutionProfileType
    source: ProjectExecutionProfileSource
    template_key: str | None
    repository_id: uuid.UUID | None
    repository_snapshot_id: uuid.UUID | None
    detection_metadata: dict
    created_by_id: uuid.UUID
    submitted_at: datetime | None
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None
    rejected_by_id: uuid.UUID | None
    rejected_at: datetime | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


class ProposeFromDetectionRequest(BaseModel):
    """EXISTING_REPOSITORY flow — detect and propose. `repository_snapshot_id`
    must belong to a Repository already configured for this project (see
    app/api/routes/github_integration.py's snapshot endpoints, Phase 00
    baseline)."""

    repository_snapshot_id: uuid.UUID
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — attributed as this proposed version's created_by.")
    github_token: str | None = Field(
        None,
        exclude=True,
        description="NEVER PERSISTED, NEVER LOGGED, NEVER RETURNED — see app/services/execution_profile_detection.py. Optional: omit to fall back to the repository's own stored, encrypted connection token, same as every other GitHub-reading endpoint in this codebase.",
    )


class ProposeFromTemplateRequest(BaseModel):
    """NEW_PROJECT flow — select an approved company stack/template. See
    app/services/execution_profile_templates.py's APPROVED_STACK_TEMPLATES
    for the closed set of valid `template_key` values."""

    template_key: str = Field(..., min_length=1)
    triggered_by_user_id: uuid.UUID
    # A human may still want to name a target repo/branch even before
    # scaffolding creates one — purely descriptive at this stage, not a
    # real Repository row (see ProjectExecutionProfile.repository_id,
    # nullable exactly for this reason).
    default_branch: str | None = Field(None, description="Overrides the template's own default_branch suggestion, if given.")


class ApproveExecutionProfileRequest(BaseModel):
    approved_by_user_id: uuid.UUID = Field(..., description="Must hold the Project Owner role (PRODUCT_OWNER) or ADMIN — see app/services/permissions.py's require_can_approve_execution_profile.")


class RejectExecutionProfileRequest(BaseModel):
    rejected_by_user_id: uuid.UUID
    reason: str = Field(..., min_length=1)


class ExecutionProfileTemplateRead(BaseModel):
    """One entry from the approved company stack/template catalog — see
    app/services/execution_profile_templates.py."""

    model_config = ConfigDict(extra="forbid")

    template_key: str
    name: str
    description: str
    languages: list[str]
    frameworks: list[str]
    package_manager: str | None
    runtime_image: str | None
