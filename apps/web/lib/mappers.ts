/**
 * Pure converters from the backend's DTOs (lib/api.ts, snake_case) to this
 * app's UI types (lib/types.ts, camelCase). Keeping these separate from
 * api.ts means the fetch client stays a thin, honest mirror of the backend
 * response shapes, while all "how do we want to display this" decisions
 * live in one place.
 */

import type {
  ApiAgentDefinition,
  ApiAgentPrompt,
  ApiAgentRun,
  ApiArtifact,
  ApiArtifactVersion,
  ApiIntegration,
  ApiConfluenceConnection,
  ApiConfluencePageLink,
  ApiConfluencePublishItem,
  ApiConfluencePublishPreview,
  ApiConfluencePublishResultItem,
  ApiJiraConnection,
  ApiJiraExportPreview,
  ApiJiraIssueLink,
  ApiJiraProjectLink,
  ApiJiraPushItem,
  ApiJiraPushPreview,
  ApiJiraPushResultItem,
  ApiKnowledgeChunk,
  ApiKnowledgeSource,
  ApiOpsAgentRunRow,
  ApiOpsSummary,
  ApiOpsCostByProject,
  ApiOpsValidationIssueFrequency,
  ApiOpsRagSourceUsage,
  ApiOpsBlockedWorkflowRow,
  ApiGenerateImplementationPlanResponse,
  ApiGithubPrPreview,
  ApiImplementationRun,
  ApiImplementationTask,
  ApiPRReviewRun,
  ApiPullRequestLink,
  ApiMaintenanceRun,
  ApiTestRun,
  ApiIntegrationConnection,
  ApiProject,
  ApiRepoContextPreview,
  ApiGitHubRepoSummary,
  ApiRepository,
  ApiRepositoryFileContent,
  ApiRepositoryFileIndexEntry,
  ApiRepositorySnapshot,
  ApiRepositoryTree,
  ApiRevisionAgentRunResponse,
  ApiReview,
  ApiValidatorDefinition,
  ApiWorkflowEdge,
  ApiWorkflowNode,
} from "@/lib/api";
import { hasRealSections, isClarificationRequest, splitMarkdownIntoSections } from "@/lib/markdown-sections";
import type {
  AgentDefinitionSummary,
  AgentPromptVersion,
  AgentRunDetail,
  AgentRunSummary,
  ArtifactCommentItem,
  ArtifactDocument,
  ArtifactVersionSummary,
  DocumentArtifact,
  GenerateImplementationPlanResult,
  GithubConnectionItem,
  GithubPrPreview,
  GitHubRepoOption,
  GithubRepositoryItem,
  ImplementationRunItem,
  ImplementationTaskItem,
  PRReviewRunItem,
  PullRequestLinkItem,
  MaintenanceRunItem,
  TestRunItem,
  IntegrationItem,
  ConfluenceConnectionItem,
  ConfluencePageLinkItem,
  ConfluencePublishListItem,
  ConfluencePublishPreviewItem,
  ConfluencePublishResultEntry,
  JiraConnectionItem,
  JiraExportPreview,
  JiraIssueLinkItem,
  JiraProjectLinkItem,
  JiraPushListItem,
  JiraPushPreviewItem,
  JiraPushResultEntry,
  KnowledgeChunkItem,
  KnowledgeSourceItem,
  OpsSummary,
  OpsCostByProject,
  OpsValidationIssueFrequency,
  OpsRagSourceUsage,
  OpsBlockedWorkflowRow,
  Project,
  ProjectWorkflowEdge,
  ProjectWorkflowNode,
  RelevantFileItem,
  RepoContextPreviewItem,
  RepositoryFileContentResult,
  RepositoryFileIndexItem,
  RepositorySnapshotItem,
  RepositoryTreeResult,
  ReviewDetail,
  ReviewItem,
  RevisionAgentRunResult,
  ValidatorDefinitionItem,
} from "@/lib/types";

// The backend's WorkflowNode has no "assigned role" concept — it's a
// display-only convenience the frontend adds on top of the fixed default
// SDLC template (see workflows/sdlc-workflow.json). Keyed by node_key.
// Mirrors apps/api/app/services/permissions.py's STAGE_EDIT_ROLES exactly —
// this used to be a separate, made-up set of job titles ("Product
// Manager", "Software Engineer", ...) that didn't match any role the
// permission system actually checks, so a node's displayed "assigned role"
// and its real enforced edit permission silently disagreed. A stage with
// more than one allowed role shows both, joined by "/".
export const ASSIGNED_ROLE_BY_NODE_KEY: Record<string, string> = {
  requirement_intake: "BA",
  problem_discovery: "BA",
  solution_discovery: "BA / Architect",
  hld: "Architect",
  story_crafting: "BA / Product Owner",
  lld: "Architect / Tech Lead",
  implementation: "Developer / Tech Lead",
  testing: "QA",
  infrastructure: "DevOps",
  release: "DevOps",
  maintenance: "DevOps / Developer",
};

function assignedRoleFor(nodeKey: string): string {
  return ASSIGNED_ROLE_BY_NODE_KEY[nodeKey] ?? "Unassigned";
}

export function toProject(p: ApiProject): Project {
  return {
    id: p.id,
    name: p.name,
    description: p.description ?? "",
    businessOwner: p.business_owner,
    currentStage: p.current_stage,
    status: p.status,
    workflowTemplateId: p.workflow_template_id,
    createdAt: p.created_at,
    updatedAt: p.updated_at,
  };
}

export function toWorkflowNode(n: ApiWorkflowNode): ProjectWorkflowNode {
  return {
    id: n.id,
    projectId: n.project_id,
    nodeKey: n.node_key,
    name: n.name,
    description: n.description,
    agentKey: n.agent_key,
    assignedRole: assignedRoleFor(n.node_key),
    requiredInputs: n.required_inputs,
    outputArtifactType: n.output_artifact_type,
    requiresHumanApproval: n.requires_human_approval,
    allowedActions: n.allowed_actions,
    status: n.status as ProjectWorkflowNode["status"],
    blockedReason: n.blocked_reason,
    overrideReason: n.override_reason,
    contextTokenBudget: n.context_token_budget,
    outputTokenBudget: n.output_token_budget,
    fullContentArtifactTypes: n.full_content_artifact_types,
    ragTopK: n.rag_top_k,
    maxRagTokens: n.max_rag_tokens,
    requiredEvidenceSection: n.required_evidence_section,
    orderIndex: n.order_index,
    position: { x: n.position_x, y: n.position_y },
  };
}

export function toValidatorDefinition(v: ApiValidatorDefinition): ValidatorDefinitionItem {
  return {
    id: v.id,
    validatorKey: v.validator_key,
    name: v.name,
    stage: v.stage,
    description: v.description,
    modelName: v.model_name,
    qualityThreshold: v.quality_threshold,
    criteria: v.criteria,
    isActive: v.is_active,
  };
}

export function toWorkflowEdge(e: ApiWorkflowEdge): ProjectWorkflowEdge {
  return {
    id: e.id,
    source: e.source_node_id,
    target: e.target_node_id,
    label: e.label ?? undefined,
  };
}

export function toDocumentArtifact(a: ApiArtifact): DocumentArtifact {
  return {
    id: a.id,
    projectId: a.project_id,
    projectName: a.project_name,
    title: a.title,
    artifactType: a.artifact_type,
    status: a.status,
    versionNumber: a.current_version_number ?? 0,
    updatedAt: a.updated_at,
  };
}

export function toImplementationTask(t: ApiImplementationTask): ImplementationTaskItem {
  return {
    id: t.id,
    projectId: t.project_id,
    storyId: t.story_id,
    workflowNodeId: t.workflow_node_id,
    artifactId: t.artifact_id,
    artifactVersionId: t.artifact_version_id,
    title: t.title,
    description: t.description,
    linkedStory: t.linked_story,
    linkedLldSection: t.linked_lld_section,
    area: t.area,
    expectedPaths: t.expected_paths,
    dependencies: t.dependencies,
    acceptanceCriteria: t.acceptance_criteria,
    testExpectation: t.test_expectation,
    riskLevel: t.risk_level,
    assignedAgentType: t.assigned_agent_type,
    status: t.status,
    orderIndex: t.order_index,
    createdAt: t.created_at,
    updatedAt: t.updated_at,
  };
}

export function toGenerateImplementationPlanResult(
  r: ApiGenerateImplementationPlanResponse
): GenerateImplementationPlanResult {
  return {
    artifactId: r.artifact_id,
    artifactVersionId: r.artifact_version_id,
    tasks: r.tasks.map(toImplementationTask),
    reviewId: r.review.id,
  };
}

export function toPullRequestLink(p: ApiPullRequestLink): PullRequestLinkItem {
  return {
    id: p.id,
    projectId: p.project_id,
    workflowNodeId: p.workflow_node_id,
    implementationTaskId: p.implementation_task_id,
    implementationRunId: p.implementation_run_id,
    repositoryId: p.repository_id,
    branchName: p.branch_name,
    baseBranch: p.base_branch,
    prNumber: p.pr_number,
    prUrl: p.pr_url,
    status: p.status,
    createdByAgent: p.created_by_agent,
    triggeredByUserId: p.triggered_by_user_id,
    commitMessage: p.commit_message,
    createdAt: p.created_at,
  };
}

export function toPRReviewRun(r: ApiPRReviewRun): PRReviewRunItem {
  return {
    id: r.id,
    projectId: r.project_id,
    workflowNodeId: r.workflow_node_id,
    implementationTaskId: r.implementation_task_id,
    implementationRunId: r.implementation_run_id,
    pullRequestLinkId: r.pull_request_link_id,
    triggeredByUserId: r.triggered_by_user_id,
    status: r.status,
    overallRecommendation: r.overall_recommendation,
    summary: r.summary,
    criticalFindings: r.critical_findings.map((f) => ({ file: f.file, detail: f.detail })),
    majorFindings: r.major_findings.map((f) => ({ file: f.file, detail: f.detail })),
    minorFindings: r.minor_findings.map((f) => ({ file: f.file, detail: f.detail })),
    missingTests: r.missing_tests,
    suggestedComments: r.suggested_comments.map((c) => ({ file: c.file, body: c.body })),
    riskScore: r.risk_score,
    finalReviewerNote: r.final_reviewer_note,
    postedComments: r.posted_comments.map((c) => ({
      file: c.file, body: c.body, githubCommentId: c.github_comment_id, githubCommentUrl: c.github_comment_url, postedAt: c.posted_at,
    })),
    usedMock: r.used_mock,
    tokenUsage: r.token_usage,
    cost: r.cost,
    errorMessage: r.error_message,
    startedAt: r.started_at,
    completedAt: r.completed_at,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

export function toTestRun(r: ApiTestRun): TestRunItem {
  return {
    id: r.id,
    projectId: r.project_id,
    workflowNodeId: r.workflow_node_id,
    implementationTaskId: r.implementation_task_id,
    implementationRunId: r.implementation_run_id,
    pullRequestLinkId: r.pull_request_link_id,
    storyId: r.story_id,
    laneId: r.lane_id,
    artifactId: r.artifact_id,
    artifactVersionId: r.artifact_version_id,
    storyArtifactId: r.story_artifact_id,
    triggeredByUserId: r.triggered_by_user_id,
    agentType: r.agent_type,
    testAgentKey: r.test_agent_key,
    status: r.status,
    testPlan: r.test_plan,
    testsToAdd: r.tests_to_add.map((t) => ({ name: t.name, description: t.description, area: t.area })),
    testsExecuted: r.tests_executed.map((t) => ({ name: t.name, result: t.result, notes: t.notes })),
    passCount: r.pass_count,
    failCount: r.fail_count,
    bugsFound: r.bugs_found,
    suggestedFixes: r.suggested_fixes,
    coverageImpact: r.coverage_impact,
    evidenceAttachments: r.evidence_attachments,
    usedMock: r.used_mock,
    tokenUsage: r.token_usage,
    cost: r.cost,
    errorMessage: r.error_message,
    startedAt: r.started_at,
    completedAt: r.completed_at,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    reviewId: r.review_id,
  };
}

export function toMaintenanceRun(r: ApiMaintenanceRun): MaintenanceRunItem {
  return {
    id: r.id,
    projectId: r.project_id,
    workflowNodeId: r.workflow_node_id,
    triggeredByUserId: r.triggered_by_user_id,
    status: r.status,
    errorLogsInput: r.error_logs_input,
    userFeedbackInput: r.user_feedback_input,
    reportMarkdown: r.report_markdown,
    artifactId: r.artifact_id,
    artifactVersionId: r.artifact_version_id,
    usedMock: r.used_mock,
    tokenUsage: r.token_usage,
    cost: r.cost,
    errorMessage: r.error_message,
    startedAt: r.started_at,
    completedAt: r.completed_at,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

export function toImplementationRun(r: ApiImplementationRun): ImplementationRunItem {
  return {
    id: r.id,
    projectId: r.project_id,
    implementationTaskId: r.implementation_task_id,
    repositorySnapshotId: r.repository_snapshot_id,
    triggeredByUserId: r.triggered_by_user_id,
    agentType: r.agent_type,
    status: r.status,
    proposedFileChanges: r.proposed_file_changes.map((c) => ({
      path: c.path, changeType: c.change_type, summary: c.summary, afterContent: c.after_content,
    })),
    diffText: r.diff_text,
    explanation: r.explanation,
    testCommand: r.test_command,
    risks: r.risks,
    usedMock: r.used_mock,
    tokenUsage: r.token_usage,
    cost: r.cost,
    errorMessage: r.error_message,
    startedAt: r.started_at,
    completedAt: r.completed_at,
    reviewStatus: r.review_status,
    reviewedByUserId: r.reviewed_by_user_id,
    reviewedAt: r.reviewed_at,
    reviewComment: r.review_comment,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    pullRequest: r.pull_request ? toPullRequestLink(r.pull_request) : null,
  };
}

export function toArtifactVersionSummary(v: ApiArtifactVersion): ArtifactVersionSummary {
  return {
    id: v.id,
    versionNumber: v.version_number,
    changeSummary: v.change_summary,
    createdByName: v.created_by_name,
    createdAt: v.created_at,
  };
}

/** Assembles the full editor document from an enriched artifact, its
 * version history, and the current version's content (split into sections
 * for the editor UI — see markdown-sections.ts). Comments are populated
 * separately since they come from the artifact's reviews, not the artifact
 * itself; pass [] here and merge them in at the call site if needed. */
export function toArtifactDocument(
  artifact: ApiArtifact,
  versions: ApiArtifactVersion[],
  currentVersionMarkdown: string,
  comments: ArtifactCommentItem[] = [],
  agentKey: string = "",
  freeformInputKeys: string[] = []
): ArtifactDocument {
  return {
    id: artifact.id,
    projectId: artifact.project_id,
    projectName: artifact.project_name,
    workflowNodeId: artifact.workflow_node_id,
    workflowStageName: artifact.workflow_stage_name,
    agentKey,
    freeformInputKeys,
    artifactType: artifact.artifact_type,
    title: artifact.title,
    status: artifact.status,
    currentVersionNumber: artifact.current_version_number ?? 0,
    sections: splitMarkdownIntoSections(currentVersionMarkdown),
    hasRealSections: hasRealSections(currentVersionMarkdown),
    needsClarification: isClarificationRequest(currentVersionMarkdown),
    versions: versions.map(toArtifactVersionSummary),
    comments,
  };
}

export function toReviewItem(r: ApiReview): ReviewItem {
  return {
    id: r.id,
    projectId: r.project_id,
    projectName: r.project_name,
    artifactId: r.artifact_id,
    artifactTitle: r.artifact_title,
    workflowStageName: r.workflow_stage_name,
    reviewerId: r.reviewer_id,
    reviewerName: r.reviewer_name,
    status: r.status,
    submittedAt: r.created_at,
  };
}

export function toRevisionAgentRunResult(r: ApiRevisionAgentRunResponse): RevisionAgentRunResult {
  return {
    agentRunId: r.agent_run.id,
    needsClarification: r.needs_clarification,
    sectionsUpdated: r.sections_updated,
    artifactVersionId: r.artifact_version_id,
    newReviewId: r.new_review?.id ?? null,
  };
}

export function toReviewDetail(
  review: Parameters<typeof toReviewItem>[0],
  artifact: ArtifactDocument,
  history: ReviewDetail["history"] = []
): ReviewDetail {
  return { ...toReviewItem(review), artifact, history };
}

function toOpsRunRow(r: ApiOpsAgentRunRow) {
  return {
    id: r.id,
    projectId: r.project_id,
    projectName: r.project_name,
    workflowStageName: r.workflow_stage_name,
    agentKey: r.agent_key,
    action: r.action,
    status: r.status,
    durationSeconds: r.duration_seconds,
    totalTokens: r.total_tokens,
    cost: r.cost,
    errorMessage: r.error_message,
    createdAt: r.created_at,
  };
}

function toOpsCostByProject(c: ApiOpsCostByProject): OpsCostByProject {
  return { projectId: c.project_id, projectName: c.project_name, totalCost: c.total_cost, runCount: c.run_count };
}

function toOpsValidationIssueFrequency(v: ApiOpsValidationIssueFrequency): OpsValidationIssueFrequency {
  return { message: v.message, count: v.count };
}

function toOpsRagSourceUsage(r: ApiOpsRagSourceUsage): OpsRagSourceUsage {
  return { sourceTitle: r.source_title, count: r.count };
}

function toOpsBlockedWorkflowRow(b: ApiOpsBlockedWorkflowRow): OpsBlockedWorkflowRow {
  return {
    projectId: b.project_id,
    projectName: b.project_name,
    nodeKey: b.node_key,
    stageName: b.stage_name,
    blockedReason: b.blocked_reason,
    updatedAt: b.updated_at,
  };
}

export function toOpsSummary(s: ApiOpsSummary): OpsSummary {
  return {
    totalRuns: s.total_runs,
    successfulRuns: s.successful_runs,
    failedRuns: s.failed_runs,
    successRate: s.success_rate,
    failureRate: s.failure_rate,
    avgDurationSeconds: s.avg_duration_seconds,
    totalTokens: s.total_tokens,
    avgTokensPerRun: s.avg_tokens_per_run,
    totalCost: s.total_cost,
    avgQualityScore: s.avg_quality_score,
    approvalRate: s.approval_rate,
    rejectionRate: s.rejection_rate,
    decidedReviewCount: s.decided_review_count,
    humanChangeRate: s.human_change_rate,
    humanChangeRateNote: s.human_change_rate_note,
    blockedWorkflowCount: s.blocked_workflow_count,
    recentRuns: s.recent_runs.map(toOpsRunRow),
    recentFailures: s.recent_failures.map(toOpsRunRow),
    stagePerformance: s.stage_performance.map((sp) => ({
      nodeKey: sp.node_key,
      stageName: sp.stage_name,
      totalRuns: sp.total_runs,
      successfulRuns: sp.successful_runs,
      failedRuns: sp.failed_runs,
      successRate: sp.success_rate,
      avgDurationSeconds: sp.avg_duration_seconds,
      totalCost: sp.total_cost,
      avgQualityScore: sp.avg_quality_score,
      avgIterations: sp.avg_iterations,
    })),
    costByProject: s.cost_by_project.map(toOpsCostByProject),
    validationIssueFrequency: s.validation_issue_frequency.map(toOpsValidationIssueFrequency),
    ragSourceUsage: s.rag_source_usage.map(toOpsRagSourceUsage),
    blockedWorkflows: s.blocked_workflows.map(toOpsBlockedWorkflowRow),
  };
}

export function toJiraExportPreview(p: ApiJiraExportPreview): JiraExportPreview {
  return {
    projectId: p.project_id,
    artifactId: p.artifact_id,
    artifactTitle: p.artifact_title,
    storyCount: p.story_count,
    validStoryCount: p.valid_story_count,
    hasErrors: p.has_errors,
    overallErrors: p.overall_errors,
    pushToJiraEnabled: p.push_to_jira_enabled,
    stories: p.stories.map((s) => ({
      storyTitle: s.story_title,
      jiraIssueType: s.jira_issue_type,
      validationErrors: s.validation_errors,
      isValid: s.is_valid,
      mapping: {
        epic: s.mapping.epic,
        labels: s.mapping.labels,
        summary: s.mapping.summary,
        description: s.mapping.description,
        priority: s.mapping.priority,
        linkedIssuesPlaceholder: s.mapping.linked_issues_placeholder,
      },
    })),
  };
}

export function toJiraConnectionItem(c: ApiJiraConnection): JiraConnectionItem {
  return {
    id: c.id,
    integrationId: c.integration_id,
    baseUrl: c.base_url,
    email: c.email,
    status: c.status,
    connectedById: c.connected_by_id,
    connectedByName: c.connected_by_name,
    tokenHint: c.token_hint,
    createdAt: c.created_at,
    updatedAt: c.updated_at,
  };
}

export function toJiraProjectLinkItem(p: ApiJiraProjectLink): JiraProjectLinkItem {
  return {
    id: p.id,
    projectId: p.project_id,
    connectionId: p.connection_id,
    jiraProjectKey: p.jira_project_key,
    jiraProjectName: p.jira_project_name,
  };
}

export function toJiraIssueLinkItem(l: ApiJiraIssueLink): JiraIssueLinkItem {
  return {
    id: l.id,
    sourceType: l.source_type,
    sourceKey: l.source_key,
    sourceLabel: l.source_label,
    jiraIssueKey: l.jira_issue_key,
    jiraIssueType: l.jira_issue_type,
    jiraIssueUrl: l.jira_issue_url,
    parentJiraIssueKey: l.parent_jira_issue_key,
    jiraStatus: l.jira_status,
    lastSyncedAt: l.last_synced_at,
  };
}

function toJiraPushListItem(i: ApiJiraPushItem): JiraPushListItem {
  return {
    sourceType: i.source_type,
    sourceKey: i.source_key,
    label: i.label,
    jiraIssueType: i.jira_issue_type,
    parentSourceKey: i.parent_source_key,
    summary: i.summary,
    description: i.description,
    validationErrors: i.validation_errors,
    alreadyLinked: i.already_linked ? toJiraIssueLinkItem(i.already_linked) : null,
  };
}

export function toJiraPushPreview(p: ApiJiraPushPreview): JiraPushPreviewItem {
  return {
    projectId: p.project_id,
    jiraProjectKey: p.jira_project_key,
    epics: p.epics.map(toJiraPushListItem),
    stories: p.stories.map(toJiraPushListItem),
    implementationTasks: p.implementation_tasks.map(toJiraPushListItem),
    testingBugs: p.testing_bugs.map(toJiraPushListItem),
    overallErrors: p.overall_errors,
  };
}

export function toJiraPushResultEntry(r: ApiJiraPushResultItem): JiraPushResultEntry {
  return {
    sourceType: r.source_type,
    sourceKey: r.source_key,
    status: r.status,
    jiraIssueKey: r.jira_issue_key,
    jiraIssueUrl: r.jira_issue_url,
    errors: r.errors,
  };
}

export function toConfluenceConnectionItem(c: ApiConfluenceConnection): ConfluenceConnectionItem {
  return {
    id: c.id,
    integrationId: c.integration_id,
    baseUrl: c.base_url,
    email: c.email,
    status: c.status,
    connectedById: c.connected_by_id,
    connectedByName: c.connected_by_name,
    tokenHint: c.token_hint,
    createdAt: c.created_at,
    updatedAt: c.updated_at,
  };
}

export function toConfluencePageLinkItem(l: ApiConfluencePageLink): ConfluencePageLinkItem {
  return {
    id: l.id,
    artifactType: l.artifact_type,
    confluencePageId: l.confluence_page_id,
    confluencePageUrl: l.confluence_page_url,
    confluencePageTitle: l.confluence_page_title,
    confluencePageVersion: l.confluence_page_version,
  };
}

function toConfluencePublishListItem(i: ApiConfluencePublishItem): ConfluencePublishListItem {
  return {
    artifactType: i.artifact_type,
    label: i.label,
    artifactId: i.artifact_id,
    artifactStatus: i.artifact_status,
    contentPreview: i.content_preview,
    validationErrors: i.validation_errors,
    alreadyPublished: i.already_published ? toConfluencePageLinkItem(i.already_published) : null,
    updateAvailable: i.update_available,
  };
}

export function toConfluencePublishPreview(p: ApiConfluencePublishPreview): ConfluencePublishPreviewItem {
  return {
    projectId: p.project_id,
    spaceKey: p.space_key,
    items: p.items.map(toConfluencePublishListItem),
  };
}

export function toConfluencePublishResultEntry(r: ApiConfluencePublishResultItem): ConfluencePublishResultEntry {
  return {
    artifactType: r.artifact_type,
    status: r.status,
    confluencePageId: r.confluence_page_id,
    confluencePageUrl: r.confluence_page_url,
    errors: r.errors,
  };
}

export function toGithubPrPreview(p: ApiGithubPrPreview): GithubPrPreview {
  return {
    suggestedTitle: p.suggested_title,
    descriptionMarkdown: p.description_markdown,
    checklist: p.checklist,
  };
}

export function toIntegrationItem(i: ApiIntegration): IntegrationItem {
  return {
    id: i.id,
    integrationName: i.integration_name,
    provider: i.provider,
    status: i.status,
    connectedByName: i.connected_by_name,
    lastSyncedAt: i.last_synced_at,
  };
}

export function toGithubConnectionItem(c: ApiIntegrationConnection): GithubConnectionItem {
  return {
    id: c.id,
    integrationId: c.integration_id,
    githubUsername: c.github_username,
    scopes: c.scopes,
    status: c.status,
    connectedById: c.connected_by_id,
    connectedByName: c.connected_by_name,
    tokenHint: c.token_hint,
    createdAt: c.created_at,
    updatedAt: c.updated_at,
  };
}

export function toGithubRepositoryItem(r: ApiRepository): GithubRepositoryItem {
  return {
    id: r.id,
    projectId: r.project_id,
    connectionId: r.connection_id,
    owner: r.owner,
    name: r.name,
    defaultBranch: r.default_branch,
    description: r.description,
    htmlUrl: r.html_url,
    isPrivate: r.is_private,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

export function toGitHubRepoOption(r: ApiGitHubRepoSummary): GitHubRepoOption {
  return {
    owner: r.owner,
    name: r.name,
    fullName: r.full_name,
    defaultBranch: r.default_branch,
    description: r.description,
    isPrivate: r.is_private,
    htmlUrl: r.html_url,
  };
}

export function toRepositoryTreeResult(t: ApiRepositoryTree): RepositoryTreeResult {
  return {
    commitSha: t.commit_sha,
    truncated: t.truncated,
    entries: t.entries.map((e) => ({ path: e.path, entryType: e.entry_type, size: e.size, sha: e.sha })),
  };
}

export function toRepositoryFileContentResult(f: ApiRepositoryFileContent): RepositoryFileContentResult {
  return {
    path: f.path,
    sha: f.sha,
    size: f.size,
    content: f.content,
    truncated: f.truncated,
    isBinary: f.is_binary,
  };
}

export function toRepositorySnapshotItem(s: ApiRepositorySnapshot): RepositorySnapshotItem {
  return {
    id: s.id,
    repositoryId: s.repository_id,
    ref: s.ref,
    commitSha: s.commit_sha,
    fileCount: s.file_count,
    truncated: s.truncated,
    triggeredById: s.triggered_by_id,
    triggeredByName: s.triggered_by_name,
    createdAt: s.created_at,
  };
}

export function toRepoContextPreview(p: ApiRepoContextPreview): RepoContextPreviewItem {
  return {
    relevantFolders: p.relevant_folders,
    relevantFiles: p.relevant_files.map(
      (f): RelevantFileItem => ({
        path: f.path,
        entryType: f.entry_type,
        size: f.size,
        score: f.score,
        reasons: f.reasons,
        contentMode: f.content_mode,
        snippet: f.snippet,
      })
    ),
    architectureSummary: p.architecture_summary,
    dependencyNotes: p.dependency_notes,
    suggestedEditScope: p.suggested_edit_scope.map((e) => ({ path: e.path, status: e.status })),
    tokenBudgetReport: {
      contextTokenBudget: p.token_budget_report.context_token_budget,
      outputTokenBudget: p.token_budget_report.output_token_budget,
      rawEstimatedTokens: p.token_budget_report.raw_estimated_tokens,
      estimatedTokens: p.token_budget_report.estimated_tokens,
      overBudget: p.token_budget_report.over_budget,
      blocks: p.token_budget_report.blocks.map((b) => ({
        priority: b.priority,
        label: b.label,
        estimatedTokens: b.estimated_tokens,
        included: b.included,
        truncated: b.truncated,
      })),
    },
    filesConsidered: p.files_considered,
    filesIncluded: p.files_included,
  };
}

export function toRepositoryFileIndexItem(f: ApiRepositoryFileIndexEntry): RepositoryFileIndexItem {
  return {
    id: f.id,
    snapshotId: f.snapshot_id,
    path: f.path,
    entryType: f.entry_type,
    size: f.size,
    sha: f.sha,
  };
}

export function toKnowledgeSourceItem(s: ApiKnowledgeSource): KnowledgeSourceItem {
  return {
    id: s.id,
    title: s.title,
    category: s.category,
    sourceType: s.source_type,
    fileUrl: s.file_url,
    status: s.status,
    uploadedByName: s.uploaded_by_name,
    chunkCount: s.chunk_count,
    createdAt: s.created_at,
  };
}

export function toKnowledgeChunkItem(c: ApiKnowledgeChunk): KnowledgeChunkItem {
  return {
    id: c.id,
    sourceId: c.source_id,
    chunkIndex: c.chunk_index,
    content: c.content,
    metadataJson: c.metadata_json,
    createdAt: c.created_at,
  };
}

export function toAgentDefinitionSummary(a: ApiAgentDefinition): AgentDefinitionSummary {
  return {
    id: a.id,
    agentKey: a.agent_key,
    name: a.name,
    description: a.description ?? "",
    modelName: a.model_name,
    isActive: a.is_active,
    totalRuns: a.total_runs,
  };
}

export function toAgentPromptVersion(p: ApiAgentPrompt): AgentPromptVersion {
  return {
    id: p.id,
    agentKey: p.agent_key,
    role: p.role,
    name: p.name,
    stage: p.stage,
    systemPrompt: p.system_prompt,
    outputFormat: p.output_format,
    validationChecklist: p.validation_checklist,
    version: p.version,
    isActive: p.is_active,
    createdAt: p.created_at,
    updatedAt: p.updated_at,
  };
}

export function toAgentRunDetail(r: ApiAgentRun, projectName: string, workflowStageName: string): AgentRunDetail {
  return {
    id: r.id,
    projectId: r.project_id,
    projectName,
    workflowNodeId: r.workflow_node_id,
    workflowStageName,
    agentKey: r.agent_key,
    promptVersion: r.prompt_version,
    action: r.action,
    status: r.status,
    inputContext: r.input_context,
    outputText: r.output_text,
    outputArtifactId: r.output_artifact_id,
    errorMessage: r.error_message,
    tokenUsage: r.token_usage as AgentRunDetail["tokenUsage"],
    cost: r.cost,
    startedAt: r.started_at,
    completedAt: r.completed_at,
    createdAt: r.created_at,
    retrievedSources: r.retrieved_sources
      ? r.retrieved_sources.map((s) => ({
          chunkId: s.chunk_id,
          sourceId: s.source_id,
          sourceTitle: s.source_title,
          chunkIndex: s.chunk_index,
          snippet: s.snippet,
          similarity: s.similarity,
          stage: s.stage ?? null,
          domain: s.domain ?? null,
          contentType: s.content_type ?? null,
          tags: s.tags ?? [],
        }))
      : null,
    retrievedSourceTitles: r.retrieved_source_titles,
    loopStatus: r.loop_status,
    loopIteration: r.loop_iteration,
    loopMaxIterations: r.loop_max_iterations,
    loopQualityThreshold: r.loop_quality_threshold,
    loopQualityScore: r.loop_quality_score,
    loopValidationIssues: r.loop_validation_issues,
    loopValidationResult: r.loop_validation_result as AgentRunDetail["loopValidationResult"],
    contextTokenBudget: r.context_token_budget,
    outputTokenBudget: r.output_token_budget,
    estimatedContextTokens: r.estimated_context_tokens,
    tokenBudgetReport: r.token_budget_report,
  };
}

export function toAgentRunSummary(
  r: ApiAgentRun,
  projectName: string,
  agentName: string,
  workflowStageName: string
): AgentRunSummary {
  return {
    id: r.id,
    projectId: r.project_id,
    projectName,
    agentName,
    workflowStageName,
    action: r.action,
    status: r.status,
    createdAt: r.created_at,
  };
}
