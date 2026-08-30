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
  ApiJiraExportPreview,
  ApiKnowledgeChunk,
  ApiKnowledgeSource,
  ApiOpsAgentRunRow,
  ApiOpsSummary,
  ApiProject,
  ApiReview,
  ApiWorkflowEdge,
  ApiWorkflowNode,
} from "@/lib/api";
import { splitMarkdownIntoSections } from "@/lib/markdown-sections";
import type {
  AgentDefinitionSummary,
  AgentPromptVersion,
  AgentRunDetail,
  AgentRunSummary,
  ArtifactCommentItem,
  ArtifactDocument,
  ArtifactVersionSummary,
  DocumentArtifact,
  IntegrationItem,
  JiraExportPreview,
  KnowledgeChunkItem,
  KnowledgeSourceItem,
  OpsSummary,
  Project,
  ProjectWorkflowEdge,
  ProjectWorkflowNode,
  ReviewDetail,
  ReviewItem,
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
    orderIndex: n.order_index,
    position: { x: n.position_x, y: n.position_y },
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

export function toOpsSummary(s: ApiOpsSummary): OpsSummary {
  return {
    totalRuns: s.total_runs,
    successfulRuns: s.successful_runs,
    failedRuns: s.failed_runs,
    avgDurationSeconds: s.avg_duration_seconds,
    totalTokens: s.total_tokens,
    totalCost: s.total_cost,
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
    })),
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
        }))
      : null,
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
