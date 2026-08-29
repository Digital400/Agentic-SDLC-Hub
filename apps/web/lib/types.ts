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
  status: ArtifactStatus;
  versionNumber: number;
  updatedAt: string;
}

export interface ArtifactSection {
  id: string;
  title: string;
  contentMarkdown: string;
}

export interface ArtifactVersionSummary {
  id: string;
  versionNumber: number;
  changeSummary: string | null;
  createdByName: string;
  createdAt: string;
}

export interface ArtifactCommentItem {
  id: string;
  authorName: string;
  body: string;
  createdAt: string;
  /** If set, the section this comment is anchored to. */
  sectionId?: string;
}

/** The full editable document, as loaded by the artifact editor — a
 * superset of DocumentArtifact's list-row shape. */
export interface ArtifactDocument {
  id: string;
  projectId: string;
  projectName: string;
  workflowStageName: string;
  artifactType: string;
  title: string;
  status: ArtifactStatus;
  currentVersionNumber: number;
  sections: ArtifactSection[];
  versions: ArtifactVersionSummary[];
  comments: ArtifactCommentItem[];
}

export interface ReviewItem {
  id: string;
  projectId: string;
  projectName: string;
  artifactId: string;
  artifactTitle: string;
  workflowStageName: string;
  reviewerId: string;
  reviewerName: string;
  status: ReviewStatus;
  submittedAt: string;
}

export interface ReviewChecklistItem {
  id: string;
  label: string;
}

/** One past, already-decided round for an artifact under review — not the
 * current pending round, which is ReviewItem/ReviewDetail itself. */
export interface ReviewDecisionHistoryEntry {
  id: string;
  versionNumber: number;
  status: Exclude<ReviewStatus, "PENDING">;
  reviewerName: string;
  comment: string | null;
  decidedAt: string;
}

/** Full detail for the Review Detail screen — the current round (from
 * ReviewItem) plus the artifact being reviewed and any prior rounds. */
export interface ReviewDetail extends ReviewItem {
  artifact: ArtifactDocument;
  history: ReviewDecisionHistoryEntry[];
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

export type AgentPromptRole = "draft" | "improve" | "validate";

/** Matches apps/api/app/models/agent.py's AgentPrompt — one version in a
 * (agentKey, role) lineage; exactly one version per lineage is active. */
export interface AgentPromptVersion {
  id: string;
  agentKey: string;
  role: AgentPromptRole;
  name: string;
  stage: string;
  systemPrompt: string;
  outputFormat: string;
  validationChecklist: string[];
  version: number;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
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
