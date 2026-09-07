"""Project Engineering Setup — the "configure engineering before agents
can run" step a new project's Create Project wizard walks through (see
app/api/routes/project_engineering_setup.py). One ProjectEngineeringSetup
row per project (unique project_id), with six 1:1 or 1:many child tables
covering each wizard step's own data.

Deliberately separate from (not a replacement for) the tables it
references:
- ProjectRepositoryConfig records the SETUP CHOICE and gates Implementation
  on it; the actual connected repo is still a real Repository row (see
  app/models/repository.py) — CONNECT_EXISTING_REPO/CREATE_NEW_REPO both
  still need a human to finish that connection through the existing
  GitHub integration flow afterward.
- ProjectJiraConfig mirrors that same relationship for JiraProjectLink.
- ProjectCodingStandard/ProjectGuardrail are plain per-project text, always
  injected into agent context (see app/api/routes/agent_runs.py's
  _merge_engineering_setup_context and
  app/services/implementation_agent.py's project_coding_standards/
  project_guardrails parameters) — NOT retrieved via the Knowledge Base's
  RAG pipeline (app/services/retrieval.py), which stays a separate,
  semantic-similarity-based source of COMPANY_STANDARD content.

Rule 10 ("do not break existing project creation"): every gate this
introduces (see the *_runs.py routes that check these tables) treats a
project with NO ProjectEngineeringSetup row as ungated — exactly today's
behavior — so every project created before this feature existed keeps
working unchanged.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DocumentationTarget, GithubSetupOption, JiraSetupOption


class ProjectEngineeringSetup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """The umbrella row — one per project. Step 2 (Technology Stack)'s own
    fields live directly here since they're plain, single-valued, and
    don't need a child table of their own; steps 3-8 each get a dedicated
    child table below."""

    __tablename__ = "project_engineering_setups"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    # --- Step 2: Technology Stack ---------------------------------------------------
    # Free-text, not enums — the space of real-world stacks is far larger
    # than a fixed list could name; validated as non-blank, nothing more
    # (see app/schemas/project_engineering_setup.py).
    application_type: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_language: Mapped[str] = mapped_column(String(100), nullable=False)
    frontend_framework: Mapped[str | None] = mapped_column(String(100), nullable=True)
    backend_framework: Mapped[str | None] = mapped_column(String(100), nullable=True)
    database: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cloud_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    created_by: Mapped["User"] = relationship("User")
    repository_config: Mapped["ProjectRepositoryConfig | None"] = relationship(
        "ProjectRepositoryConfig", back_populates="setup", uselist=False, cascade="all, delete-orphan"
    )
    jira_config: Mapped["ProjectJiraConfig | None"] = relationship(
        "ProjectJiraConfig", back_populates="setup", uselist=False, cascade="all, delete-orphan"
    )
    coding_standards: Mapped[list["ProjectCodingStandard"]] = relationship(
        "ProjectCodingStandard", back_populates="setup", cascade="all, delete-orphan", order_by="ProjectCodingStandard.order_index"
    )
    guardrails: Mapped[list["ProjectGuardrail"]] = relationship(
        "ProjectGuardrail", back_populates="setup", cascade="all, delete-orphan", order_by="ProjectGuardrail.order_index"
    )
    documentation_config: Mapped["ProjectDocumentationConfig | None"] = relationship(
        "ProjectDocumentationConfig", back_populates="setup", uselist=False, cascade="all, delete-orphan"
    )
    command_config: Mapped["ProjectCommandConfig | None"] = relationship(
        "ProjectCommandConfig", back_populates="setup", uselist=False, cascade="all, delete-orphan"
    )


class ProjectRepositoryConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Step 3: GitHub Setup. `repository_id` is null until a human actually
    finishes connecting/creating the repo through the existing GitHub
    integration flow (app/api/routes/github_integration.py) — this table
    only records intent plus the PR-creation conventions (rule 8)."""

    __tablename__ = "project_repository_configs"

    setup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_engineering_setups.id", ondelete="CASCADE"), nullable=False, unique=True)
    option: Mapped[GithubSetupOption] = mapped_column(Enum(GithubSetupOption, native_enum=False, length=30, validate_strings=True), nullable=False)
    # Set once CONNECT_EXISTING_REPO/CREATE_NEW_REPO is actually finished —
    # see app/api/routes/project_engineering_setup.py's link_repository.
    repository_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True)
    # CREATE_NEW_REPO's declared intent before the repo itself exists.
    new_repo_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Rule 8 — PR creation requires both of these; defaults match what
    # implementation_runs.py's create_pull_request already did before this
    # config existed, so a project that only sets option (no explicit
    # naming/branch override) behaves identically.
    branch_naming_pattern: Mapped[str] = mapped_column(String(255), nullable=False, default="agent/{task}-{run_id}")
    target_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")

    setup: Mapped["ProjectEngineeringSetup"] = relationship("ProjectEngineeringSetup", back_populates="repository_config")
    repository: Mapped["Repository | None"] = relationship("Repository")


class ProjectJiraConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Step 4: Jira Setup. `jira_project_link_id` is null until a human
    actually finishes connecting through the existing Jira integration
    flow (app/api/routes/jira_integration.py)."""

    __tablename__ = "project_jira_configs"

    setup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_engineering_setups.id", ondelete="CASCADE"), nullable=False, unique=True)
    option: Mapped[JiraSetupOption] = mapped_column(Enum(JiraSetupOption, native_enum=False, length=30, validate_strings=True), nullable=False)
    jira_project_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jira_project_links.id", ondelete="SET NULL"), nullable=True)

    setup: Mapped["ProjectEngineeringSetup"] = relationship("ProjectEngineeringSetup", back_populates="jira_config")
    jira_project_link: Mapped["JiraProjectLink | None"] = relationship("JiraProjectLink")


class ProjectCodingStandard(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Step 5: Company Coding Standards — one row per named standard
    (naming conventions, error handling, formatting, ...), always injected
    verbatim into agent context (rule 6) rather than retrieved via RAG."""

    __tablename__ = "project_coding_standards"

    setup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_engineering_setups.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    setup: Mapped["ProjectEngineeringSetup"] = relationship("ProjectEngineeringSetup", back_populates="coding_standards")


class ProjectGuardrail(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Step 6: Agent Guardrails — one row per rule (e.g. "never touch
    payment code without human review", "always add a test for a new
    endpoint"), always injected verbatim into agent context (rule 6)."""

    __tablename__ = "project_guardrails"

    setup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_engineering_setups.id", ondelete="CASCADE"), nullable=False)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    setup: Mapped["ProjectEngineeringSetup"] = relationship("ProjectEngineeringSetup", back_populates="guardrails")


class ProjectDocumentationConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Step 7: Documentation Flow — rule 9's "approved artifacts can
    publish to configured documentation target" reads this to decide
    which of the already-existing publish actions
    (app/services/story_confluence_publish.py, app/services/github_export.py)
    are actually offered/used for this project."""

    __tablename__ = "project_documentation_configs"

    setup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_engineering_setups.id", ondelete="CASCADE"), nullable=False, unique=True)
    target: Mapped[DocumentationTarget] = mapped_column(Enum(DocumentationTarget, native_enum=False, length=30, validate_strings=True), nullable=False)

    setup: Mapped["ProjectEngineeringSetup"] = relationship("ProjectEngineeringSetup", back_populates="documentation_config")


class ProjectCommandConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Step 8: Build/Test Commands. Rule 5 — CodeRunner (see
    app/api/routes/code_runs.py) refuses to run for a project with no
    ProjectCommandConfig row at all; `test_commands` here is also the
    default POST /code-runs falls back to when the caller doesn't pass its
    own. Rule 7 ("CodeRunner can only run allowlisted commands") is
    enforced independently and unconditionally by
    app/services/code_runner.py's own settings.CODE_RUNNER_ALLOWED_TEST_EXECUTABLES
    check — this config's commands still pass through that same check,
    same as any explicitly-passed ones; nothing here bypasses it."""

    __tablename__ = "project_command_configs"

    setup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_engineering_setups.id", ondelete="CASCADE"), nullable=False, unique=True)
    build_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    test_commands: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    lint_command: Mapped[str | None] = mapped_column(String(500), nullable=True)

    setup: Mapped["ProjectEngineeringSetup"] = relationship("ProjectEngineeringSetup", back_populates="command_config")
