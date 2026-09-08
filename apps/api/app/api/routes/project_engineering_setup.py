"""Project Engineering Setup endpoints — the Create Project wizard's own
data (see app/models/project_engineering_setup.py's module docstring for
the full rationale and how this relates to Repository/JiraProjectLink).

One POST creates the whole setup from the wizard's "Review & Create" step;
two small link-* actions record when a human finishes actually connecting
a repo/Jira project through the existing integration flows afterward.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.core.database import get_db
from app.models import (
    JiraProjectLink,
    Project,
    ProjectCodingStandard,
    ProjectCommandConfig,
    ProjectDocumentationConfig,
    ProjectEngineeringSetup,
    ProjectGuardrail,
    ProjectJiraConfig,
    ProjectRepositoryConfig,
    Repository,
    User,
)
from app.schemas.project_engineering_setup import (
    CreateEngineeringSetupRequest,
    EngineeringSetupRead,
    LinkJiraProjectRequest,
    LinkRepositoryRequest,
)
from app.services.audit import record_audit_log

router = APIRouter(prefix="/projects/{project_id}/engineering-setup", tags=["project-engineering-setup"])


def _get_project_or_404(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    return project


def _get_setup_with_relations(db: Session, project_id: uuid.UUID) -> ProjectEngineeringSetup | None:
    return (
        db.query(ProjectEngineeringSetup)
        .options(
            joinedload(ProjectEngineeringSetup.repository_config),
            joinedload(ProjectEngineeringSetup.jira_config),
            joinedload(ProjectEngineeringSetup.coding_standards),
            joinedload(ProjectEngineeringSetup.guardrails),
            joinedload(ProjectEngineeringSetup.documentation_config),
            joinedload(ProjectEngineeringSetup.command_config),
        )
        .filter(ProjectEngineeringSetup.project_id == project_id)
        .first()
    )


@router.post("", response_model=EngineeringSetupRead, status_code=status.HTTP_201_CREATED)
def create_engineering_setup(
    project_id: uuid.UUID, payload: CreateEngineeringSetupRequest, db: Session = Depends(get_db)
) -> EngineeringSetupRead:
    """Step 9 ("Review & Create") — saves every wizard step in one call.
    One setup per project; calling this again for a project that already
    has one is rejected (use PATCH-style per-step updates once those
    exist, not a silent overwrite of a wizard someone already completed)."""
    project = _get_project_or_404(db, project_id)
    creator = db.get(User, payload.created_by_id)
    if creator is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"created_by_id {payload.created_by_id} does not match an existing user")

    existing = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project_id).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Project {project_id} already has an engineering setup.")

    setup = ProjectEngineeringSetup(
        project_id=project.id,
        created_by_id=creator.id,
        application_type=payload.technology_stack.application_type,
        primary_language=payload.technology_stack.primary_language,
        frontend_framework=payload.technology_stack.frontend_framework,
        backend_framework=payload.technology_stack.backend_framework,
        database=payload.technology_stack.database,
        cloud_provider=payload.technology_stack.cloud_provider,
    )
    db.add(setup)
    db.flush()

    db.add(
        ProjectRepositoryConfig(
            setup_id=setup.id,
            option=payload.repository.option,
            new_repo_name=payload.repository.new_repo_name,
            branch_naming_pattern=payload.repository.branch_naming_pattern,
            target_branch=payload.repository.target_branch,
        )
    )
    db.add(ProjectJiraConfig(setup_id=setup.id, option=payload.jira.option))
    for index, standard in enumerate(payload.coding_standards):
        db.add(
            ProjectCodingStandard(
                setup_id=setup.id, title=standard.title, content=standard.content, category=standard.category, order_index=index
            )
        )
    for index, guardrail in enumerate(payload.guardrails):
        db.add(ProjectGuardrail(setup_id=setup.id, rule_text=guardrail.rule_text, order_index=index))
    db.add(ProjectDocumentationConfig(setup_id=setup.id, target=payload.documentation.target))
    db.add(
        ProjectCommandConfig(
            setup_id=setup.id,
            build_command=payload.commands.build_command,
            test_commands=payload.commands.test_commands,
            lint_command=payload.commands.lint_command,
        )
    )
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=creator.id, action="project_engineering_setup.created",
        entity_type="ProjectEngineeringSetup", entity_id=setup.id,
        extra_data={
            "application_type": setup.application_type, "primary_language": setup.primary_language,
            "github_option": payload.repository.option.value, "jira_option": payload.jira.option.value,
            "documentation_target": payload.documentation.target.value,
            "coding_standard_count": len(payload.coding_standards), "guardrail_count": len(payload.guardrails),
        },
    )

    db.commit()
    setup = _get_setup_with_relations(db, project_id)
    return EngineeringSetupRead.from_orm_setup(setup)


@router.get("", response_model=EngineeringSetupRead | None)
def get_engineering_setup(project_id: uuid.UUID, db: Session = Depends(get_db)) -> EngineeringSetupRead | None:
    _get_project_or_404(db, project_id)
    setup = _get_setup_with_relations(db, project_id)
    return EngineeringSetupRead.from_orm_setup(setup) if setup is not None else None


@router.post("/link-repository", response_model=EngineeringSetupRead)
def link_repository(project_id: uuid.UUID, payload: LinkRepositoryRequest, db: Session = Depends(get_db)) -> EngineeringSetupRead:
    """Called once a human finishes actually connecting/creating the repo
    through the existing GitHub integration flow (POST /github/repositories)
    — records which real Repository row fulfills this project's
    ProjectRepositoryConfig, so Implementation's gate (see
    app/api/routes/implementation_runs.py) can tell "setup says GitHub is
    coming" apart from "GitHub is actually connected now."""
    _get_project_or_404(db, project_id)
    setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project_id).first()
    if setup is None or setup.repository_config is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project_id} has no engineering setup / repository config.")
    repository = db.get(Repository, payload.repository_id)
    if repository is None or repository.project_id != project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"repository_id {payload.repository_id} is not a repository of project {project_id}")

    setup.repository_config.repository_id = repository.id
    db.commit()
    setup = _get_setup_with_relations(db, project_id)
    return EngineeringSetupRead.from_orm_setup(setup)


@router.post("/link-jira-project", response_model=EngineeringSetupRead)
def link_jira_project(project_id: uuid.UUID, payload: LinkJiraProjectRequest, db: Session = Depends(get_db)) -> EngineeringSetupRead:
    """Mirrors link_repository, for Jira — see its own docstring."""
    _get_project_or_404(db, project_id)
    setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == project_id).first()
    if setup is None or setup.jira_config is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project_id} has no engineering setup / Jira config.")
    link = db.get(JiraProjectLink, payload.jira_project_link_id)
    if link is None or link.project_id != project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"jira_project_link_id {payload.jira_project_link_id} is not a Jira link of project {project_id}")

    setup.jira_config.jira_project_link_id = link.id
    db.commit()
    setup = _get_setup_with_relations(db, project_id)
    return EngineeringSetupRead.from_orm_setup(setup)
