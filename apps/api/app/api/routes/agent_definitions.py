"""Read-only Agent Definitions endpoint — backs the Prompt Library's agent
list (one card per SDLC-stage agent). Prompt versions themselves live under
/prompts (see app/api/routes/prompts.py); this only exposes the agent
config each prompt lineage belongs to.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import AgentDefinition, AgentRun
from app.schemas.agent_definition import AgentDefinitionRead

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentDefinitionRead])
def list_agent_definitions(db: Session = Depends(get_db)) -> list[AgentDefinitionRead]:
    run_counts = dict(
        db.query(AgentRun.agent_definition_id, func.count(AgentRun.id)).group_by(AgentRun.agent_definition_id).all()
    )
    agents = db.query(AgentDefinition).order_by(AgentDefinition.name).all()
    return [AgentDefinitionRead.from_orm_agent(a, run_counts.get(a.id, 0)) for a in agents]


@router.get("/{agent_key}", response_model=AgentDefinitionRead)
def get_agent_definition(agent_key: str, db: Session = Depends(get_db)) -> AgentDefinitionRead:
    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == agent_key).first()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent '{agent_key}' not found")
    total_runs = db.query(func.count(AgentRun.id)).filter(AgentRun.agent_definition_id == agent.id).scalar() or 0
    return AgentDefinitionRead.from_orm_agent(agent, total_runs)
