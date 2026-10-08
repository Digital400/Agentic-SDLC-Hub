from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StoryTestExecutionQaDecision, StoryTestExecutionStatus


class StartStoryTestExecutionRequest(BaseModel):
    story_id: uuid.UUID
    triggered_by_user_id: uuid.UUID


class ResultItem(BaseModel):
    scenario: str
    status: str = Field(..., description="PASS, FAIL, or BLOCKED")
    notes: str = ""


class RecordTestResultsRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    results: list[ResultItem] = Field(..., min_length=1)
    evidence_urls: list[str] = Field(default_factory=list)
    bugs_found: list[str] = Field(default_factory=list)


class GenerateChecklistRequest(BaseModel):
    triggered_by_user_id: uuid.UUID


class AttachCodeRunLogRequest(BaseModel):
    triggered_by_user_id: uuid.UUID
    code_run_id: uuid.UUID


class QaApproveRequest(BaseModel):
    actor_user_id: uuid.UUID
    decision: str = Field(..., description="APPROVED or REJECTED")
    reason: str = ""


class StoryTestExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    story_id: uuid.UUID
    lane_id: uuid.UUID | None
    test_scenario_artifact_id: uuid.UUID | None
    pull_request_link_id: uuid.UUID | None
    executed_by_user_id: uuid.UUID | None
    status: StoryTestExecutionStatus
    results_json: list[dict]
    agent_checklist: list[dict]
    evidence_urls: list[str]
    bugs_found: list[str]
    qa_decision: StoryTestExecutionQaDecision
    qa_decision_reason: str
    qa_decided_by_user_id: uuid.UUID | None
    used_mock: bool
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
