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
  ApiProject,
  ApiReview,
  ApiWorkflowEdge,
  ApiWorkflowNode,
} from "@/lib/api";
import { splitMarkdownIntoSections } from "@/lib/markdown-sections";
import type {
  AgentDefinitionSummary,
  AgentPromptVersion,
  AgentRunSummary,
  ArtifactCommentItem,
  ArtifactDocument,
  ArtifactVersionSummary,
  DocumentArtifact,
  Project,
  ProjectWorkflowEdge,
  ProjectWorkflowNode,
  ReviewDetail,
  ReviewItem,
} from "@/lib/types";

// The backend's WorkflowNode has no "assigned role" concept — it's a
// display-only convenience the frontend adds on top of the fixed default
// SDLC template (see workflows/sdlc-workflow.json). Keyed by node_key.
export const ASSIGNED_ROLE_BY_NODE_KEY: Record<string, string> = {
  requirement_intake: "Product Manager",
  problem_discovery: "Product Manager",
  solution_discovery: "Tech Lead",
  hld: "Tech Lead",
  story_crafting: "Product Manager",
  lld: "Tech Lead",
  implementation: "Software Engineer",
  testing: "QA Engineer",
  infrastructure: "DevOps Engineer",
  release: "Release Manager",
  maintenance: "Support Engineer",
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
  comments: ArtifactCommentItem[] = []
): ArtifactDocument {
  return {
    id: artifact.id,
    projectId: artifact.project_id,
    projectName: artifact.project_name,
    workflowStageName: artifact.workflow_stage_name,
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
