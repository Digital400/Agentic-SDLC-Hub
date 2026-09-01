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
    total_cost: float
    avg_quality_score: float | None
    avg_iterations: float | None


class OpsCostByProject(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    project_name: str
    total_cost: float
    run_count: int


class OpsValidationIssueFrequency(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str
    count: int


class OpsRagSourceUsage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_title: str
    count: int


class OpsBlockedWorkflowRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    project_name: str
    node_key: str
    stage_name: str
    blocked_reason: str | None
    updated_at: str


class OpsSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float | None
    failure_rate: float | None
    avg_duration_seconds: float | None
    total_tokens: int
    avg_tokens_per_run: float | None
    total_cost: float
    avg_quality_score: float | None
    approval_rate: float | None
    rejection_rate: float | None
    decided_review_count: int
    human_change_rate: None
    human_change_rate_note: str
    blocked_workflow_count: int
    recent_runs: list[OpsAgentRunRow]
    recent_failures: list[OpsAgentRunRow]
    stage_performance: list[OpsStagePerformance]
    cost_by_project: list[OpsCostByProject]
    validation_issue_frequency: list[OpsValidationIssueFrequency]
    rag_source_usage: list[OpsRagSourceUsage]
    blocked_workflows: list[OpsBlockedWorkflowRow]
