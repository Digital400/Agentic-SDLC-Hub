import uuid

from pydantic import BaseModel, ConfigDict


class ValidatorDefinitionRead(BaseModel):
    """Read model for GET /validators — see app/models/validator.py and
    app/services/validator_agent.py."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    validator_key: str
    name: str
    stage: str
    description: str | None
    model_name: str
    quality_threshold: float
    criteria: list[str]
    is_active: bool
