from pydantic import BaseModel, ConfigDict


class OpsAgentRunRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    project_name: str
    workflow_stage_name: str
    agent_key: str
    action: str
    status: str
    duration_seconds: float | None
    total_tokens: int | None
    cost: float | None
    error_message: str | None
    created_at: str


class OpsStagePerformance(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_key: str
    stage_name: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float | None
    avg_duration_seconds: float | None


class OpsSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_runs: int
    successful_runs: int
    failed_runs: int
    avg_duration_seconds: float | None
    total_tokens: int
    total_cost: float
    approval_rate: float | None
    rejection_rate: float | None
    decided_review_count: int
    human_change_rate: None
    human_change_rate_note: str
    blocked_workflow_count: int
    recent_runs: list[OpsAgentRunRow]
    recent_failures: list[OpsAgentRunRow]
    stage_performance: list[OpsStagePerformance]
