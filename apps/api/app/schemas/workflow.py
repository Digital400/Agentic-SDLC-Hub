import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import WorkflowStatus
from app.schemas.validators import NonBlankStr


class WorkflowNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    node_key: str
    name: str
    description: str
    agent_key: str
    required_inputs: list[str]
    output_artifact_type: str
    requires_human_approval: bool
    allowed_actions: list[str]
    status: WorkflowStatus
    blocked_reason: str | None
    override_reason: str | None
    order_index: int
    position_x: float
    position_y: float


class WorkflowNodeStatusUpdate(BaseModel):
    """Body for PATCH /projects/{id}/workflow-nodes/{node_id} — a manual
    override (see app/services/graph_engine.py's
    GraphEngineService.manual_override). Bypasses every graph rule, so a
    reason and actor are always required and every call is audited."""

    status: WorkflowStatus
    reason: NonBlankStr
    overridden_by_id: uuid.UUID = Field(..., description="Existing user id.")


class WorkflowEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    label: str | None
