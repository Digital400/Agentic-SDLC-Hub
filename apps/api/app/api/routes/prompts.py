"""Prompt Library endpoints.

Covers: create a prompt (its first version), list prompts, get the active
prompt for an agent, update a version in place, create a new version, and
activate a version. Versioning mirrors Artifact/ArtifactVersion: a "prompt"
is really a lineage of immutable-ish AgentPrompt rows sharing
(agent_definition_id, role), with exactly one marked `is_active` at a time.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import AgentDefinition, AgentPrompt, AgentPromptRole, User
from app.schemas.prompt import (
    AgentPromptActivateRequest,
    AgentPromptCreate,
    AgentPromptRead,
    AgentPromptUpdate,
    AgentPromptVersionCreate,
)
from app.services.audit import record_audit_log
from app.services.permissions import require_can_update_prompt

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _get_agent_definition_or_400(db: Session, agent_key: str) -> AgentDefinition:
    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == agent_key).first()
    if agent is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"agent_key '{agent_key}' does not match an existing agent")
    return agent


def _get_prompt_or_404(db: Session, prompt_id: uuid.UUID) -> AgentPrompt:
    prompt = db.get(AgentPrompt, prompt_id)
    if prompt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Prompt {prompt_id} not found")
    return prompt


def _get_user_or_400(db: Session, user_id: uuid.UUID, field_name: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field_name} {user_id} does not match an existing user")
    return user


# 1. Create agent prompt ---------------------------------------------------------


@router.post("", response_model=AgentPromptRead, status_code=status.HTTP_201_CREATED)
def create_prompt(payload: AgentPromptCreate, db: Session = Depends(get_db)) -> AgentPrompt:
    agent = _get_agent_definition_or_400(db, payload.agent_key)

    existing = (
        db.query(AgentPrompt)
        .filter(AgentPrompt.agent_definition_id == agent.id, AgentPrompt.role == payload.role)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A {payload.role.value} prompt already exists for '{payload.agent_key}'; use "
            "POST /prompts/{id}/versions to add a new version instead.",
        )

    prompt = AgentPrompt(
        agent_definition=agent,
        role=payload.role,
        version=1,
        name=payload.name,
        stage=payload.stage,
        system_prompt=payload.system_prompt,
        output_format=payload.output_format,
        validation_checklist=payload.validation_checklist,
        is_active=True,  # the only version so far
    )
    db.add(prompt)
    db.flush()

    record_audit_log(
        db,
        action="agent_prompt.created",
        entity_type="AgentPrompt",
        entity_id=prompt.id,
        extra_data={"agent_key": payload.agent_key, "role": payload.role.value, "version": 1},
    )

    db.commit()
    db.refresh(prompt)
    return AgentPromptRead.from_orm_prompt(prompt)


# 2. List prompts -----------------------------------------------------------------


@router.get("", response_model=list[AgentPromptRead])
def list_prompts(
    db: Session = Depends(get_db),
    agent_key: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
) -> list[AgentPromptRead]:
    query = db.query(AgentPrompt).join(AgentDefinition)
    if agent_key is not None:
        query = query.filter(AgentDefinition.agent_key == agent_key)
    if stage is not None:
        query = query.filter(AgentPrompt.stage == stage)
    if is_active is not None:
        query = query.filter(AgentPrompt.is_active == is_active)

    prompts = query.order_by(AgentDefinition.agent_key, AgentPrompt.role, AgentPrompt.version).all()
    return [AgentPromptRead.from_orm_prompt(p) for p in prompts]


# 3. Get prompt by agent key -----------------------------------------------------


@router.get("/{agent_key}", response_model=AgentPromptRead)
def get_prompt_by_agent_key(
    agent_key: str, db: Session = Depends(get_db), role: AgentPromptRole = Query(default=AgentPromptRole.DRAFT)
) -> AgentPromptRead:
    """Returns the currently active prompt for this agent's given role
    (defaults to DRAFT, the only role in active use today)."""
    agent = _get_agent_definition_or_400(db, agent_key)

    prompt = (
        db.query(AgentPrompt)
        .filter(AgentPrompt.agent_definition_id == agent.id, AgentPrompt.role == role, AgentPrompt.is_active.is_(True))
        .first()
    )
    if prompt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No active {role.value} prompt for agent '{agent_key}'")
    return AgentPromptRead.from_orm_prompt(prompt)


# 4. Update prompt ------------------------------------------------------------------


@router.patch("/{prompt_id}", response_model=AgentPromptRead)
def update_prompt(prompt_id: uuid.UUID, payload: AgentPromptUpdate, db: Session = Depends(get_db)) -> AgentPromptRead:
    """Edits this version in place. Only allowed while it's NOT the active
    version — an activated prompt is live; changing it silently would mean
    agent behavior changes without a new, reviewable version. Use
    POST /prompts/{id}/versions instead once a prompt is active."""
    prompt = _get_prompt_or_404(db, prompt_id)
    editor = _get_user_or_400(db, payload.updated_by_id, "updated_by_id")
    require_can_update_prompt(editor)

    if prompt.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot edit the active version in place — create a new version instead.",
        )

    changes: dict[str, object] = {}
    for field in ("name", "stage", "system_prompt", "output_format", "validation_checklist"):
        value = getattr(payload, field)
        if value is not None:
            changes[field] = value
            setattr(prompt, field, value)

    if changes:
        record_audit_log(
            db,
            action="agent_prompt.updated",
            entity_type="AgentPrompt",
            entity_id=prompt.id,
            extra_data={"fields": list(changes.keys())},
        )

    db.commit()
    db.refresh(prompt)
    return AgentPromptRead.from_orm_prompt(prompt)


# 5. Version prompt -----------------------------------------------------------------


@router.post("/{prompt_id}/versions", response_model=AgentPromptRead, status_code=status.HTTP_201_CREATED)
def create_prompt_version(
    prompt_id: uuid.UUID, payload: AgentPromptVersionCreate, db: Session = Depends(get_db)
) -> AgentPromptRead:
    """Creates a new version in the same (agent, role) lineage as
    `prompt_id`. Starts inactive — see POST /prompts/{id}/activate."""
    base = _get_prompt_or_404(db, prompt_id)
    creator = _get_user_or_400(db, payload.created_by_id, "created_by_id")
    require_can_update_prompt(creator)

    last_version_number = (
        db.query(AgentPrompt.version)
        .filter(AgentPrompt.agent_definition_id == base.agent_definition_id, AgentPrompt.role == base.role)
        .order_by(AgentPrompt.version.desc())
        .limit(1)
        .scalar()
    )
    next_version_number = (last_version_number or 0) + 1

    new_version = AgentPrompt(
        agent_definition_id=base.agent_definition_id,
        role=base.role,
        version=next_version_number,
        name=payload.name,
        stage=payload.stage,
        system_prompt=payload.system_prompt,
        output_format=payload.output_format,
        validation_checklist=payload.validation_checklist,
        is_active=False,
    )
    db.add(new_version)
    db.flush()

    record_audit_log(
        db,
        action="agent_prompt.version_created",
        entity_type="AgentPrompt",
        entity_id=new_version.id,
        extra_data={"agent_key": base.agent_definition.agent_key, "role": base.role.value, "version": next_version_number},
    )

    db.commit()
    db.refresh(new_version)
    return AgentPromptRead.from_orm_prompt(new_version)


# 6. Activate prompt version -----------------------------------------------------------


@router.post("/{prompt_id}/activate", response_model=AgentPromptRead)
def activate_prompt_version(
    prompt_id: uuid.UUID, payload: AgentPromptActivateRequest, db: Session = Depends(get_db)
) -> AgentPromptRead:
    """Makes this version the active one; deactivates every other version
    in its (agent, role) lineage — exactly one active version at a time."""
    prompt = _get_prompt_or_404(db, prompt_id)
    activator = _get_user_or_400(db, payload.activated_by_id, "activated_by_id")
    require_can_update_prompt(activator)

    if prompt.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This version is already active.")

    siblings = (
        db.query(AgentPrompt)
        .filter(
            AgentPrompt.agent_definition_id == prompt.agent_definition_id,
            AgentPrompt.role == prompt.role,
            AgentPrompt.id != prompt.id,
            AgentPrompt.is_active.is_(True),
        )
        .all()
    )
    for sibling in siblings:
        sibling.is_active = False

    prompt.is_active = True

    record_audit_log(
        db,
        action="agent_prompt.activated",
        entity_type="AgentPrompt",
        entity_id=prompt.id,
        extra_data={
            "agent_key": prompt.agent_definition.agent_key,
            "role": prompt.role.value,
            "version": prompt.version,
            "deactivated_versions": [s.version for s in siblings],
        },
    )

    db.commit()
    db.refresh(prompt)
    return AgentPromptRead.from_orm_prompt(prompt)
