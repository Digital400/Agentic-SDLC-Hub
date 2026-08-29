import uuid

from pydantic import BaseModel, ConfigDict

from app.models.enums import WorkflowStatus


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
    order_index: int
    position_x: float
    position_y: float


class WorkflowNodeStatusUpdate(BaseModel):
    status: WorkflowStatus


class WorkflowEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    label: str | None
