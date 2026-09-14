import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CodingStandardCategory, DocumentationTarget, GithubSetupOption, JiraSetupOption
from app.schemas.validators import NonBlankStr


# --- Step 2: Technology Stack ---------------------------------------------------------


class TechnologyStackInput(BaseModel):
    application_type: NonBlankStr = Field(..., max_length=100)
    primary_language: NonBlankStr = Field(..., max_length=100)
    frontend_framework: str | None = Field(default=None, max_length=100)
    backend_framework: str | None = Field(default=None, max_length=100)
    database: str | None = Field(default=None, max_length=100)
    cloud_provider: str | None = Field(default=None, max_length=100)


# --- Step 3: GitHub Setup --------------------------------------------------------------


class RepositorySetupInput(BaseModel):
    option: GithubSetupOption
    new_repo_name: str | None = Field(default=None, max_length=255)
    branch_naming_pattern: NonBlankStr = Field(default="agent/{task}-{run_id}", max_length=255)
    target_branch: NonBlankStr = Field(default="main", max_length=255)


class RepositoryConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    option: GithubSetupOption
    repository_id: uuid.UUID | None
    new_repo_name: str | None
    branch_naming_pattern: str
    target_branch: str


# --- Step 4: Jira Setup -----------------------------------------------------------------


class JiraSetupInput(BaseModel):
    option: JiraSetupOption


class JiraConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    option: JiraSetupOption
    jira_project_link_id: uuid.UUID | None


# --- Step 5/6: Coding Standards / Guardrails ---------------------------------------------


class CodingStandardInput(BaseModel):
    title: NonBlankStr = Field(..., max_length=255)
    content: NonBlankStr
    # Which Agent Context Builder rule section this standard is grouped
    # under (see app/services/agent_context_builder.py) — GENERAL for one
    # that isn't specifically architecture/security/testing/git/docs.
    category: CodingStandardCategory = CodingStandardCategory.GENERAL


class CodingStandardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    content: str
    category: CodingStandardCategory
    order_index: int


class GuardrailInput(BaseModel):
    rule_text: NonBlankStr


class GuardrailRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_text: str
    order_index: int


# --- Step 7: Documentation Flow ----------------------------------------------------------


class DocumentationSetupInput(BaseModel):
    target: DocumentationTarget = DocumentationTarget.INTERNAL_ONLY


class DocumentationConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target: DocumentationTarget


# --- Step 8: Build/Test Commands ---------------------------------------------------------


class CommandSetupInput(BaseModel):
    build_command: str | None = Field(default=None, max_length=500)
    test_commands: list[str] = Field(default_factory=list)
    lint_command: str | None = Field(default=None, max_length=500)


class CommandConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    build_command: str | None
    test_commands: list[str]
    lint_command: str | None


# --- Step 9: Review & Create — the one call that saves everything at once -----------------


class CreateEngineeringSetupRequest(BaseModel):
    """Body for POST /projects/{id}/engineering-setup — saves every wizard
    step's data in one call, from Step 9's "Review & Create" action.
    Steps 3/4 (GitHub/Jira) only record intent here; actually connecting a
    real repository/Jira project still goes through the existing
    integration flows (app/api/routes/github_integration.py,
    app/api/routes/jira_integration.py) afterward — see
    ProjectRepositoryConfig/ProjectJiraConfig's own docstrings."""

    created_by_id: uuid.UUID = Field(..., description="Existing user id.")
    technology_stack: TechnologyStackInput
    repository: RepositorySetupInput
    jira: JiraSetupInput
    coding_standards: list[CodingStandardInput] = Field(default_factory=list)
    guardrails: list[GuardrailInput] = Field(default_factory=list)
    documentation: DocumentationSetupInput = DocumentationSetupInput()
    commands: CommandSetupInput = CommandSetupInput()


class EngineeringSetupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    application_type: str
    primary_language: str
    frontend_framework: str | None
    backend_framework: str | None
    database: str | None
    cloud_provider: str | None
    created_at: datetime
    updated_at: datetime

    repository_config: RepositoryConfigRead | None
    jira_config: JiraConfigRead | None
    coding_standards: list[CodingStandardRead]
    guardrails: list[GuardrailRead]
    documentation_config: DocumentationConfigRead | None
    command_config: CommandConfigRead | None

    @classmethod
    def from_orm_setup(cls, setup) -> "EngineeringSetupRead":
        return cls(
            id=setup.id,
            project_id=setup.project_id,
            application_type=setup.application_type,
            primary_language=setup.primary_language,
            frontend_framework=setup.frontend_framework,
            backend_framework=setup.backend_framework,
            database=setup.database,
            cloud_provider=setup.cloud_provider,
            created_at=setup.created_at,
            updated_at=setup.updated_at,
            repository_config=RepositoryConfigRead.model_validate(setup.repository_config) if setup.repository_config else None,
            jira_config=JiraConfigRead.model_validate(setup.jira_config) if setup.jira_config else None,
            coding_standards=[CodingStandardRead.model_validate(s) for s in setup.coding_standards],
            guardrails=[GuardrailRead.model_validate(g) for g in setup.guardrails],
            documentation_config=DocumentationConfigRead.model_validate(setup.documentation_config) if setup.documentation_config else None,
            command_config=CommandConfigRead.model_validate(setup.command_config) if setup.command_config else None,
        )


class UpdateEngineeringSetupRequest(BaseModel):
    """Body for PATCH /projects/{id}/engineering-setup — edits an
    already-created setup (the same data the Create Project wizard
    collected, now viewable/editable afterward instead of write-once).
    Every section is optional; a caller only passes the ones actually
    being changed, same "only touch what's given" convention as
    app/schemas/project.py's ProjectUpdate.

    coding_standards/guardrails are a full replacement of that list when
    passed — not a merge of individual items — the same shape the wizard
    itself submits, and these rows have no independent identity a caller
    could address one at a time from the edit form.

    repository/jira here only ever touch the *intent* fields (option,
    branch naming pattern, target branch) — repository_id/
    jira_project_link_id stay controlled exclusively by
    link_repository/link_jira_project below (an actual finished
    connection, never something this edit form can set directly)."""

    updated_by_id: uuid.UUID = Field(..., description="Existing user id.")
    technology_stack: TechnologyStackInput | None = None
    repository: RepositorySetupInput | None = None
    jira: JiraSetupInput | None = None
    coding_standards: list[CodingStandardInput] | None = None
    guardrails: list[GuardrailInput] | None = None
    documentation: DocumentationSetupInput | None = None
    commands: CommandSetupInput | None = None


class LinkRepositoryRequest(BaseModel):
    """Body for POST /projects/{id}/engineering-setup/link-repository —
    called once a human finishes actually connecting/creating the repo
    through the existing GitHub integration flow, to record which real
    Repository row fulfills this project's ProjectRepositoryConfig."""

    repository_id: uuid.UUID


class LinkJiraProjectRequest(BaseModel):
    """Body for POST /projects/{id}/engineering-setup/link-jira-project —
    mirrors LinkRepositoryRequest for Jira."""

    jira_project_link_id: uuid.UUID
