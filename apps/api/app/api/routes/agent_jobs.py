"""AgentJob endpoints — Phase 06's durable job execution surface.

Polling first (this phase's explicit "Add polling endpoints first"):
GET /agent-jobs/{id} and GET /agent-jobs/{id}/events are the real,
fully-tested primary interface — a caller polls status/events on whatever
interval it likes, exactly like every other run/job resource in this
codebase already works (ImplementationRun, TestRun, PRReviewRun — Phase
00 baseline). GET /agent-jobs/{id}/stream is the OPTIONAL secondary
addition: a plain Server-Sent-Events wrapper around the same polling
query, for a caller that wants push-shaped delivery without a new
transport/protocol this codebase doesn't otherwise use.
"""

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import AgentJob, User
from app.schemas.agent_job import (
    AgentJobEventRead,
    AgentJobRead,
    CancelAgentJobRequest,
    ContinueAgentJobRequest,
    StartAgentJobRequest,
)
from app.services.agent_jobs import AgentJobError, AgentJobService, select_dispatcher
from app.services.agent_jobs.dispatcher import UnknownHandlerError, get_handler

router = APIRouter(prefix="/agent-jobs", tags=["agent-jobs"])


def _get_user_or_400(db: Session, user_id: uuid.UUID, *, field_name: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field_name} {user_id} does not match an existing user")
    return user


def _get_job_or_404(db: Session, job_id: uuid.UUID) -> AgentJob:
    job = db.get(AgentJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent job {job_id} not found")
    return job


@router.post("", response_model=AgentJobRead, status_code=status.HTTP_201_CREATED)
def start_agent_job(payload: StartAgentJobRequest, db: Session = Depends(get_db)) -> AgentJobRead:
    try:
        get_handler(payload.handler_name)
    except UnknownHandlerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    triggered_by = None
    if payload.triggered_by_user_id is not None:
        triggered_by = _get_user_or_400(db, payload.triggered_by_user_id, field_name="triggered_by_user_id")

    service = AgentJobService(db)
    job, created = service.create_job(work_packet=payload.work_packet, created_by=triggered_by, idempotency_key=payload.idempotency_key)
    db.commit()
    db.refresh(job)

    if not created:
        # An existing job with this idempotency_key was found — return it
        # as-is, never re-submit/re-run it (that's the entire point of an
        # idempotency key).
        return AgentJobRead.model_validate(job)

    dispatcher = select_dispatcher()
    dispatcher.submit(job_id=job.id, handler_name=payload.handler_name, db=db)
    db.commit()
    db.refresh(job)
    return AgentJobRead.model_validate(job)


@router.get("/{job_id}", response_model=AgentJobRead)
def get_agent_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> AgentJobRead:
    return AgentJobRead.model_validate(_get_job_or_404(db, job_id))


@router.get("/{job_id}/events", response_model=list[AgentJobEventRead])
def list_agent_job_events(
    job_id: uuid.UUID, since_sequence: int = Query(0, ge=0, description="Only events with sequence > this value — the incremental-polling parameter."),
    db: Session = Depends(get_db),
) -> list[AgentJobEventRead]:
    _get_job_or_404(db, job_id)
    events = AgentJobService(db).list_events(job_id, since_sequence=since_sequence)
    return [AgentJobEventRead.model_validate(e) for e in events]


@router.post("/{job_id}/cancel", response_model=AgentJobRead)
def cancel_agent_job(job_id: uuid.UUID, payload: CancelAgentJobRequest, db: Session = Depends(get_db)) -> AgentJobRead:
    job = _get_job_or_404(db, job_id)
    requester = _get_user_or_400(db, payload.requested_by_user_id, field_name="requested_by_user_id")

    service = AgentJobService(db)
    try:
        service.request_cancellation(job, requested_by=requester)
    except AgentJobError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    db.commit()
    db.refresh(job)
    return AgentJobRead.model_validate(job)


@router.post("/{job_id}/continue", response_model=AgentJobRead, status_code=status.HTTP_201_CREATED)
def continue_agent_job(job_id: uuid.UUID, payload: ContinueAgentJobRequest, db: Session = Depends(get_db)) -> AgentJobRead:
    """Resumes a WAITING_INPUT/WAITING_APPROVAL job — always creates a NEW
    job (continuation_of_job_id points back at `job_id`); the original job
    row is never mutated or re-entered. See AgentJobService.
    create_continuation's own docstring for why."""
    original = _get_job_or_404(db, job_id)
    triggered_by = None
    if payload.triggered_by_user_id is not None:
        triggered_by = _get_user_or_400(db, payload.triggered_by_user_id, field_name="triggered_by_user_id")

    service = AgentJobService(db)
    try:
        job, created = service.create_continuation(
            original_job=original, updated_work_packet=payload.updated_work_packet, created_by=triggered_by, idempotency_key=payload.idempotency_key,
        )
    except AgentJobError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    db.refresh(job)

    if not created:
        return AgentJobRead.model_validate(job)

    dispatcher = select_dispatcher()
    dispatcher.submit(job_id=job.id, handler_name="default", db=db)
    db.commit()
    db.refresh(job)
    return AgentJobRead.model_validate(job)


# --- Optional event streaming (secondary to the polling endpoints above) -----------------


_STREAM_POLL_INTERVAL_SECONDS = 1.0
_STREAM_MAX_ITERATIONS = 3600  # ~1 hour at the poll interval above — a stream must not run forever


@router.get("/{job_id}/stream")
async def stream_agent_job_events(job_id: uuid.UUID, db: Session = Depends(get_db)):
    """OPTIONAL — see module docstring. A plain Server-Sent-Events wrapper
    around the same GET /{job_id}/events polling query; not a new
    transport this codebase didn't already conceptually have (HTTP long-
    lived GET), just a push-shaped convenience. Stops once the job reaches
    a terminal status or after _STREAM_MAX_ITERATIONS polls, whichever
    comes first — never streams indefinitely."""
    _get_job_or_404(db, job_id)

    async def _event_source():
        service = AgentJobService(db)
        last_sequence = 0
        for _ in range(_STREAM_MAX_ITERATIONS):
            job = service.get_or_404(job_id)
            events = service.list_events(job_id, since_sequence=last_sequence)
            for event in events:
                last_sequence = event.sequence
                yield f"event: {event.event_type.value}\ndata: {json.dumps({'sequence': event.sequence, 'payload': event.payload})}\n\n"
            if job.status.value in ("COMPLETED", "FAILED", "CANCELLED", "STALE", "WAITING_INPUT", "WAITING_APPROVAL"):
                yield f"event: STREAM_END\ndata: {json.dumps({'status': job.status.value})}\n\n"
                return
            await asyncio.sleep(_STREAM_POLL_INTERVAL_SECONDS)

    return StreamingResponse(_event_source(), media_type="text/event-stream")
