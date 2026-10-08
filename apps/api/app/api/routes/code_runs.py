"""CodeRun endpoints — the Story Code Implementation Agent's Flow steps
4-9, built on app/services/story_code_implementation.py: apply an
already-ACCEPTED story-scoped ImplementationRun's patch through a real,
isolated git workspace (app/services/code_runner.py), run configured
tests, and only on success commit/push a real branch and move the
story's lane toward its GitHub PR stage.

Distinct from app/api/routes/implementation_runs.py's create_pull_request
(GitHub REST API, file-by-file, no local checkout) — that action is
completely unchanged by this router; this is the local-git alternative.

RULE — "Do not apply changes without user approval": POST /code-runs
refuses anything but an ACCEPTED ImplementationRun (see
create_code_run's own check).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.github_integration import decrypt_repository_token
from app.core.database import get_db
from app.models import CodeRun, ImplementationRun, ProjectEngineeringSetup, PullRequestLink, Repository, Story, User
from app.schemas.code_run import ApplyViaCodeRunnerRequest, CodeRunRead, CreatePrFromCodeRunRequest
from app.schemas.implementation_run import PullRequestLinkRead
from app.services.audit import record_audit_log
from app.services.story_code_implementation import (
    StoryCodeImplementationError,
    create_code_run,
    create_pull_request_from_code_run,
    run_code_implementation_pipeline,
)

router = APIRouter(prefix="/code-runs", tags=["code-runs"])


@router.post("", response_model=CodeRunRead, status_code=status.HTTP_201_CREATED)
def apply_via_code_runner(payload: ApplyViaCodeRunnerRequest, db: Session = Depends(get_db)) -> CodeRun:
    """Story Code Implementation Agent, Flow steps 4-9. Story-scoped
    ImplementationRuns only — project-level runs keep using
    create_pull_request unchanged."""
    run = db.get(ImplementationRun, payload.implementation_run_id)
    if run is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"implementation_run_id {payload.implementation_run_id} does not match an existing implementation run",
        )
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    if run.story_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "apply-via-code-runner is only available for story-scoped implementation runs.")
    story = db.get(Story, run.story_id)
    if story is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Run {run.id}'s story no longer exists.")

    repository = db.query(Repository).filter(Repository.project_id == run.project_id).order_by(Repository.created_at.desc()).first()
    if repository is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot apply — connect a GitHub repository first.")
    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    # Project Engineering Setup rule 5 — "CodeRunner requires build/test
    # command config." A project with no ProjectEngineeringSetup (or one
    # with a setup but no command_config, e.g. an older setup created
    # before this field existed) is ungated (rule 10). Rule 7 ("CodeRunner
    # can only run allowlisted commands") stays enforced independently and
    # unconditionally by app/services/code_runner.py's own
    # settings.CODE_RUNNER_ALLOWED_TEST_EXECUTABLES check, regardless of
    # where test_commands came from.
    engineering_setup = db.query(ProjectEngineeringSetup).filter(ProjectEngineeringSetup.project_id == run.project_id).first()
    test_commands = payload.test_commands
    if engineering_setup is not None:
        if engineering_setup.command_config is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot apply — this project's engineering setup has no build/test command configuration yet.",
            )
        if not test_commands:
            test_commands = engineering_setup.command_config.test_commands

    try:
        code_run = create_code_run(db, implementation_run=run, repository=repository, triggered_by=triggered_by)
    except StoryCodeImplementationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    record_audit_log(
        db, project_id=run.project_id, actor_user_id=triggered_by.id, action="code_run.started",
        entity_type="CodeRun", entity_id=code_run.id, extra_data={"story_id": str(story.id), "branch_name": code_run.branch_name},
    )
    db.commit()

    code_run = run_code_implementation_pipeline(
        db, code_run=code_run, implementation_run=run, repository=repository, story=story,
        base_branch=base_branch, test_commands=test_commands,
    )
    db.commit()
    db.refresh(code_run)
    return code_run


@router.get("/{code_run_id}", response_model=CodeRunRead)
def get_code_run(code_run_id: uuid.UUID, db: Session = Depends(get_db)) -> CodeRun:
    code_run = db.get(CodeRun, code_run_id)
    if code_run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CodeRun {code_run_id} not found")
    return code_run


@router.post("/{code_run_id}/create-pull-request", response_model=PullRequestLinkRead, status_code=status.HTTP_201_CREATED)
def create_pull_request(code_run_id: uuid.UUID, payload: CreatePrFromCodeRunRequest, db: Session = Depends(get_db)) -> PullRequestLink:
    """"Implement GitHub PR creation for story lane" — creates a real
    GitHub PR from a CodeRun's already-pushed branch (no further commits
    — CodeRunnerService already pushed everything real). RULE — "PR
    creation requires pushed branch"."""
    code_run = db.get(CodeRun, code_run_id)
    if code_run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"CodeRun {code_run_id} not found")
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    if code_run.implementation_run_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"CodeRun {code_run_id} has no associated implementation run.")
    implementation_run = db.get(ImplementationRun, code_run.implementation_run_id)
    if implementation_run is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"CodeRun {code_run_id}'s implementation run no longer exists.")
    story = db.get(Story, code_run.story_id)
    if story is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"CodeRun {code_run_id}'s story no longer exists.")
    repository = db.get(Repository, code_run.repository_id)
    if repository is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"CodeRun {code_run_id}'s repository no longer exists.")
    base_branch = payload.base_branch or repository.default_branch
    if not base_branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No base_branch given and this repository has no known default branch.")

    github_token = decrypt_repository_token(repository)  # a real write cannot silently degrade to "no token"

    try:
        link = create_pull_request_from_code_run(
            db, code_run=code_run, implementation_run=implementation_run, story=story, repository=repository,
            base_branch=base_branch, github_token=github_token, triggered_by=triggered_by,
        )
    except StoryCodeImplementationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    db.commit()
    db.refresh(link)
    return link
