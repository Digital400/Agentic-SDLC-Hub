import uuid

from pydantic import BaseModel, ConfigDict


class AgentDefinitionRead(BaseModel):
    """Read model for the Prompt Library's agent cards — see
    GET /agents. Distinct from AgentPromptRead/AgentRunRead in
    app/schemas/prompt.py and app/schemas/agent_run.py."""

    # protected_namespaces=() — "model_name" collides with Pydantic's
    # reserved "model_" prefix (used for its own model_* methods); this is
    # a plain data field, not one of those, so the warning is a false alarm.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    agent_key: str
    name: str
    description: str | None
    model_name: str
    is_active: bool

    # Denormalized — how many times this agent has actually run, across
    # every project. Not a plain attribute, so always built via
    # from_orm_agent rather than left to automatic serialization.
    total_runs: int

    @classmethod
    def from_orm_agent(cls, agent, total_runs: int) -> "AgentDefinitionRead":
        return cls(
            id=agent.id,
            agent_key=agent.agent_key,
            name=agent.name,
            description=agent.description,
            model_name=agent.model_name,
            is_active=agent.is_active,
            total_runs=total_runs,
        )
