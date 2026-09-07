import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";

// These mirror apps/api/app/models/enums.py. Kept here (rather than in
// packages/shared) since they're not yet part of a stable cross-app
// contract — promote them to packages/shared if/when the API client is
// generated from these shapes.

export type ProjectStatus = "ACTIVE" | "COMPLETED" | "ARCHIVED";
/** Selected once, at project creation — decides which workflow template
 * generates the project's graph (see apps/api/app/models/enums.py's
 * WorkType docstring). NEW_PROJECT keeps the full default SDLC template;
 * the other three all use the existing-project feature template. */
export type WorkType = "NEW_PROJECT" | "EXISTING_PROJECT_FEATURE" | "BUG_FIX" | "TECHNICAL_IMPROVEMENT";
export type ReviewStatus = "PENDING" | "APPROVED" | "NEEDS_CHANGES" | "REJECTED";
export type AgentRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
/** An artifact's own review lifecycle — distinct from WorkflowStatus, which
 * tracks its owning WorkflowNode's broader lifecycle. */
export type ArtifactStatus = "DRAFT" | "READY_FOR_REVIEW" | "APPROVED" | "NEEDS_CHANGES" | "REJECTED";
export type KnowledgeSourceType = "PROJECT_ARTIFACT" | "UPLOADED_DOCUMENT" | "EXTERNAL_LINK";
/** A knowledge source's ingestion lifecycle — a real upload (see
 * apps/api/app/services/document_ingestion.py) goes straight to INDEXED,
 * since every chunk is embedded before the upload call returns. */
export type KnowledgeSourceStatus = "PENDING" | "PROCESSING" | "INDEXED" | "FAILED";
/** What kind of guidance a chunk represents — see
 * apps/api/app/models/enums.py's KnowledgeContentType. */
export type KnowledgeContentType =
  | "COMPANY_STANDARD"
  | "PAST_ARTIFACT"
  | "UI_GUIDELINE"
  | "ARCHITECTURE_RULE"
  | "TESTING_STANDARD"
  | "OTHER";

export interface Project {
  id: string;
  name: string;
  description: string;
  businessOwner: string;
  /** node_key of the workflow node this project is currently on. */
  currentStage: string;
  status: ProjectStatus;
  workType: WorkType;
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
  /** Why the graph engine blocked this node — set only when status is BLOCKED. */
  blockedReason: string | null;
  /** Set only when the current status came from an admin manual override
   * rather than normal graph progression. */
  overrideReason: string | null;
  contextTokenBudget: number;
  outputTokenBudget: number;
  fullContentArtifactTypes: string[];
  ragTopK: number;
  maxRagTokens: number;
  /** A `## <heading>` this stage's artifact must have, with non-empty
   * content, before a review can approve it. Null for most stages. */
  requiredEvidenceSection: string | null;
  orderIndex: number;
  position: { x: number; y: number };
}

/** One workflow stage's independent quality validator — see
 * apps/api/app/models/validator.py. One per stage, keyed by nodeKey (its
 * `stage` field matches WorkflowNode.node_key exactly). */
export interface ValidatorDefinitionItem {
  id: string;
  validatorKey: string;
  name: string;
  /** Matches ProjectWorkflowNode.nodeKey. */
  stage: string;
  description: string | null;
  modelName: string;
  qualityThreshold: number;
  criteria: string[];
  isActive: boolean;
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

/** See apps/api/app/models/implementation_task.py's ImplementationTaskArea. */
export type ImplementationTaskArea = "BACKEND" | "FRONTEND" | "DATABASE" | "TESTING" | "INFRA" | "DOCS";
export type ImplementationTaskRiskLevel = "LOW" | "MEDIUM" | "HIGH";
/** No coding agent exists yet to advance a task past PENDING — see
 * ImplementationTaskStatus's backend docstring. */
export type ImplementationTaskStatus = "PENDING" | "IN_PROGRESS" | "COMPLETED" | "BLOCKED";

/** One unit of work in an Implementation Plan, generated from an approved
 * LLD — see apps/api/app/services/implementation_planner.py. */
export interface ImplementationTaskItem {
  id: string;
  projectId: string;
  storyId: string | null;
  workflowNodeId: string | null;
  artifactId: string | null;
  artifactVersionId: string | null;
  /** Multi-repo support — which of the project's (possibly several)
   * connected repositories this task's code changes target. Null means
   * "use the project's primary repository" — see GithubRepositoryItem.isPrimary. */
  repositoryId: string | null;
  title: string;
  description: string;
  linkedStory: string | null;
  linkedLldSection: string | null;
  area: ImplementationTaskArea;
  expectedPaths: string[];
  dependencies: string[];
  acceptanceCriteria: string[];
  testExpectation: string;
  riskLevel: ImplementationTaskRiskLevel;
  /** Derived 1:1 from `area`. A real agent exists for BACKEND/FRONTEND/
   * DATABASE/DOCS (see apps/api/app/services/implementation_agent.py);
   * TESTING/INFRA remain forward-looking classifications only. */
  assignedAgentType: string;
  status: ImplementationTaskStatus;
  orderIndex: number;
  createdAt: string;
  updatedAt: string;
}

/** Result of "Generate Implementation Plan" — see
 * apps/api/app/api/routes/projects.py's generate_implementation_plan.
 * "Approve Implementation Plan" is the existing review-decision screen
 * against `reviewId`, not a separate action. */
export interface GenerateImplementationPlanResult {
  artifactId: string;
  artifactVersionId: string;
  tasks: ImplementationTaskItem[];
  reviewId: string;
}

// Implementation Agent execution — see apps/api/app/services/implementation_agent.py
// and apps/api/app/models/implementation_run.py. Generating/reviewing a run
// never writes to GitHub; only createPullRequest (once ACCEPTED) does —
// see PullRequestLinkItem below.
export type ImplementationRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
export type ImplementationRunReviewStatus = "PENDING_REVIEW" | "ACCEPTED" | "REJECTED";

export interface ProposedFileChangeItem {
  path: string;
  changeType: string; // "create" | "modify" | "delete"
  summary: string;
  afterContent: string | null;
}

export type PullRequestStatus = "OPEN" | "MERGED" | "CLOSED";

/** A real GitHub pull request created from an ACCEPTED run — see
 * apps/api/app/models/pull_request_link.py. Always targets a freshly
 * created feature branch (`branchName`), never `baseBranch` directly. */
export interface PullRequestLinkItem {
  id: string;
  projectId: string;
  workflowNodeId: string | null;
  implementationTaskId: string;
  implementationRunId: string;
  repositoryId: string;
  storyId: string | null;
  laneId: string | null;
  codeRunId: string | null;
  jiraIssueKey: string | null;
  branchName: string;
  baseBranch: string;
  prNumber: number;
  prUrl: string;
  status: PullRequestStatus;
  createdByAgent: boolean;
  triggeredByUserId: string | null;
  commitMessage: string;
  createdAt: string;
}

export interface ImplementationRunItem {
  id: string;
  projectId: string;
  implementationTaskId: string;
  repositorySnapshotId: string | null;
  triggeredByUserId: string | null;
  agentType: string;
  status: ImplementationRunStatus;
  proposedFileChanges: ProposedFileChangeItem[];
  diffText: string;
  explanation: string;
  testCommand: string;
  risks: string[];
  usedMock: boolean;
  tokenUsage: Record<string, number> | null;
  cost: number | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
  reviewStatus: ImplementationRunReviewStatus;
  reviewedByUserId: string | null;
  reviewedAt: string | null;
  reviewComment: string | null;
  createdAt: string;
  updatedAt: string;
  pullRequest: PullRequestLinkItem | null;
}

// Testing Agent system — see apps/api/app/services/testing_agent.py and
// apps/api/app/models/test_run.py. `testsExecuted`/pass-fail are a
// reasoning-based assessment (no execution sandbox exists) — a real
// test_report Artifact + QA Review (see reviewId) is what actually gates
// approval, not this run itself.
export type TestAgentType = "UNIT" | "API" | "UI" | "REGRESSION" | "SECURITY";
export type TestRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface TestToAddItem {
  name: string;
  description: string;
  area: string;
}

export interface TestExecutedItem {
  name: string;
  result: string; // "PASS" | "FAIL"
  notes: string;
}

export interface TestRunItem {
  id: string;
  projectId: string;
  workflowNodeId: string | null;
  implementationTaskId: string;
  implementationRunId: string;
  pullRequestLinkId: string | null;
  storyId: string | null;
  laneId: string | null;
  artifactId: string | null;
  artifactVersionId: string | null;
  storyArtifactId: string | null;
  triggeredByUserId: string | null;
  agentType: TestAgentType;
  testAgentKey: string | null;
  status: TestRunStatus;
  testPlan: string;
  testsToAdd: TestToAddItem[];
  testsExecuted: TestExecutedItem[];
  passCount: number;
  failCount: number;
  bugsFound: string[];
  suggestedFixes: string[];
  coverageImpact: Record<string, string>;
  evidenceAttachments: string[];
  usedMock: boolean;
  tokenUsage: Record<string, number> | null;
  cost: number | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
  reviewId: string | null;
}

// Maintenance Agent — see apps/api/app/services/maintenance_agent.py and
// apps/api/app/models/maintenance_run.py. Repeatable, manually-triggered
// project health reports; unlike TestRunItem, there is no reviewId — the
// rule is "recommend actions only", so nothing here is ever approved.
export type MaintenanceRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface MaintenanceRunItem {
  id: string;
  projectId: string;
  workflowNodeId: string;
  triggeredByUserId: string | null;
  status: MaintenanceRunStatus;
  errorLogsInput: string | null;
  userFeedbackInput: string | null;
  reportMarkdown: string | null;
  artifactId: string | null;
  artifactVersionId: string | null;
  usedMock: boolean;
  tokenUsage: Record<string, number> | null;
  cost: number | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

// PR Review Agent — see apps/api/app/services/pr_review_agent.py and
// apps/api/app/models/pr_review_run.py. Fully bespoke: no Artifact/Review
// exists for this. "Human reviewer decides final approval" is the real
// GitHub PR review, external to this app — overallRecommendation is
// advisory input to that human, never an approval this app grants.
export type PRReviewRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
export type PRReviewRecommendation = "APPROVE" | "REQUEST_CHANGES" | "COMMENT_ONLY";

export interface FindingItem {
  file: string;
  detail: string;
}

export interface SuggestedCommentItem {
  file: string;
  body: string;
}

export interface PostedCommentItem {
  file: string;
  body: string;
  githubCommentId: number;
  githubCommentUrl: string;
  postedAt: string;
}

export interface PRReviewRunItem {
  id: string;
  projectId: string;
  workflowNodeId: string | null; // null for a story-scoped run
  implementationTaskId: string;
  implementationRunId: string;
  pullRequestLinkId: string;
  storyId: string | null;
  triggeredByUserId: string | null;
  status: PRReviewRunStatus;
  overallRecommendation: PRReviewRecommendation | null;
  summary: string;
  criticalFindings: FindingItem[];
  majorFindings: FindingItem[];
  minorFindings: FindingItem[];
  missingTests: string[];
  unrelatedChanges: string[];
  suggestedComments: SuggestedCommentItem[];
  riskScore: number | null;
  finalReviewerNote: string;
  postedComments: PostedCommentItem[];
  usedMock: boolean;
  tokenUsage: Record<string, number> | null;
  cost: number | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PostedCommentResultItem {
  file: string;
  body: string;
  status: "posted" | "failed";
  githubCommentId: number | null;
  githubCommentUrl: string | null;
  error: string | null;
}

export interface ArtifactSection {
  id: string;
  title: string;
  contentMarkdown: string;
  /** True only for the synthesized leading "Content" section — a title
   * that never actually appeared in the source document (see
   * lib/markdown-sections.ts's splitMarkdownIntoSections). Undefined/
   * falsy for every real `##`-heading section. */
  isSynthetic?: boolean;
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
  /** The artifact section title (see ArtifactSection.title) this review
   * comment is about, when the reviewer linked it to one — matches
   * apps/api/app/models/review.py's ReviewComment.section_title. Null for
   * general, document-wide feedback. Distinct from `sectionId` above,
   * which is the document editor's own per-load section anchor. */
  sectionTitle?: string | null;
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
  /** False for a headingless document (splitMarkdownIntoSections's
   * fallback single "Content" section, a synthetic label that never
   * appears in the source text) — see markdown-sections.ts's
   * hasRealSections. Gates "Improve section" in AgentActionsPanel. */
  hasRealSections: boolean;
  /** True when the current content is an agent's clarification request
   * (starts with "# Clarification Needed") rather than a real drafted
   * document — see markdown-sections.ts's isClarificationRequest. */
  needsClarification: boolean;
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

/** Result of POST /reviews/{id}/run-revision-agent — see
 * apps/api/app/services/revision_agent.py. */
export interface RevisionAgentRunResult {
  agentRunId: string;
  needsClarification: boolean;
  sectionsUpdated: string[];
  artifactVersionId: string | null;
  newReviewId: string | null;
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
  stage: string | null;
  domain: string | null;
  contentType: string | null;
  tags: string[];
}

/** Matches apps/api/app/models/enums.py's LoopStatus. */
export type LoopStatus =
  | "NOT_STARTED"
  | "RUNNING"
  | "COMPLETED_QUALITY_MET"
  | "COMPLETED_MAX_ITERATIONS"
  | "COMPLETED_NO_CRITICAL_ISSUES"
  | "WAITING_FOR_CLARIFICATION";

/** The full structured validator output for one loop iteration — see
 * apps/api/app/services/validator_agent.py's ValidatorResult. */
export interface LoopValidationResult {
  quality_score?: number;
  completeness_score?: number;
  clarity_score?: number;
  risk_coverage_score?: number;
  critical_issues?: string[];
  suggestions?: string[];
  approval_recommendation?: string;
  /** Specific required content the validator found absent or too thin —
   * distinct from critical_issues (blocking problems broadly). */
  missing_details?: string[];
  /** The specific risks/assumptions the draft itself calls out — distinct
   * from risk_coverage_score, which only scores whether any were called
   * out at all. */
  risks?: string[];
  /** A coarser, reviewer-facing rollup of approval_recommendation:
   * "READY_FOR_REVIEW" | "NEEDS_IMPROVEMENT". */
  recommendation?: string;
}

/** Full detail for the Agent Run Detail screen, and reused as the "last
 * agent run" shown on a workflow node's detail panel. */
export interface AgentRunDetail {
  id: string;
  projectId: string;
  projectName: string;
  workflowNodeId: string;
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
  retrievedSourceTitles: string[] | null;
  /** Only DRAFT-action runs go through the Loop Engine — others stay at
   * NOT_STARTED/0/null defaults. */
  loopStatus: LoopStatus;
  loopIteration: number;
  loopMaxIterations: number | null;
  loopQualityThreshold: number | null;
  loopQualityScore: number | null;
  loopValidationIssues: string[] | null;
  loopValidationResult: LoopValidationResult | null;
  contextTokenBudget: number | null;
  outputTokenBudget: number | null;
  estimatedContextTokens: number | null;
  tokenBudgetReport: Record<string, unknown> | null;
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

// Real Jira integration — see apps/api/app/services/jira_integration.py
// and apps/api/app/api/routes/jira_integration.py. Distinct from
// JiraExportPreview above (the older, local, no-connection preview).
// Nothing here is ever pushed except what a human explicitly selects.
export type JiraSourceType = "EPIC" | "STORY" | "IMPLEMENTATION_TASK" | "TESTING_BUG";

export interface JiraConnectionItem {
  id: string;
  integrationId: string;
  baseUrl: string;
  email: string;
  status: "NOT_CONNECTED" | "CONNECTED" | "ERROR";
  connectedById: string | null;
  connectedByName: string | null;
  tokenHint: string;
  createdAt: string;
  updatedAt: string;
}

export interface JiraProjectLinkItem {
  id: string;
  projectId: string;
  connectionId: string;
  jiraProjectKey: string;
  jiraProjectName: string | null;
}

export interface JiraIssueLinkItem {
  id: string;
  sourceType: JiraSourceType;
  sourceKey: string;
  sourceLabel: string;
  jiraIssueKey: string;
  jiraIssueType: string;
  jiraIssueUrl: string;
  parentJiraIssueKey: string | null;
  jiraStatus: string | null;
  lastSyncedAt: string | null;
}

export interface JiraPushListItem {
  sourceType: JiraSourceType;
  sourceKey: string;
  label: string;
  jiraIssueType: string;
  parentSourceKey: string | null;
  summary: string;
  description: string;
  validationErrors: string[];
  alreadyLinked: JiraIssueLinkItem | null;
}

export interface JiraPushPreviewItem {
  projectId: string;
  jiraProjectKey: string;
  epics: JiraPushListItem[];
  stories: JiraPushListItem[];
  implementationTasks: JiraPushListItem[];
  testingBugs: JiraPushListItem[];
  overallErrors: string[];
}

export interface JiraPushResultEntry {
  sourceType: JiraSourceType;
  sourceKey: string;
  status: "created" | "skipped_duplicate" | "skipped_invalid" | "failed";
  jiraIssueKey: string | null;
  jiraIssueUrl: string | null;
  errors: string[];
}

// Real Confluence integration — see
// apps/api/app/services/confluence_integration.py and
// apps/api/app/api/routes/confluence_integration.py. Same connect/
// preview/publish shape as Jira above: nothing is ever published except
// the exact artifact types a human explicitly selects.
export interface ConfluenceConnectionItem {
  id: string;
  integrationId: string;
  baseUrl: string;
  email: string;
  status: "NOT_CONNECTED" | "CONNECTED" | "ERROR";
  connectedById: string | null;
  connectedByName: string | null;
  tokenHint: string;
  createdAt: string;
  updatedAt: string;
}

export interface ConfluenceSpaceLinkItem {
  id: string;
  projectId: string;
  connectionId: string;
  spaceKey: string;
  spaceName: string | null;
  rootPageId: string;
  rootPageUrl: string;
}

export interface ConfluencePageLinkItem {
  id: string;
  artifactType: string;
  confluencePageId: string;
  confluencePageUrl: string;
  confluencePageTitle: string;
  confluencePageVersion: number;
}

export interface ConfluencePublishListItem {
  artifactType: string;
  label: string;
  artifactId: string | null;
  artifactStatus: string | null;
  contentPreview: string;
  validationErrors: string[];
  alreadyPublished: ConfluencePageLinkItem | null;
  updateAvailable: boolean;
}

export interface ConfluencePublishPreviewItem {
  projectId: string;
  spaceKey: string;
  items: ConfluencePublishListItem[];
}

export interface ConfluencePublishResultEntry {
  artifactType: string;
  status: "published" | "updated" | "skipped_invalid" | "failed";
  confluencePageId: string | null;
  confluencePageUrl: string | null;
  errors: string[];
}

/** Local preview of a GitHub PR's title/description/checklist for a
 * code_change artifact — see apps/api/app/services/github_export.py. No
 * real GitHub connection exists; a human pastes this into a real PR. */
export interface GithubPrPreview {
  suggestedTitle: string;
  descriptionMarkdown: string;
  checklist: string[];
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
  totalCost: number;
  /** null until at least one run at this stage has gone through the Loop Engine. */
  avgQualityScore: number | null;
  avgIterations: number | null;
}

export interface OpsCostByProject {
  projectId: string;
  projectName: string;
  totalCost: number;
  runCount: number;
}

/** One recurring critical validation issue (see ValidatorResult) and how many VALIDATE steps raised it. */
export interface OpsValidationIssueFrequency {
  message: string;
  count: number;
}

/** One Knowledge Base source and how many agent runs actually cited it. */
export interface OpsRagSourceUsage {
  sourceTitle: string;
  count: number;
}

export interface OpsBlockedWorkflowRow {
  projectId: string;
  projectName: string;
  nodeKey: string;
  stageName: string;
  blockedReason: string | null;
  updatedAt: string;
}

/** Company-wide AI Ops metrics — see apps/api/app/services/ops_metrics.py. */
export interface OpsSummary {
  totalRuns: number;
  successfulRuns: number;
  failedRuns: number;
  /** null only when there have been zero runs at all. */
  successRate: number | null;
  failureRate: number | null;
  avgDurationSeconds: number | null;
  totalTokens: number;
  avgTokensPerRun: number | null;
  totalCost: number;
  /** null until at least one DRAFT run has gone through the Loop Engine. */
  avgQualityScore: number | null;
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
  costByProject: OpsCostByProject[];
  validationIssueFrequency: OpsValidationIssueFrequency[];
  ragSourceUsage: OpsRagSourceUsage[];
  blockedWorkflows: OpsBlockedWorkflowRow[];
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

/** GitHub integration foundation — see
 * apps/api/app/services/github_integration.py. Deliberately has NO token
 * field, encrypted or otherwise (see IntegrationConnection's own
 * docstring) — `tokenHint` (e.g. "****d3f9") is all a UI ever sees about
 * the stored credential. */
export interface GithubConnectionItem {
  id: string;
  integrationId: string;
  githubUsername: string | null;
  scopes: string[] | null;
  status: IntegrationStatus;
  connectedById: string | null;
  connectedByName: string | null;
  tokenHint: string;
  createdAt: string;
  updatedAt: string;
}

export interface GithubRepositoryItem {
  id: string;
  projectId: string;
  connectionId: string;
  owner: string;
  name: string;
  defaultBranch: string | null;
  description: string | null;
  htmlUrl: string | null;
  isPrivate: boolean | null;
  /** Multi-repo support — the repo a task uses by default when it doesn't
   * explicitly target one of the project's (possibly several) other
   * connected repositories. Exactly one per project. */
  isPrimary: boolean;
  createdAt: string;
  updatedAt: string;
}

/** One repo the connected token can see — for the repo picker in the
 * repository-configuration form, so owner/name is chosen from what
 * actually exists rather than typed by hand. */
export interface GitHubRepoOption {
  owner: string;
  name: string;
  fullName: string;
  defaultBranch: string;
  description: string | null;
  isPrivate: boolean;
  htmlUrl: string;
}

export type RepositoryFileEntryType = "FILE" | "DIRECTORY";

export interface RepositoryTreeEntryItem {
  path: string;
  entryType: RepositoryFileEntryType;
  size: number | null;
  sha: string;
}

export interface RepositoryTreeResult {
  commitSha: string;
  entries: RepositoryTreeEntryItem[];
  /** GitHub's own tree API sets this when the repo has more entries than
   * one recursive call can return — surfaced honestly rather than
   * silently presenting a partial scan as complete. */
  truncated: boolean;
}

export interface RepositoryFileContentResult {
  path: string;
  sha: string;
  size: number;
  /** Null when `truncated` (too large to preview) or `isBinary`. */
  content: string | null;
  truncated: boolean;
  isBinary: boolean;
}

export interface RepositorySnapshotItem {
  id: string;
  repositoryId: string;
  ref: string;
  commitSha: string;
  fileCount: number;
  truncated: boolean;
  triggeredById: string | null;
  triggeredByName: string | null;
  createdAt: string;
}

export interface RepositoryFileIndexItem {
  id: string;
  snapshotId: string;
  path: string;
  entryType: RepositoryFileEntryType;
  size: number | null;
  sha: string;
}

// Repo Context Builder preview — see apps/api/app/services/repo_context_builder.py.
export interface RelevantFileItem {
  path: string;
  entryType: "FILE";
  size: number | null;
  score: number;
  reasons: string[];
  contentMode: "full" | "summary" | "omitted";
  snippet: string | null;
}

export interface SuggestedEditScopeEntryItem {
  path: string;
  status: "existing" | "new";
}

export interface TokenBudgetReportBlockItem {
  priority: string;
  label: string;
  estimatedTokens: number;
  included: boolean;
  truncated: boolean;
}

export interface TokenBudgetReportItem {
  contextTokenBudget: number;
  outputTokenBudget: number;
  rawEstimatedTokens: number;
  estimatedTokens: number;
  overBudget: boolean;
  blocks: TokenBudgetReportBlockItem[];
}

export interface RepoContextPreviewItem {
  relevantFolders: string[];
  relevantFiles: RelevantFileItem[];
  architectureSummary: string;
  dependencyNotes: string[];
  suggestedEditScope: SuggestedEditScopeEntryItem[];
  tokenBudgetReport: TokenBudgetReportItem;
  filesConsidered: number;
  filesIncluded: number;
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
