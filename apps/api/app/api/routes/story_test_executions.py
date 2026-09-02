"""Story Testing stage endpoints — see app/services/story_test_execution.py
and app/models/story_test_execution.py for the full requirement mapping."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import CodeRun, Story, StoryTestExecution, User, UserRole
from app.schemas.story_test_execution import (
    AttachCodeRunLogRequest,
    GenerateChecklistRequest,
    QaApproveRequest,
    RecordTestResultsRequest,
    StartStoryTestExecutionRequest,
    StoryTestExecutionRead,
)
from app.services.story_test_execution import (
    StoryTestExecutionError,
    attach_code_run_log,
    generate_agent_checklist,
    qa_approve,
    record_manual_results,
    start_story_test_execution,
)

router = APIRouter(prefix="/story-test-executions", tags=["story-test-executions"])


def _get_execution_or_404(db: Session, execution_id: uuid.UUID) -> StoryTestExecution:
    execution = db.get(StoryTestExecution, execution_id)
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story test execution {execution_id} not found")
    return execution


def _get_user_or_400(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"User {user_id} does not match an existing user")
    return user


@router.post("", response_model=StoryTestExecutionRead, status_code=status.HTTP_201_CREATED)
def start_test_execution(payload: StartStoryTestExecutionRequest, db: Session = Depends(get_db)) -> StoryTestExecution:
    story = db.get(Story, payload.story_id)
    if story is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Story {payload.story_id} does not exist")
    if story.delivery_lane is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Story {story.id} has no delivery lane.")
    actor = _get_user_or_400(db, payload.triggered_by_user_id)

    try:
        execution = start_story_test_execution(db, story=story, lane=story.delivery_lane, actor=actor)
    except StoryTestExecutionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    db.commit()
    db.refresh(execution)
    return execution


@router.get("/{execution_id}", response_model=StoryTestExecutionRead)
def get_test_execution(execution_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryTestExecution:
    return _get_execution_or_404(db, execution_id)


@router.get("/by-story/{story_id}", response_model=list[StoryTestExecutionRead])
def list_test_executions_for_story(story_id: uuid.UUID, db: Session = Depends(get_db)) -> list[StoryTestExecution]:
    return (
        db.query(StoryTestExecution)
        .filter(StoryTestExecution.story_id == story_id)
        .order_by(StoryTestExecution.created_at.desc())
        .all()
    )


@router.post("/{execution_id}/generate-checklist", response_model=StoryTestExecutionRead)
def generate_checklist(execution_id: uuid.UUID, payload: GenerateChecklistRequest, db: Session = Depends(get_db)) -> StoryTestExecution:
    execution = _get_execution_or_404(db, execution_id)
    actor = _get_user_or_400(db, payload.triggered_by_user_id)

    generate_agent_checklist(db, execution=execution, actor=actor)

    db.commit()
    db.refresh(execution)
    return execution


@router.post("/{execution_id}/attach-code-run", response_model=StoryTestExecutionRead)
def attach_code_run(execution_id: uuid.UUID, payload: AttachCodeRunLogRequest, db: Session = Depends(get_db)) -> StoryTestExecution:
    execution = _get_execution_or_404(db, execution_id)
    actor = _get_user_or_400(db, payload.triggered_by_user_id)
    code_run = db.get(CodeRun, payload.code_run_id)
    if code_run is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"CodeRun {payload.code_run_id} does not exist")

    try:
        attach_code_run_log(db, execution=execution, code_run=code_run, actor=actor)
    except StoryTestExecutionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    db.commit()
    db.refresh(execution)
    return execution


@router.patch("/{execution_id}/results", response_model=StoryTestExecutionRead)
def record_results(execution_id: uuid.UUID, payload: RecordTestResultsRequest, db: Session = Depends(get_db)) -> StoryTestExecution:
    """Requirements 1/4/5 — manual test result entry, pass/fail per
    scenario, and bug creation suggestions in one call."""
    execution = _get_execution_or_404(db, execution_id)
    actor = _get_user_or_400(db, payload.triggered_by_user_id)

    try:
        record_manual_results(
            db, execution=execution,
            results=[r.model_dump() for r in payload.results],
            evidence_urls=payload.evidence_urls, bugs_found=payload.bugs_found, actor=actor,
        )
    except StoryTestExecutionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    db.commit()
    db.refresh(execution)
    return execution


@router.post("/{execution_id}/qa-approve", response_model=StoryTestExecutionRead)
def qa_approve_execution(execution_id: uuid.UUID, payload: QaApproveRequest, db: Session = Depends(get_db)) -> StoryTestExecution:
    """Requirement 6, QA approval — QA or Admin only, same role gate as
    every other review gate in this lane (LLD_REVIEW/HUMAN_CODE_REVIEW/
    QA_APPROVAL's own generic-endpoint gate)."""
    execution = _get_execution_or_404(db, execution_id)
    actor = _get_user_or_400(db, payload.actor_user_id)
    if actor.role not in (UserRole.QA, UserRole.ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role {actor.role.value} may not record a QA decision — QA only.")

    try:
        qa_approve(db, execution=execution, decision=payload.decision, actor=actor, reason=payload.reason)
    except StoryTestExecutionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    db.commit()
    db.refresh(execution)
    return execution
