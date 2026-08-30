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
export type KnowledgeSourceType = "PROJECT_ARTIFACT" | "UPLOADED_DOCUMENT" | "EXTERNAL_LINK";
/** A knowledge source's ingestion lifecycle — no ingestion pipeline exists
 * yet (see apps/api/app/models/knowledge.py), so this only reflects
 * whatever a source was seeded/set to, not real pipeline progress. */
export type KnowledgeSourceStatus = "PENDING" | "PROCESSING" | "INDEXED" | "FAILED";

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
  workflowNodeId: string;
  workflowStageName: string;
  /** The agent responsible for this stage — see ProjectWorkflowNode.agentKey. */
  agentKey: string;
  /** Which of this stage's required_inputs are freeform (no upstream
   * artifact to satisfy them) — e.g. "stakeholder_request" for Requirement
   * Intake. Empty for a stage whose inputs are all upstream artifacts. */
  freeformInputKeys: string[];
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

/** One Knowledge Base chunk retrieved for a run's context — see
 * apps/api/app/services/retrieval.py. */
export interface RetrievedSourceItem {
  chunkId: string;
  sourceId: string;
  sourceTitle: string;
  chunkIndex: number;
  snippet: string;
  similarity: number;
}

/** Full detail for the Agent Run Detail screen. */
export interface AgentRunDetail {
  id: string;
  projectId: string;
  projectName: string;
  workflowStageName: string;
  agentKey: string;
  promptVersion: number | null;
  action: "draft" | "improve" | "validate";
  status: AgentRunStatus;
  inputContext: Record<string, unknown>;
  outputText: string | null;
  outputArtifactId: string | null;
  errorMessage: string | null;
  tokenUsage: { prompt_tokens: number; completion_tokens: number; total_tokens: number } | null;
  cost: number | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  /** Null: retrieval never ran (e.g. the run failed before that step).
   * Empty array: retrieval ran and found nothing relevant enough to use —
   * the run proceeded on project context alone. */
  retrievedSources: RetrievedSourceItem[] | null;
}

/** One story's Jira field mapping preview — see
 * apps/api/app/services/jira_export.py. No real Jira connection exists;
 * this is a review-before-push preview only. */
export interface JiraFieldMapping {
  epic: string | null;
  labels: string[];
  summary: string;
  description: string;
  priority: string | null;
  linkedIssuesPlaceholder: string[];
}

export interface StoryJiraPreviewItem {
  storyTitle: string;
  jiraIssueType: string;
  mapping: JiraFieldMapping;
  validationErrors: string[];
  isValid: boolean;
}

export interface JiraExportPreview {
  projectId: string;
  artifactId: string;
  artifactTitle: string;
  storyCount: number;
  validStoryCount: number;
  hasErrors: boolean;
  overallErrors: string[];
  stories: StoryJiraPreviewItem[];
  pushToJiraEnabled: boolean;
}

/** One row in the AI Ops Dashboard's agent run table / failure list. */
export interface OpsAgentRunRow {
  id: string;
  projectId: string;
  projectName: string;
  workflowStageName: string;
  agentKey: string;
  action: string;
  status: string;
  durationSeconds: number | null;
  totalTokens: number | null;
  cost: number | null;
  errorMessage: string | null;
  createdAt: string;
}

export interface OpsStagePerformance {
  nodeKey: string;
  stageName: string;
  totalRuns: number;
  successfulRuns: number;
  failedRuns: number;
  /** null when this stage has had zero runs. */
  successRate: number | null;
  avgDurationSeconds: number | null;
}

/** Company-wide AI Ops metrics — see apps/api/app/services/ops_metrics.py. */
export interface OpsSummary {
  totalRuns: number;
  successfulRuns: number;
  failedRuns: number;
  avgDurationSeconds: number | null;
  totalTokens: number;
  totalCost: number;
  /** null when there are no decided reviews yet to compute a rate from. */
  approvalRate: number | null;
  rejectionRate: number | null;
  decidedReviewCount: number;
  /** Always null today — see humanChangeRateNote. */
  humanChangeRate: null;
  humanChangeRateNote: string;
  blockedWorkflowCount: number;
  recentRuns: OpsAgentRunRow[];
  recentFailures: OpsAgentRunRow[];
  stagePerformance: OpsStagePerformance[];
}

export type IntegrationProvider = "JIRA" | "CONFLUENCE" | "GITHUB" | "SLACK" | "TEAMS" | "AZURE_DEVOPS";
export type IntegrationStatus = "NOT_CONNECTED" | "CONNECTED" | "ERROR";

/** One row from the Integrations settings UI — see
 * apps/api/app/models/integration.py. No MCP tool is wired up yet, so
 * every integration is realistically NOT_CONNECTED with lastSyncedAt null. */
export interface IntegrationItem {
  id: string;
  integrationName: string;
  provider: IntegrationProvider;
  status: IntegrationStatus;
  connectedByName: string | null;
  lastSyncedAt: string | null;
}

export interface KnowledgeSourceItem {
  id: string;
  title: string;
  category: string;
  sourceType: KnowledgeSourceType;
  fileUrl: string | null;
  status: KnowledgeSourceStatus;
  uploadedByName: string;
  chunkCount: number;
  createdAt: string;
}

export interface KnowledgeChunkItem {
  id: string;
  sourceId: string;
  chunkIndex: number;
  content: string;
  metadataJson: Record<string, unknown> | null;
  createdAt: string;
}
