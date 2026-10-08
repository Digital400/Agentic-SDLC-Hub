import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ReleaseStatus
from app.schemas.story import StoryRead


class ReleaseCreate(BaseModel):
    """Stories are NOT added here — see POST /releases/{id}/stories. A
    release always starts empty; membership is its own explicit action
    (requirement 1 — mirrors SprintCreate's own convention)."""

    project_id: uuid.UUID
    name: str = Field(..., max_length=255)
    version: str = Field(..., max_length=50)
    target_date: date | None = None
    created_by_id: uuid.UUID


class ReleaseUpdate(BaseModel):
    """All fields optional except the actor — only what's provided
    changes. `release_notes` may be hand-edited here after generation.
    Refused once the release is APPROVED/RELEASED/CANCELLED — see
    _require_release_editable in app/api/routes/releases.py."""

    name: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=50)
    target_date: date | None = None
    release_notes: str | None = None
    updated_by_id: uuid.UUID = Field(..., description="Existing user id — the actor making this change.")


class ReleaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: str
    target_date: date | None
    status: ReleaseStatus
    release_notes: str
    created_by_id: uuid.UUID
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AddStoryToReleaseRequest(BaseModel):
    story_id: uuid.UUID
    actor_user_id: uuid.UUID = Field(..., description="Existing user id — the actor adding this story.")


class RemoveStoryFromReleaseRequest(BaseModel):
    actor_user_id: uuid.UUID


class ReleaseStoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    story_id: uuid.UUID
    added_by_id: uuid.UUID
    created_at: datetime


class ReleaseBoardItem(BaseModel):
    release_story: ReleaseStoryRead
    story: StoryRead


class ReleaseBoardRead(BaseModel):
    release: ReleaseRead
    items: list[ReleaseBoardItem]


class GenerateReleaseNotesRequest(BaseModel):
    triggered_by_user_id: uuid.UUID


class ReleaseLifecycleRequest(BaseModel):
    """POST /releases/{id}/approve — no other fields needed, the
    transition itself is the whole action (requirement 7's approval
    gate)."""

    actor_user_id: uuid.UUID
