import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";

// These mirror apps/api/app/models/enums.py. Kept here (rather than in
// packages/shared) since they're not yet part of a stable cross-app
// contract — promote them to packages/shared if/when the API client is
// generated from these shapes.

export type ProjectStatus = "ACTIVE" | "COMPLETED" | "ARCHIVED";
export type ReviewStatus = "PENDING" | "APPROVED" | "NEEDS_CHANGES" | "REJECTED";
export type AgentRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
/** An artifact's own review lifecycle — distinct from WorkflowStatus, which
 * tracks its owning WorkflowNode's broader lifecycle. */
export type ArtifactStatus = "DRAFT" | "READY_FOR_REVIEW" | "APPROVED" | "NEEDS_CHANGES" | "REJECTED";

export interface Project {
  id: string;
  name: string;
  description: string;
  businessOwner: string;
  /** node_key of the workflow node this project is currently on. */
  currentStage: string;
  status: ProjectStatus;
  workflowTemplateId: string;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectWorkflowNode {
  id: string;
  projectId: string;
  nodeKey: string;
  name: string;
  description: string;
  agentKey: string;
  /** The human role expected to act on this stage (e.g. "Tech Lead"). */
  assignedRole: string;
  requiredInputs: string[];
  outputArtifactType: string;
  requiresHumanApproval: boolean;
  allowedActions: string[];
  status: WorkflowStatus;
  orderIndex: number;
  position: { x: number; y: number };
}

export interface ProjectWorkflowEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

export interface DocumentArtifact {
  id: string;
  projectId: string;
  projectName: string;
  title: string;
  artifactType: string;
  status: WorkflowStatus;
  versionNumber: number;
  updatedAt: string;
}

export interface ReviewItem {
  id: string;
  projectId: string;
  projectName: string;
  artifactTitle: string;
  workflowStageName: string;
  reviewerName: string;
  status: ReviewStatus;
  submittedAt: string;
}

export interface AgentDefinitionSummary {
  id: string;
  agentKey: string;
  name: string;
  description: string;
  modelName: string;
  isActive: boolean;
  totalRuns: number;
}

export interface AgentRunSummary {
  id: string;
  projectId: string;
  projectName: string;
  agentName: string;
  workflowStageName: string;
  action: "draft" | "improve" | "validate";
  status: AgentRunStatus;
  createdAt: string;
}

export interface KnowledgeBaseSource {
  id: string;
  title: string;
  sourceType: "Project artifact" | "Uploaded document" | "External link";
  projectName?: string;
  indexed: boolean;
  updatedAt: string;
}
