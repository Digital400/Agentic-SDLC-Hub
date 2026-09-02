/**
 * Typed client for the FastAPI backend (apps/api). Functions here return
 * the backend's own response shapes (snake_case, matching apps/api/app/schemas/*)
 * — see lib/mappers.ts for converting those into this app's UI types
 * (lib/types.ts, camelCase).
 *
 * Works from both server components (page data fetching) and client
 * components (form submits, decisions) — see .env.example.
 */

// Exported for the rare case a caller needs the raw base URL directly
// (e.g. a plain download link/button rather than a fetch call) — see the
// Export Stories buttons in components/documents/artifact-editor.tsx.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // A FormData body (file upload) must NOT get a hardcoded JSON
  // Content-Type — the browser sets its own multipart boundary header
  // when it serializes the body, and overriding it here would break
  // parsing on the server. Every other call sends JSON.
  const isFormData = init?.body instanceof FormData;
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: isFormData ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
    // Server-component fetches default to caching in Next.js; this app's
    // data changes on every write, so always fetch fresh rather than
    // reason about revalidation tags per route.
    cache: "no-store",
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      // Non-JSON error body — fall back to statusText already set above.
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const patch = <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const del = <T>(path: string, body?: unknown) => request<T>(path, { method: "DELETE", body: body ? JSON.stringify(body) : undefined });
const postForm = <T>(path: string, formData: FormData) => request<T>(path, { method: "POST", body: formData });

// --- DTOs — mirror apps/api/app/schemas/*.py exactly (snake_case) ----------

export type ApiUserRole = "ADMIN" | "BA" | "PRODUCT_OWNER" | "ARCHITECT" | "TECH_LEAD" | "DEVELOPER" | "QA" | "DEVOPS" | "VIEWER";

export interface ApiUser {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  // Global functional role — see apps/api/app/services/permissions.py.
  role: ApiUserRole;
  created_at: string;
}

export interface ApiProject {
  id: string;
  name: string;
  description: string | null;
  business_owner: string;
  current_stage: string;
  status: "ACTIVE" | "COMPLETED" | "ARCHIVED";
  workflow_template_id: string;
  workflow_template_version: string;
  created_by_id: string;
  created_at: string;
  updated_at: string;
}

export interface ApiWorkflowNode {
  id: string;
  project_id: string;
  node_key: string;
  name: string;
  description: string;
  agent_key: string;
  required_inputs: string[];
  output_artifact_type: string;
  requires_human_approval: boolean;
  allowed_actions: string[];
  status: string;
  // Set by GraphEngineService when the node's own rules block it (distinct
  // from a manual override) — see app/services/graph_engine.py. Null unless
  // status is BLOCKED.
  blocked_reason: string | null;
  // Set only when the node's current status came from a manual override
  // (PATCH .../workflow-nodes/{id}) rather than normal graph progression.
  override_reason: string | null;
  // Token Budget Service ceilings for this node's agent runs — see
  // app/services/token_budget.py.
  context_token_budget: number;
  output_token_budget: number;
  full_content_artifact_types: string[];
  rag_top_k: number;
  max_rag_tokens: number;
  // A `## <heading>` this stage's artifact must have, with non-empty
  // content, before a review can approve it — see
  // GraphEngineService.validate_evidence_requirement. Null for most stages.
  required_evidence_section: string | null;
  order_index: number;
  position_x: number;
  position_y: number;
}

export interface ApiWorkflowEdge {
  id: string;
  project_id: string;
  source_node_id: string;
  target_node_id: string;
  label: string | null;
}

export interface ApiArtifact {
  id: string;
  project_id: string;
  workflow_node_id: string;
  artifact_type: string;
  title: string;
  current_version_id: string | null;
  status: "DRAFT" | "READY_FOR_REVIEW" | "APPROVED" | "NEEDS_CHANGES" | "REJECTED";
  created_by_id: string;
  created_at: string;
  updated_at: string;
  // Denormalized by ArtifactRead.from_orm_artifact — see apps/api/app/schemas/artifact.py.
  project_name: string;
  workflow_stage_name: string;
  current_version_number: number | null;
}

// See app/models/implementation_task.py / app/services/implementation_planner.py.
export type ApiImplementationTaskArea = "BACKEND" | "FRONTEND" | "DATABASE" | "TESTING" | "INFRA" | "DOCS";
export type ApiImplementationTaskRiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type ApiImplementationTaskStatus = "PENDING" | "IN_PROGRESS" | "COMPLETED" | "BLOCKED";

export interface ApiImplementationTask {
  id: string;
  project_id: string;
  story_id: string | null;
  workflow_node_id: string | null;
  artifact_id: string | null;
  artifact_version_id: string | null;
  title: string;
  description: string;
  linked_story: string | null;
  linked_lld_section: string | null;
  area: ApiImplementationTaskArea;
  expected_paths: string[];
  dependencies: string[];
  acceptance_criteria: string[];
  test_expectation: string;
  risk_level: ApiImplementationTaskRiskLevel;
  assigned_agent_type: string;
  status: ApiImplementationTaskStatus;
  order_index: number;
  created_at: string;
  updated_at: string;
}

export interface ApiGenerateImplementationPlanResponse {
  artifact_id: string;
  artifact_version_id: string;
  tasks: ApiImplementationTask[];
  review: ApiReview;
}

// Implementation Agent execution — see app/services/implementation_agent.py
// and app/models/implementation_run.py. Generating/reviewing a run never
// writes to GitHub; only create_pull_request (once ACCEPTED) does — see
// ApiPullRequestLink below.
export type ApiImplementationRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
export type ApiImplementationRunReviewStatus = "PENDING_REVIEW" | "ACCEPTED" | "REJECTED";

export interface ApiProposedFileChange {
  path: string;
  change_type: string; // "create" | "modify" | "delete"
  summary: string;
  after_content: string | null;
}

export type ApiPullRequestStatus = "OPEN" | "MERGED" | "CLOSED";

export interface ApiPullRequestLink {
  id: string;
  project_id: string;
  workflow_node_id: string | null;
  implementation_task_id: string;
  implementation_run_id: string;
  repository_id: string;
  story_id: string | null;
  lane_id: string | null;
  code_run_id: string | null;
  jira_issue_key: string | null;
  branch_name: string;
  base_branch: string;
  pr_number: number;
  pr_url: string;
  status: ApiPullRequestStatus;
  created_by_agent: boolean;
  triggered_by_user_id: string | null;
  commit_message: string;
  created_at: string;
}

// CodeRunnerService — see app/models/code_run.py and
// app/api/routes/code_runs.py. The local-git alternative to
// api.implementationRuns.createPullRequest (which commits file-by-file
// through GitHub's REST API); this applies an accepted patch through a
// real, isolated clone, runs configured tests, and only on success
// commits/pushes a real branch.
export type ApiCodeRunStatus =
  | "QUEUED" | "CLONING" | "BRANCH_CREATED" | "APPLYING_CHANGES" | "TESTING" | "COMMITTED" | "PUSHED" | "FAILED";

export interface ApiCodeRunLogEntry {
  timestamp: string;
  level: string;
  message: string;
}

export interface ApiCodeRun {
  id: string;
  project_id: string;
  story_id: string;
  lane_id: string | null;
  repository_id: string;
  triggered_by_user_id: string | null;
  branch_name: string;
  status: ApiCodeRunStatus;
  started_at: string | null;
  completed_at: string | null;
  logs: ApiCodeRunLogEntry[];
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiImplementationRun {
  id: string;
  project_id: string;
  implementation_task_id: string;
  repository_snapshot_id: string | null;
  triggered_by_user_id: string | null;
  story_id: string | null;
  lane_id: string | null;
  story_lld_artifact_id: string | null;
  assigned_user_id: string | null;
  agent_type: string;
  assigned_agent_key: string | null;
  status: ApiImplementationRunStatus;
  proposed_file_changes: ApiProposedFileChange[];
  diff_text: string;
  explanation: string;
  test_command: string;
  risks: string[];
  pr_description: string;
  used_mock: boolean;
  token_usage: Record<string, number> | null;
  cost: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  review_status: ApiImplementationRunReviewStatus;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  review_comment: string | null;
  created_at: string;
  updated_at: string;
  pull_request: ApiPullRequestLink | null;
}

// Testing Agent system — see app/services/testing_agent.py and
// app/models/test_run.py. Reasoning-based test assessment (no execution
// sandbox exists); a real test_report Artifact + QA Review is what
// actually gates approval — see review_id below.
export type ApiTestAgentType = "UNIT" | "API" | "UI" | "REGRESSION" | "SECURITY";
export type ApiTestRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface ApiTestToAdd {
  name: string;
  description: string;
  area: string;
}

export interface ApiTestExecuted {
  name: string;
  result: string; // "PASS" | "FAIL"
  notes: string;
}

export interface ApiTestRun {
  id: string;
  project_id: string;
  workflow_node_id: string | null;
  implementation_task_id: string;
  implementation_run_id: string;
  pull_request_link_id: string | null;
  story_id: string | null;
  lane_id: string | null;
  artifact_id: string | null;
  artifact_version_id: string | null;
  story_artifact_id: string | null;
  triggered_by_user_id: string | null;
  agent_type: ApiTestAgentType;
  test_agent_key: string | null;
  status: ApiTestRunStatus;
  test_plan: string;
  tests_to_add: ApiTestToAdd[];
  tests_executed: ApiTestExecuted[];
  pass_count: number;
  fail_count: number;
  bugs_found: string[];
  suggested_fixes: string[];
  coverage_impact: Record<string, string>;
  evidence_attachments: string[];
  used_mock: boolean;
  token_usage: Record<string, number> | null;
  cost: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  review_id: string | null;
}

// PR Review Agent — see app/services/pr_review_agent.py and
// app/models/pr_review_run.py. Fully bespoke: no Artifact/Review exists
// for this. "Human reviewer decides final approval" is the real GitHub
// PR review, external to this app — overallRecommendation is advisory.
export type ApiPRReviewRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
export type ApiPRReviewRecommendation = "APPROVE" | "REQUEST_CHANGES" | "COMMENT_ONLY";

export interface ApiFinding {
  file: string;
  detail: string;
}

export interface ApiSuggestedComment {
  file: string;
  body: string;
}

export interface ApiPostedComment {
  file: string;
  body: string;
  github_comment_id: number;
  github_comment_url: string;
  posted_at: string;
}

export interface ApiPRReviewRun {
  id: string;
  project_id: string;
  workflow_node_id: string | null; // null for a story-scoped run
  implementation_task_id: string;
  implementation_run_id: string;
  pull_request_link_id: string;
  story_id: string | null;
  triggered_by_user_id: string | null;
  status: ApiPRReviewRunStatus;
  overall_recommendation: ApiPRReviewRecommendation | null;
  summary: string;
  critical_findings: ApiFinding[];
  major_findings: ApiFinding[];
  minor_findings: ApiFinding[];
  missing_tests: string[];
  suggested_comments: ApiSuggestedComment[];
  risk_score: number | null;
  final_reviewer_note: string;
  posted_comments: ApiPostedComment[];
  used_mock: boolean;
  token_usage: Record<string, number> | null;
  cost: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiPostedCommentResult {
  file: string;
  body: string;
  status: "posted" | "failed";
  github_comment_id: number | null;
  github_comment_url: string | null;
  error: string | null;
}

export interface ApiPostPRReviewCommentsResponse {
  run: ApiPRReviewRun;
  results: ApiPostedCommentResult[];
}

export interface ApiArtifactVersion {
  id: string;
  artifact_id: string;
  version_number: number;
  content_markdown: string;
  content_json: Record<string, unknown> | null;
  change_summary: string | null;
  created_by_id: string;
  created_at: string;
  // Denormalized by ArtifactVersionRead.from_orm_version.
  created_by_name: string;
}

// Scrum story lanes — see apps/api/app/schemas/story.py.
export interface ApiStory {
  id: string;
  project_id: string;
  source_artifact_version_id: string;
  story_type: "VERTICAL" | "HORIZONTAL";
  status: "PENDING" | "IN_SPRINT" | "LANE_ACTIVE" | "DONE";
  epic: string;
  feature: string;
  title: string;
  user_story: string;
  priority: string;
  dependencies: string;
  acceptance_criteria: string[];
  definition_of_done: string[];
  suggested_owner_role: string | null;
  story_points: number | null;
  business_value: string;
  technical_areas: string[];
  jira_issue_type: string;
  suggested_subtasks: string[];
  release_readiness_criteria: string[];
  owner_user_id: string | null;
  sprint_id: string | null;
  lane_created_at: string | null;
  created_by_id: string;
  created_at: string;
  updated_at: string;
  // Real, stored fields — see app/models/story.py. jira_sync_status is
  // this app's own create-state machine, distinct from jira_status
  // below (Jira's own live workflow status).
  jira_sync_status: "NOT_SYNCED" | "SYNC_PENDING" | "SYNCED" | "SYNC_FAILED";
  jira_issue_id: string | null;
  // Computed server-side — see app/api/routes/stories.py's _story_to_read.
  lane_status: string;
  jira_status: string;
  jira_issue_key: string | null;
  jira_issue_url: string | null;
}

// Story delivery lane — dedicated per-story graph, see
// app/services/story_delivery.py. Not the project-level WorkflowNode/
// WorkflowEdge graph engine.
export interface ApiStoryDeliveryLane {
  id: string;
  project_id: string;
  story_id: string;
  current_node_id: string | null;
  status: "ACTIVE" | "BLOCKED" | "COMPLETED";
  created_by_id: string;
  created_at: string;
  updated_at: string;
}

export interface ApiStoryDeliveryNode {
  id: string;
  lane_id: string;
  node_key: string;
  name: string;
  status: "LOCKED" | "READY" | "IN_PROGRESS" | "WAITING_FOR_REVIEW" | "BLOCKED" | "COMPLETED";
  assigned_role: string | null;
  assigned_user_id: string | null;
  requires_approval: boolean;
  blocked_reason: string | null;
  started_at: string | null;
  completed_at: string | null;
  order_index: number;
  created_at: string;
  updated_at: string;
}

export interface ApiStoryArtifact {
  id: string;
  story_id: string;
  lane_id: string | null;
  node_id: string | null;
  artifact_type: string;
  title: string;
  content_markdown: string;
  version_number: number;
  created_by_id: string;
  created_at: string;
  updated_at: string;
}

export interface ApiSprint {
  id: string;
  project_id: string;
  name: string;
  goal: string;
  start_date: string | null;
  end_date: string | null;
  capacity_points: number | null;
  status: "PLANNED" | "ACTIVE" | "COMPLETED" | "CANCELLED";
  created_by_id: string;
  created_at: string;
  updated_at: string;
}

export interface ApiSprintStory {
  id: string;
  sprint_id: string;
  story_id: string;
  planned_points: number | null;
  assigned_owner_id: string | null;
  status: "PLANNED" | "IN_PROGRESS" | "DONE" | "REMOVED";
  created_at: string;
  updated_at: string;
}

export interface ApiSprintBoard {
  sprint: ApiSprint;
  items: { sprint_story: ApiSprintStory; story: ApiStory }[];
  planned_points_total: number;
  capacity_points: number | null;
  over_capacity: boolean;
}

// Release planning from story delivery lanes — see app/models/release.py
// and app/api/routes/releases.py. Distinct from the older per-Sprint
// release_planning stage (api.sprints' generateReleasePlan).
export interface ApiRelease {
  id: string;
  project_id: string;
  name: string;
  version: string;
  target_date: string | null;
  status: "DRAFT" | "APPROVED" | "RELEASED" | "CANCELLED";
  release_notes: string;
  created_by_id: string;
  approved_by_id: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiReleaseStory {
  id: string;
  release_id: string;
  story_id: string;
  added_by_id: string;
  created_at: string;
}

export interface ApiReleaseBoard {
  release: ApiRelease;
  items: { release_story: ApiReleaseStory; story: ApiStory }[];
}

export interface ApiReviewComment {
  id: string;
  review_id: string;
  author_id: string;
  body: string;
  // Matches an ArtifactSection heading (see lib/markdown-sections.ts) when
  // the reviewer could link this comment to one — null for general,
  // document-wide feedback.
  section_title: string | null;
  created_at: string;
}

export interface ApiReview {
  id: string;
  artifact_version_id: string;
  workflow_node_id: string;
  reviewer_id: string;
  status: "PENDING" | "APPROVED" | "NEEDS_CHANGES" | "REJECTED";
  decided_at: string | null;
  created_at: string;
  updated_at: string;
  comments: ApiReviewComment[];
  // Denormalized by ReviewRead.from_orm_review — see apps/api/app/schemas/review.py.
  project_id: string;
  project_name: string;
  artifact_id: string;
  artifact_title: string;
  workflow_stage_name: string;
  reviewer_name: string;
}

export interface ApiReviewCommentInput {
  body: string;
  section_title?: string | null;
}

export interface ApiRevisionAgentRunResponse {
  agent_run: ApiAgentRun;
  needs_clarification: boolean;
  sections_updated: string[];
  artifact_version_id: string | null;
  artifact_status: string | null;
  workflow_node_status: string;
  new_review: ApiReview | null;
}

export interface ApiAgentPrompt {
  id: string;
  agent_definition_id: string;
  agent_key: string;
  role: "draft" | "improve" | "validate";
  version: number;
  name: string;
  stage: string;
  system_prompt: string;
  output_format: string;
  validation_checklist: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ApiKnowledgeSource {
  id: string;
  title: string;
  category: string;
  source_type: "PROJECT_ARTIFACT" | "UPLOADED_DOCUMENT" | "EXTERNAL_LINK";
  file_url: string | null;
  status: "PENDING" | "PROCESSING" | "INDEXED" | "FAILED";
  uploaded_by_id: string;
  created_at: string;
  updated_at: string;
  uploaded_by_name: string;
  chunk_count: number;
}

export interface ApiKnowledgeSearchResult {
  chunk_id: string;
  source_id: string;
  source_title: string;
  chunk_index: number;
  content: string;
  similarity: number;
}

export interface ApiKnowledgeChunk {
  id: string;
  source_id: string;
  chunk_index: number;
  content: string;
  metadata_json: Record<string, unknown> | null;
  embedding: number[] | null;
  created_at: string;
}

export interface ApiJiraFieldMapping {
  epic: string | null;
  labels: string[];
  summary: string;
  description: string;
  priority: string | null;
  linked_issues_placeholder: string[];
}

// NOTE: distinct from ApiStoryJiraPreview below (the real per-story sync
// preview, app/services/story_jira_sync.py) — this one is the older,
// local, no-connection whole-backlog preview (app/services/jira_export.py).
// Named differently to avoid a TypeScript declaration-merging collision
// that silently unioned the two shapes together before this fix.
export interface ApiJiraExportStoryPreview {
  story_title: string;
  jira_issue_type: string;
  mapping: ApiJiraFieldMapping;
  validation_errors: string[];
  is_valid: boolean;
}

export interface ApiJiraExportPreview {
  project_id: string;
  artifact_id: string;
  artifact_title: string;
  story_count: number;
  valid_story_count: number;
  has_errors: boolean;
  overall_errors: string[];
  stories: ApiJiraExportStoryPreview[];
  push_to_jira_enabled: boolean;
}

// Real Jira integration — see app/services/jira_integration.py and
// app/api/routes/jira_integration.py. Distinct from ApiJiraExportPreview
// above (that's the older, local, no-connection story-only preview; this
// is the real connect/preview/push flow covering Epics/Stories/
// Implementation Tasks/Testing bugs). Nothing here is ever pushed except
// what a human explicitly selects — see JiraPushRequest.
export type ApiJiraSourceType = "EPIC" | "STORY" | "IMPLEMENTATION_TASK" | "TESTING_BUG";

export interface ApiJiraConnection {
  id: string;
  integration_id: string;
  base_url: string;
  email: string;
  status: "NOT_CONNECTED" | "CONNECTED" | "ERROR";
  connected_by_id: string | null;
  created_at: string;
  updated_at: string;
  token_hint: string;
  connected_by_name: string | null;
}

export interface ApiConnectJiraRequest {
  base_url: string;
  email: string;
  api_token: string;
  connected_by_id: string;
}

export interface ApiJiraProjectLink {
  id: string;
  project_id: string;
  connection_id: string;
  jira_project_key: string;
  jira_project_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiJiraIssueLink {
  id: string;
  project_id: string;
  jira_project_link_id: string;
  source_type: ApiJiraSourceType;
  source_key: string;
  source_label: string;
  jira_issue_key: string;
  jira_issue_type: string;
  jira_issue_url: string;
  parent_jira_issue_key: string | null;
  jira_status: string | null;
  last_synced_at: string | null;
  created_at: string;
}

export interface ApiJiraPushItem {
  source_type: ApiJiraSourceType;
  source_key: string;
  label: string;
  jira_issue_type: string;
  parent_source_key: string | null;
  summary: string;
  description: string;
  validation_errors: string[];
  already_linked: ApiJiraIssueLink | null;
}

export interface ApiJiraPushPreview {
  project_id: string;
  jira_project_key: string;
  epics: ApiJiraPushItem[];
  stories: ApiJiraPushItem[];
  implementation_tasks: ApiJiraPushItem[];
  testing_bugs: ApiJiraPushItem[];
  overall_errors: string[];
}

export interface ApiJiraPushResultItem {
  source_type: ApiJiraSourceType;
  source_key: string;
  status: "created" | "skipped_duplicate" | "skipped_invalid" | "failed";
  jira_issue_key: string | null;
  jira_issue_url: string | null;
  errors: string[];
}

export interface ApiJiraPushResponse {
  results: ApiJiraPushResultItem[];
}

export interface ApiJiraSyncStatusResponse {
  links: ApiJiraIssueLink[];
}

// Per-story Jira sync — see app/services/story_jira_sync.py and
// app/api/routes/jira_integration.py's /jira/stories/... routes.
// Story Points / Sprint have no standard Jira field, so both are shown
// as plainly-labeled lines inside `description` — exactly what a sync
// would send, never a hidden custom-field guess.
export interface ApiStoryJiraSubtaskPreview {
  implementation_task_id: string;
  title: string;
  description: string;
  validation_errors: string[];
  already_linked: ApiJiraIssueLink | null;
}

export interface ApiStoryJiraPreview {
  story_id: string;
  summary: string;
  description: string;
  priority: string | null;
  story_points: number | null;
  sprint_name: string | null;
  labels: string[];
  subtasks: ApiStoryJiraSubtaskPreview[];
  validation_errors: string[];
  already_linked: ApiJiraIssueLink | null;
}

export interface ApiBulkStoryJiraPreviewResponse {
  previews: ApiStoryJiraPreview[];
}

export interface ApiSubtaskJiraSyncResult {
  implementation_task_id: string;
  status: "created" | "skipped_duplicate" | "skipped_invalid" | "failed";
  jira_issue_key: string | null;
  jira_issue_url: string | null;
  errors: string[];
}

export interface ApiStoryJiraSyncResult {
  story_id: string;
  status: "created" | "skipped_duplicate" | "skipped_invalid" | "failed";
  jira_issue_key: string | null;
  jira_issue_url: string | null;
  errors: string[];
  subtasks: ApiSubtaskJiraSyncResult[];
}

export interface ApiBulkStoryJiraSyncResponse {
  results: ApiStoryJiraSyncResult[];
}

// Real Confluence integration — see app/services/confluence_integration.py
// and app/api/routes/confluence_integration.py. Same connect/preview/
// publish shape as Jira above: nothing is ever published except the
// exact artifact_types a human explicitly selects — see
// ApiConfluencePublishRequest below.
export interface ApiConfluenceConnection {
  id: string;
  integration_id: string;
  base_url: string;
  email: string;
  status: "NOT_CONNECTED" | "CONNECTED" | "ERROR";
  connected_by_id: string | null;
  created_at: string;
  updated_at: string;
  token_hint: string;
  connected_by_name: string | null;
}

export interface ApiConnectConfluenceRequest {
  base_url: string;
  email: string;
  api_token: string;
  connected_by_id: string;
}

export interface ApiConfluenceSpaceLink {
  id: string;
  project_id: string;
  connection_id: string;
  space_key: string;
  space_name: string | null;
  root_page_id: string;
  root_page_url: string;
  created_at: string;
  updated_at: string;
}

export interface ApiConfluencePageLink {
  id: string;
  project_id: string;
  confluence_space_link_id: string;
  artifact_type: string;
  artifact_id: string;
  artifact_version_id: string;
  confluence_page_id: string;
  confluence_page_url: string;
  confluence_page_title: string;
  confluence_page_version: number;
  created_at: string;
  updated_at: string;
}

export interface ApiConfluencePublishItem {
  artifact_type: string;
  label: string;
  artifact_id: string | null;
  artifact_status: string | null;
  content_preview: string;
  validation_errors: string[];
  already_published: ApiConfluencePageLink | null;
  update_available: boolean;
}

export interface ApiConfluencePublishPreview {
  project_id: string;
  space_key: string;
  items: ApiConfluencePublishItem[];
}

export interface ApiConfluencePublishResultItem {
  artifact_type: string;
  status: "published" | "updated" | "skipped_invalid" | "failed";
  confluence_page_id: string | null;
  confluence_page_url: string | null;
  errors: string[];
}

export interface ApiConfluencePublishResponse {
  results: ApiConfluencePublishResultItem[];
}

// See app/services/github_export.py — no real GitHub connection exists;
// this is what a human pastes into a real PR's title/description boxes.
export interface ApiGithubPrPreview {
  suggested_title: string;
  description_markdown: string;
  checklist: string[];
}

export interface ApiOpsAgentRunRow {
  id: string;
  project_id: string;
  project_name: string;
  workflow_stage_name: string;
  agent_key: string;
  action: string;
  status: string;
  duration_seconds: number | null;
  total_tokens: number | null;
  cost: number | null;
  error_message: string | null;
  created_at: string;
}

export interface ApiOpsStagePerformance {
  node_key: string;
  stage_name: string;
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  success_rate: number | null;
  avg_duration_seconds: number | null;
  total_cost: number;
  avg_quality_score: number | null;
  avg_iterations: number | null;
}

export interface ApiOpsCostByProject {
  project_id: string;
  project_name: string;
  total_cost: number;
  run_count: number;
}

export interface ApiOpsValidationIssueFrequency {
  message: string;
  count: number;
}

export interface ApiOpsRagSourceUsage {
  source_title: string;
  count: number;
}

export interface ApiOpsBlockedWorkflowRow {
  project_id: string;
  project_name: string;
  node_key: string;
  stage_name: string;
  blocked_reason: string | null;
  updated_at: string;
}

export interface ApiOpsSummary {
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  success_rate: number | null;
  failure_rate: number | null;
  avg_duration_seconds: number | null;
  total_tokens: number;
  avg_tokens_per_run: number | null;
  total_cost: number;
  avg_quality_score: number | null;
  approval_rate: number | null;
  rejection_rate: number | null;
  decided_review_count: number;
  human_change_rate: null;
  human_change_rate_note: string;
  blocked_workflow_count: number;
  recent_runs: ApiOpsAgentRunRow[];
  recent_failures: ApiOpsAgentRunRow[];
  stage_performance: ApiOpsStagePerformance[];
  cost_by_project: ApiOpsCostByProject[];
  validation_issue_frequency: ApiOpsValidationIssueFrequency[];
  rag_source_usage: ApiOpsRagSourceUsage[];
  blocked_workflows: ApiOpsBlockedWorkflowRow[];
}

export interface ApiIntegration {
  id: string;
  integration_name: string;
  provider: "JIRA" | "CONFLUENCE" | "GITHUB" | "SLACK" | "TEAMS" | "AZURE_DEVOPS";
  status: "NOT_CONNECTED" | "CONNECTED" | "ERROR";
  config_json: Record<string, unknown> | null;
  connected_by_id: string | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
  connected_by_name: string | null;
}

// --- GitHub integration foundation — see app/services/github_integration.py ---------
// SECURITY: the access token is write-only. It appears only in
// ApiConnectGitHubRequest, never in any *Read type below.

export type ApiIntegrationStatus = "NOT_CONNECTED" | "CONNECTED" | "ERROR";

export interface ApiConnectGitHubRequest {
  access_token: string;
  connected_by_id: string;
}

export interface ApiIntegrationConnection {
  id: string;
  integration_id: string;
  github_username: string | null;
  scopes: string[] | null;
  status: ApiIntegrationStatus;
  connected_by_id: string | null;
  created_at: string;
  updated_at: string;
  // Display-only hint (e.g. "****d3f9") — never the real token.
  token_hint: string;
  connected_by_name: string | null;
}

export interface ApiCreateRepositoryRequest {
  project_id: string;
  connection_id: string;
  owner: string;
  name: string;
}

export interface ApiRepository {
  id: string;
  project_id: string;
  connection_id: string;
  owner: string;
  name: string;
  default_branch: string | null;
  description: string | null;
  html_url: string | null;
  is_private: boolean | null;
  created_at: string;
  updated_at: string;
}

// One repo the connected token can see — backs the repo picker in the
// repository-configuration form (see app/services/github_integration.py's
// list_repositories). Not persisted; a fresh list on every fetch.
export interface ApiGitHubRepoSummary {
  owner: string;
  name: string;
  full_name: string;
  default_branch: string;
  description: string | null;
  is_private: boolean;
  html_url: string;
}

// Maintenance Agent system — see app/services/maintenance_agent.py and
// app/api/routes/maintenance_runs.py. Repeatable, manually-triggered
// project health reports — unlike Testing/PR Review, no Review is ever
// created (rule: "recommend actions only" — nothing here needs approval).
export type ApiMaintenanceRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface ApiMaintenanceRun {
  id: string;
  project_id: string;
  workflow_node_id: string;
  triggered_by_user_id: string | null;
  status: ApiMaintenanceRunStatus;
  error_logs_input: string | null;
  user_feedback_input: string | null;
  report_markdown: string | null;
  artifact_id: string | null;
  artifact_version_id: string | null;
  used_mock: boolean;
  token_usage: Record<string, number> | null;
  cost: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export type ApiRepositoryFileEntryType = "FILE" | "DIRECTORY";

export interface ApiRepositoryTreeEntry {
  path: string;
  entry_type: ApiRepositoryFileEntryType;
  size: number | null;
  sha: string;
}

export interface ApiRepositoryTree {
  commit_sha: string;
  entries: ApiRepositoryTreeEntry[];
  truncated: boolean;
}

export interface ApiRepositoryFileContent {
  path: string;
  sha: string;
  size: number;
  content: string | null;
  truncated: boolean;
  is_binary: boolean;
}

export interface ApiCreateSnapshotRequest {
  triggered_by_id: string;
  ref?: string | null;
}

export interface ApiRepositorySnapshot {
  id: string;
  repository_id: string;
  ref: string;
  commit_sha: string;
  file_count: number;
  truncated: boolean;
  triggered_by_id: string | null;
  created_at: string;
  triggered_by_name: string | null;
}

export interface ApiRepositoryFileIndexEntry {
  id: string;
  snapshot_id: string;
  path: string;
  entry_type: ApiRepositoryFileEntryType;
  size: number | null;
  sha: string;
}

// Repo Context Builder — see apps/api/app/services/repo_context_builder.py.
// A stateless preview of exactly what repo context would be sent to a
// coding agent for one ImplementationTask; nothing here is persisted.
export interface ApiRelevantFile {
  path: string;
  entry_type: "FILE";
  size: number | null;
  score: number;
  reasons: string[];
  content_mode: "full" | "summary" | "omitted";
  snippet: string | null;
}

export interface ApiSuggestedEditScopeEntry {
  path: string;
  status: "existing" | "new";
}

export interface ApiRepoContextPreview {
  relevant_folders: string[];
  relevant_files: ApiRelevantFile[];
  architecture_summary: string;
  dependency_notes: string[];
  suggested_edit_scope: ApiSuggestedEditScopeEntry[];
  token_budget_report: {
    context_token_budget: number;
    output_token_budget: number;
    raw_estimated_tokens: number;
    estimated_tokens: number;
    over_budget: boolean;
    blocks: { priority: string; label: string; estimated_tokens: number; included: boolean; truncated: boolean }[];
  };
  files_considered: number;
  files_included: number;
}

export interface ApiValidatorDefinition {
  id: string;
  validator_key: string;
  name: string;
  stage: string;
  description: string | null;
  model_name: string;
  quality_threshold: number;
  criteria: string[];
  is_active: boolean;
}

export interface ApiAgentDefinition {
  id: string;
  agent_key: string;
  name: string;
  description: string | null;
  model_name: string;
  is_active: boolean;
  total_runs: number;
}

export interface ApiRetrievedSource {
  chunk_id: string;
  source_id: string;
  source_title: string;
  chunk_index: number;
  snippet: string;
  similarity: number;
  // Retrieval metadata added by the stage-aware RAG work — see
  // apps/api/app/services/retrieval.py. May be absent on older stored runs.
  stage?: string | null;
  domain?: string | null;
  project_type?: string | null;
  content_type?: string | null;
  tags?: string[];
}

// Matches apps/api/app/models/enums.py's LoopStatus.
export type ApiLoopStatus =
  | "NOT_STARTED"
  | "RUNNING"
  | "COMPLETED_QUALITY_MET"
  | "COMPLETED_MAX_ITERATIONS"
  | "COMPLETED_NO_CRITICAL_ISSUES"
  | "WAITING_FOR_CLARIFICATION";

export interface ApiAgentRun {
  id: string;
  project_id: string;
  workflow_node_id: string;
  agent_key: string;
  prompt_version: number | null;
  action: "draft" | "improve" | "validate";
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  input_artifact_ids: string[];
  input_context: Record<string, unknown>;
  output_text: string | null;
  output_artifact_id: string | null;
  error_message: string | null;
  token_usage: Record<string, number> | null;
  cost: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  // Knowledge Base chunks retrieved for this run — see
  // apps/api/app/services/retrieval.py. Null means retrieval never ran
  // (e.g. the run failed before reaching that step); [] means it ran and
  // found nothing relevant enough to inject.
  retrieved_sources: ApiRetrievedSource[] | null;
  // Deduped, ordered source titles behind retrieved_sources.
  retrieved_source_titles: string[] | null;
  // Loop Engine summary — see app/services/loop_engine.py. Only DRAFT-action
  // runs go through the loop; other actions stay at these defaults.
  loop_status: ApiLoopStatus;
  loop_iteration: number;
  loop_max_iterations: number | null;
  loop_quality_threshold: number | null;
  loop_quality_score: number | null;
  loop_validation_issues: string[] | null;
  // Full ValidatorResult dict: quality_score, completeness_score,
  // clarity_score, risk_coverage_score, critical_issues, suggestions,
  // approval_recommendation. Null if the run never reached VALIDATE.
  loop_validation_result: Record<string, unknown> | null;
  // Token Budget Service — the node's budgets that applied to this run, the
  // pre-call estimate, and the full per-block breakdown.
  context_token_budget: number | null;
  output_token_budget: number | null;
  estimated_context_tokens: number | null;
  token_budget_report: Record<string, unknown> | null;
}

// "Improve section" — see app/services/section_improve_agent.py.
export interface ApiImproveSectionResponse {
  agent_run: ApiAgentRun;
  needs_clarification: boolean;
  artifact_version_id: string | null;
  artifact_status: string;
  workflow_node_status: string;
  // None only when nothing was saved (a failed run, or a clarification
  // request instead of a revision).
  section_updated: string | null;
}

// --- Users -------------------------------------------------------------------

export const api = {
  users: {
    list: () => get<ApiUser[]>("/users"),
  },

  projects: {
    list: (params?: { status?: string }) => {
      const qs = params?.status ? `?status=${params.status}` : "";
      return get<{ items: ApiProject[]; total: number }>(`/projects${qs}`);
    },
    get: (id: string) => get<ApiProject>(`/projects/${id}`),
    create: (body: { name: string; business_owner: string; description?: string; created_by_id: string }) =>
      post<ApiProject>("/projects", body),
    update: (
      id: string,
      body: Partial<{ name: string; business_owner: string; description: string; current_stage: string }> & {
        updated_by_id: string;
      }
    ) => patch<ApiProject>(`/projects/${id}`, body),
    archive: (id: string) => post<ApiProject>(`/projects/${id}/archive`),
    workflowNodes: (id: string, storyId?: string) =>
      get<ApiWorkflowNode[]>(`/projects/${id}/workflow-nodes${storyId ? `?story_id=${storyId}` : ""}`),
    workflowEdges: (id: string, storyId?: string) =>
      get<ApiWorkflowEdge[]>(`/projects/${id}/workflow-edges${storyId ? `?story_id=${storyId}` : ""}`),
    // Manual override — bypasses every graph engine rule, so a reason and
    // the acting (existing) user are always required and every call is
    // audited. See GraphEngineService.manual_override.
    updateNodeStatus: (
      projectId: string,
      nodeId: string,
      body: { status: string; reason: string; overridden_by_id: string }
    ) => patch<ApiWorkflowNode>(`/projects/${projectId}/workflow-nodes/${nodeId}`, body),
    artifacts: (id: string) => get<ApiArtifact[]>(`/projects/${id}/artifacts`),
    agentRuns: (id: string) => get<ApiAgentRun[]>(`/projects/${id}/agent-runs`),
    previewJiraExport: (id: string) => post<ApiJiraExportPreview>(`/projects/${id}/stories/preview-jira-export`),
    implementationTasks: (id: string) => get<ApiImplementationTask[]>(`/projects/${id}/implementation-tasks`),
    // "Generate Implementation Plan" — see app/services/implementation_planner.py.
    // "Approve Implementation Plan" is the existing api.reviews.approve(reviewId) against
    // the review this returns, not a separate action.
    generateImplementationPlan: (id: string, body: { triggered_by_user_id: string; reviewer_id: string }) =>
      post<ApiGenerateImplementationPlanResponse>(`/projects/${id}/implementation-plan/generate`, body),
    githubRepository: (id: string) => get<ApiRepository | null>(`/projects/${id}/github-repository`),
    jiraProject: (id: string) => get<ApiJiraProjectLink | null>(`/projects/${id}/jira-project`),
    confluenceSpace: (id: string) => get<ApiConfluenceSpaceLink | null>(`/projects/${id}/confluence-space`),
    // Repo Context Preview (rule 7) — see app/services/repo_context_builder.py.
    // Read-only; safe to call as often as the user wants before a coding
    // agent ever exists to consume it.
    repoContextPreview: (
      projectId: string,
      taskId: string,
      params?: { snapshotId?: string; maxFiles?: number; maxTokens?: number }
    ) => {
      const qs = new URLSearchParams();
      if (params?.snapshotId) qs.set("snapshot_id", params.snapshotId);
      if (params?.maxFiles) qs.set("max_files", String(params.maxFiles));
      if (params?.maxTokens) qs.set("max_tokens", String(params.maxTokens));
      const query = qs.toString();
      return get<ApiRepoContextPreview>(
        `/projects/${projectId}/implementation-tasks/${taskId}/repo-context-preview${query ? `?${query}` : ""}`
      );
    },
    implementationTaskRuns: (projectId: string, taskId: string) =>
      get<ApiImplementationRun[]>(`/projects/${projectId}/implementation-tasks/${taskId}/implementation-runs`),
    implementationTaskTestRuns: (projectId: string, taskId: string) =>
      get<ApiTestRun[]>(`/projects/${projectId}/implementation-tasks/${taskId}/test-runs`),
    implementationTaskPrReviewRuns: (projectId: string, taskId: string) =>
      get<ApiPRReviewRun[]>(`/projects/${projectId}/implementation-tasks/${taskId}/pr-review-runs`),
    maintenanceRuns: (projectId: string) => get<ApiMaintenanceRun[]>(`/projects/${projectId}/maintenance-runs`),
  },

  artifacts: {
    get: (id: string) => get<ApiArtifact>(`/artifacts/${id}`),
    create: (body: { project_id: string; workflow_node_id: string; artifact_type: string; title: string; created_by_id: string }) =>
      post<ApiArtifact>("/artifacts", body),
    versions: (id: string) => get<ApiArtifactVersion[]>(`/artifacts/${id}/versions`),
    createVersion: (id: string, body: { content_markdown: string; change_summary?: string; created_by_id: string }) =>
      post<ApiArtifactVersion>(`/artifacts/${id}/versions`, body),
    updateContent: (id: string, body: { content_markdown: string; change_summary?: string; edited_by_id: string }) =>
      patch<ApiArtifactVersion>(`/artifacts/${id}`, body),
    submitForReview: (id: string) => post<ApiArtifact>(`/artifacts/${id}/submit-for-review`),
    // "Improve section" — see app/services/section_improve_agent.py. Only
    // works on a DRAFT artifact; every section besides `section_title` is
    // guaranteed unchanged. Never creates/touches a Review.
    improveSection: (id: string, body: { section_title: string; instruction: string; triggered_by_user_id: string }) =>
      post<ApiImproveSectionResponse>(`/artifacts/${id}/improve-section`, body),
    // "GitHub integration" (rule scope) — a local preview of the PR
    // title/description/checklist a human pastes into a real GitHub PR;
    // no real GitHub connection exists — see app/services/github_export.py.
    githubPrPreview: (id: string) => get<ApiGithubPrPreview>(`/artifacts/${id}/github-pr-preview`),
  },

  reviews: {
    list: (status?: string) => get<ApiReview[]>(`/reviews${status ? `?status=${status}` : "?status=PENDING"}`),
    listAll: () =>
      Promise.all([
        get<ApiReview[]>("/reviews?status=PENDING"),
        get<ApiReview[]>("/reviews?status=APPROVED"),
        get<ApiReview[]>("/reviews?status=NEEDS_CHANGES"),
        get<ApiReview[]>("/reviews?status=REJECTED"),
      ]).then((groups) => groups.flat()),
    get: (id: string) => get<ApiReview>(`/reviews/${id}`),
    create: (body: { artifact_id: string; reviewer_id: string }) => post<ApiReview>("/reviews", body),
    approve: (id: string, comment?: string) => post<ApiReview>(`/reviews/${id}/approve`, { comment: comment ?? null }),
    // Structured comments (rule 1), each optionally linked to a section
    // (rule 2) — see ApiReviewCommentInput and ReviewComment.section_title.
    requestChanges: (id: string, comments: ApiReviewCommentInput[]) =>
      post<ApiReview>(`/reviews/${id}/request-changes`, { comments }),
    reject: (id: string, comment: string) => post<ApiReview>(`/reviews/${id}/reject`, { comment }),
    addComment: (id: string, body: { author_id: string; body: string; section_title?: string | null }) =>
      post<ApiReviewComment>(`/reviews/${id}/comments`, body),
    // "Run revision agent" (rule 4) — see app/services/revision_agent.py.
    runRevisionAgent: (id: string, triggeredByUserId: string) =>
      post<ApiRevisionAgentRunResponse>(`/reviews/${id}/run-revision-agent`, { triggered_by_user_id: triggeredByUserId }),
  },

  // Implementation Agent execution (diff/patch preview only) — see
  // app/api/routes/implementation_runs.py.
  implementationRuns: {
    start: (body: { implementation_task_id: string; triggered_by_user_id: string }) =>
      post<ApiImplementationRun>("/implementation-runs", body),
    get: (id: string) => get<ApiImplementationRun>(`/implementation-runs/${id}`),
    review: (id: string, body: { decision: "ACCEPTED" | "REJECTED"; reviewed_by_user_id: string; comment?: string | null }) =>
      post<ApiImplementationRun>(`/implementation-runs/${id}/review`, body),
    // Real GitHub write (branch + commit(s) + PR) — only reachable once
    // the run is ACCEPTED. Never targets the repository's default branch.
    createPullRequest: (id: string, body: { triggered_by_user_id: string; base_branch?: string | null }) =>
      post<ApiImplementationRun>(`/implementation-runs/${id}/create-pull-request`, body),
  },

  // CodeRunnerService — the local-git alternative flow: apply an
  // accepted patch through a real isolated clone, run configured tests,
  // and only on success commit/push a real branch (see
  // app/api/routes/code_runs.py). Story-scoped implementation runs only.
  codeRuns: {
    apply: (body: { implementation_run_id: string; triggered_by_user_id: string; base_branch?: string | null; test_commands?: string[] }) =>
      post<ApiCodeRun>("/code-runs", body),
    get: (id: string) => get<ApiCodeRun>(`/code-runs/${id}`),
    // Only reachable once the CodeRun reached PUSHED — creates a real
    // GitHub PR from the already-pushed branch (no further commits).
    createPullRequest: (id: string, body: { triggered_by_user_id: string; base_branch?: string | null }) =>
      post<ApiPullRequestLink>(`/code-runs/${id}/create-pull-request`, body),
  },

  // Testing Agent system — see app/api/routes/test_runs.py. QA approval
  // happens at the existing /reviews/{id} screen (review_id above), not here.
  testRuns: {
    start: (body: {
      implementation_task_id: string;
      agent_type: ApiTestAgentType;
      triggered_by_user_id: string;
      reviewer_id?: string; // required for a project-level task; ignored for a story-scoped one
      evidence_attachments?: string[];
    }) => post<ApiTestRun>("/test-runs", body),
    get: (id: string) => get<ApiTestRun>(`/test-runs/${id}`),
  },

  // Maintenance Agent — see app/api/routes/maintenance_runs.py. Repeatable,
  // manually-triggered; no approval gate exists for its output (rule:
  // "recommend actions only").
  maintenanceRuns: {
    start: (body: { project_id: string; triggered_by_user_id: string; error_logs?: string | null; user_feedback?: string | null }) =>
      post<ApiMaintenanceRun>("/maintenance-runs", body),
    get: (id: string) => get<ApiMaintenanceRun>(`/maintenance-runs/${id}`),
  },

  // PR Review Agent — see app/api/routes/pr_review_runs.py. Posting
  // comments is the only real GitHub write here, and it sends exactly
  // the (possibly human-edited) text passed in — never re-derives it
  // from suggested_comments server-side.
  prReviewRuns: {
    start: (body: { implementation_task_id: string; triggered_by_user_id: string }) =>
      post<ApiPRReviewRun>("/pr-review-runs", body),
    get: (id: string) => get<ApiPRReviewRun>(`/pr-review-runs/${id}`),
    postComments: (id: string, body: { triggered_by_user_id: string; comments: { file: string; body: string }[] }) =>
      post<ApiPostPRReviewCommentsResponse>(`/pr-review-runs/${id}/post-comments`, body),
  },

  integrations: {
    list: () => get<ApiIntegration[]>("/integrations"),
    connect: (id: string) => post<ApiIntegration>(`/integrations/${id}/connect`),
    disconnect: (id: string) => post<ApiIntegration>(`/integrations/${id}/disconnect`),
  },

  // GitHub integration foundation — read-only repo scan. `access_token`
  // in `connect` is the only place a token ever appears in a request;
  // every response type here is token-free (see ApiIntegrationConnection).
  github: {
    connect: (body: ApiConnectGitHubRequest) => post<ApiIntegrationConnection>("/github/connections", body),
    listConnections: () => get<ApiIntegrationConnection[]>("/github/connections"),
    disconnect: (connectionId: string) => post<ApiIntegrationConnection>(`/github/connections/${connectionId}/disconnect`),
    listRepositoryOptions: (connectionId: string) => get<ApiGitHubRepoSummary[]>(`/github/connections/${connectionId}/repositories`),
    saveRepository: (body: ApiCreateRepositoryRequest) => post<ApiRepository>("/github/repositories", body),
    getRepository: (repositoryId: string) => get<ApiRepository>(`/github/repositories/${repositoryId}`),
    listBranches: (repositoryId: string) => get<string[]>(`/github/repositories/${repositoryId}/branches`),
    getDefaultBranch: (repositoryId: string) => get<string>(`/github/repositories/${repositoryId}/default-branch`),
    getTree: (repositoryId: string, ref?: string) =>
      get<ApiRepositoryTree>(`/github/repositories/${repositoryId}/tree${ref ? `?ref=${encodeURIComponent(ref)}` : ""}`),
    readFile: (repositoryId: string, path: string, ref?: string) => {
      const qs = new URLSearchParams({ path });
      if (ref) qs.set("ref", ref);
      return get<ApiRepositoryFileContent>(`/github/repositories/${repositoryId}/file?${qs.toString()}`);
    },
    createSnapshot: (repositoryId: string, body: ApiCreateSnapshotRequest) =>
      post<ApiRepositorySnapshot>(`/github/repositories/${repositoryId}/snapshots`, body),
    listSnapshots: (repositoryId: string) => get<ApiRepositorySnapshot[]>(`/github/repositories/${repositoryId}/snapshots`),
    listSnapshotFiles: (snapshotId: string) => get<ApiRepositoryFileIndexEntry[]>(`/github/snapshots/${snapshotId}/files`),
  },

  // Real Jira integration — see app/api/routes/jira_integration.py.
  // push() only ever creates exactly what's named in `selections` — there
  // is no "push everything" call, by design (no auto-create rule).
  jira: {
    connect: (body: ApiConnectJiraRequest) => post<ApiJiraConnection>("/jira/connections", body),
    listConnections: () => get<ApiJiraConnection[]>("/jira/connections"),
    disconnect: (connectionId: string) => post<ApiJiraConnection>(`/jira/connections/${connectionId}/disconnect`),
    saveProject: (body: { project_id: string; connection_id: string; jira_project_key: string }) =>
      post<ApiJiraProjectLink>("/jira/projects", body),
    pushPreview: (projectId: string) => get<ApiJiraPushPreview>(`/jira/projects/${projectId}/push-preview`),
    push: (body: { project_id: string; triggered_by_user_id: string; selections: { source_type: ApiJiraSourceType; source_key: string }[] }) =>
      post<ApiJiraPushResponse>("/jira/push", body),
    syncStatus: (projectId: string) => post<ApiJiraSyncStatusResponse>(`/jira/projects/${projectId}/sync-status`),
    // Per-story sync — always preview before sync (see the panel in
    // components/stories/stories-view.tsx). Bulk sync only ever touches
    // the exact `story_ids` passed in — never an implicit "sync all".
    storyPreview: (storyId: string) => get<ApiStoryJiraPreview>(`/jira/stories/${storyId}/preview`),
    bulkPreviewStories: (storyIds: string[]) =>
      post<ApiBulkStoryJiraPreviewResponse>("/jira/stories/bulk-preview", { story_ids: storyIds }),
    syncStory: (storyId: string, body: { triggered_by_user_id: string }) =>
      post<ApiStoryJiraSyncResult>(`/jira/stories/${storyId}/sync`, body),
    bulkSyncStories: (body: { story_ids: string[]; triggered_by_user_id: string }) =>
      post<ApiBulkStoryJiraSyncResponse>("/jira/stories/bulk-sync", body),
  },

  // Real Confluence integration — see app/api/routes/confluence_integration.py.
  // publish() only ever touches exactly the artifact_types named — there
  // is no "publish everything" call, by design (rule: preview before
  // publishing; draft artifacts are always rejected server-side too).
  confluence: {
    connect: (body: ApiConnectConfluenceRequest) => post<ApiConfluenceConnection>("/confluence/connections", body),
    listConnections: () => get<ApiConfluenceConnection[]>("/confluence/connections"),
    disconnect: (connectionId: string) => post<ApiConfluenceConnection>(`/confluence/connections/${connectionId}/disconnect`),
    saveSpace: (body: { project_id: string; connection_id: string; space_key: string }) =>
      post<ApiConfluenceSpaceLink>("/confluence/spaces", body),
    publishPreview: (projectId: string) => get<ApiConfluencePublishPreview>(`/confluence/projects/${projectId}/publish-preview`),
    publish: (body: { project_id: string; triggered_by_user_id: string; artifact_types: string[] }) =>
      post<ApiConfluencePublishResponse>("/confluence/publish", body),
  },

  ops: {
    summary: () => get<ApiOpsSummary>("/ops/summary"),
  },

  agentDefinitions: {
    list: () => get<ApiAgentDefinition[]>("/agents"),
    get: (agentKey: string) => get<ApiAgentDefinition>(`/agents/${agentKey}`),
  },

  validators: {
    list: () => get<ApiValidatorDefinition[]>("/validators"),
    getByStage: (stage: string) => get<ApiValidatorDefinition>(`/validators/${stage}`),
  },

  knowledgeSources: {
    list: (params?: { category?: string; status?: string }) => {
      const qs = new URLSearchParams();
      if (params?.category) qs.set("category", params.category);
      if (params?.status) qs.set("status", params.status);
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return get<ApiKnowledgeSource[]>(`/knowledge-sources${suffix}`);
    },
    get: (id: string) => get<ApiKnowledgeSource>(`/knowledge-sources/${id}`),
    create: (body: { title: string; category: string; source_type: string; file_url?: string; uploaded_by_id: string }) =>
      post<ApiKnowledgeSource>("/knowledge-sources", body),
    upload: (formData: FormData) => postForm<ApiKnowledgeSource>("/knowledge-sources/upload", formData),
    chunks: (id: string) => get<ApiKnowledgeChunk[]>(`/knowledge-sources/${id}/chunks`),
    search: (query: string, params?: { limit?: number; category?: string }) => {
      const qs = new URLSearchParams({ query });
      if (params?.limit) qs.set("limit", String(params.limit));
      if (params?.category) qs.set("category", params.category);
      return get<ApiKnowledgeSearchResult[]>(`/knowledge-sources/search?${qs.toString()}`);
    },
  },

  prompts: {
    list: (params?: { agent_key?: string }) => {
      const qs = params?.agent_key ? `?agent_key=${params.agent_key}` : "";
      return get<ApiAgentPrompt[]>(`/prompts${qs}`);
    },
    getByAgentKey: (agentKey: string, role: string = "draft") => get<ApiAgentPrompt>(`/prompts/${agentKey}?role=${role}`),
    update: (
      id: string,
      body: Partial<{ name: string; stage: string; system_prompt: string; output_format: string; validation_checklist: string[] }> & {
        updated_by_id: string;
      }
    ) => patch<ApiAgentPrompt>(`/prompts/${id}`, body),
    createVersion: (
      id: string,
      body: {
        name: string;
        stage: string;
        system_prompt: string;
        output_format: string;
        validation_checklist: string[];
        created_by_id: string;
      }
    ) => post<ApiAgentPrompt>(`/prompts/${id}/versions`, body),
    activate: (id: string, activatedById: string) =>
      post<ApiAgentPrompt>(`/prompts/${id}/activate`, { activated_by_id: activatedById }),
  },

  agentRuns: {
    get: (id: string) => get<ApiAgentRun>(`/agent-runs/${id}`),
    start: (body: {
      project_id: string;
      workflow_node_id: string;
      action: string;
      triggered_by_user_id: string;
      input_context?: Record<string, unknown>;
    }) => post<ApiAgentRun>("/agent-runs", body),
    saveToArtifact: (id: string) =>
      post<{ agent_run: ApiAgentRun; artifact_id: string; artifact_version_id: string; artifact_status: string; workflow_node_status: string }>(
        `/agent-runs/${id}/save-to-artifact`
      ),
  },

  // Scrum story lanes — see apps/api/app/api/routes/stories.py and
  // workflows/scrum-story-lanes-*.json.
  stories: {
    list: (projectId: string) => get<{ items: ApiStory[]; total: number }>(`/projects/${projectId}/stories`),
    syncFromBacklog: (projectId: string, body: { story_type: "VERTICAL" | "HORIZONTAL"; triggered_by_user_id: string }) =>
      post<{ created: ApiStory[]; already_existed: number }>(`/projects/${projectId}/stories/sync-from-backlog`, body),
    update: (
      storyId: string,
      body: Partial<{
        title: string;
        description: string;
        user_story: string;
        priority: string;
        dependencies: string;
        acceptance_criteria: string[];
        definition_of_done: string[];
        suggested_owner_role: string;
        story_points: number;
        business_value: string;
        technical_areas: string[];
        jira_issue_type: string;
        jira_issue_key: string;
        suggested_subtasks: string[];
        release_readiness_criteria: string[];
      }> & { updated_by_id: string }
    ) => patch<ApiStory>(`/stories/${storyId}`, body),
    // Distinct from update() — also writes a StoryAssignee history row
    // (see app/models/story_assignee.py).
    assign: (storyId: string, ownerUserId: string, assignedById: string) =>
      post<ApiStory>(`/stories/${storyId}/assign`, { owner_user_id: ownerUserId, assigned_by_id: assignedById }),
    createLane: (storyId: string, triggeredByUserId: string) =>
      post<ApiStory>(`/stories/${storyId}/lane`, { triggered_by_user_id: triggeredByUserId }),
    // 404s when no lane has been created yet — callers should catch
    // ApiError with status 404.
    getLane: (storyId: string) => get<ApiStoryDeliveryLane>(`/stories/${storyId}/lane`),
    // 404s when no Story LLD has been drafted yet — callers should catch
    // ApiError with status 404 and treat it as "not drafted yet."
    getLld: (storyId: string) => get<ApiStoryArtifact>(`/stories/${storyId}/lld`),
    // 404s when no Implementation Plan has been drafted yet.
    getImplementationPlan: (storyId: string) => get<ApiStoryArtifact>(`/stories/${storyId}/implementation-plan`),
    // 404s when no Test Scenarios have been drafted yet.
    getTestScenarios: (storyId: string) => get<ApiStoryArtifact>(`/stories/${storyId}/test-scenarios`),
    // 404s until Story LLD/LLD_REVIEW is approved — see
    // app/api/routes/stories.py's _ensure_story_implementation_task.
    getImplementationTask: (storyId: string) => get<ApiImplementationTask>(`/stories/${storyId}/implementation-task`),
    // 404s until a test run has completed for this story — see
    // app/services/testing_agent.STORY_TEST_REPORT_ARTIFACT_TYPE.
    getTestReport: (storyId: string) => get<ApiStoryArtifact>(`/stories/${storyId}/test-report`),
  },

  storyDelivery: {
    listNodes: (laneId: string) => get<ApiStoryDeliveryNode[]>(`/delivery-lanes/${laneId}/nodes`),
    updateNodeStatus: (
      nodeId: string,
      body: { status: string; actor_user_id: string; blocked_reason?: string; assigned_user_id?: string }
    ) => patch<ApiStoryDeliveryNode>(`/delivery-lane-nodes/${nodeId}`, body),
    draftStoryLld: (nodeId: string, triggeredByUserId: string) =>
      post<{ needs_clarification: boolean; story_artifact: ApiStoryArtifact | null; node_status: string }>(
        `/delivery-lane-nodes/${nodeId}/draft-story-lld`,
        { triggered_by_user_id: triggeredByUserId }
      ),
    draftImplementationPlan: (nodeId: string, triggeredByUserId: string) =>
      post<{ needs_clarification: boolean; story_artifact: ApiStoryArtifact | null; node_status: string }>(
        `/delivery-lane-nodes/${nodeId}/draft-implementation-plan`,
        { triggered_by_user_id: triggeredByUserId }
      ),
    draftTestScenarios: (nodeId: string, triggeredByUserId: string) =>
      post<{ needs_clarification: boolean; story_artifact: ApiStoryArtifact | null; node_status: string }>(
        `/delivery-lane-nodes/${nodeId}/draft-test-scenarios`,
        { triggered_by_user_id: triggeredByUserId }
      ),
  },

  sprints: {
    list: (projectId: string) => get<ApiSprint[]>(`/projects/${projectId}/sprints`),
    create: (body: { project_id: string; name: string; goal?: string; start_date?: string; end_date?: string; capacity_points?: number; created_by_id: string }) =>
      post<ApiSprint>("/sprints", body),
    update: (
      sprintId: string,
      body: Partial<{ name: string; goal: string; start_date: string; end_date: string; capacity_points: number }> & { updated_by_id: string }
    ) => patch<ApiSprint>(`/sprints/${sprintId}`, body),
    addStory: (
      sprintId: string,
      body: { story_id: string; planned_points?: number; assigned_owner_id?: string; actor_user_id: string }
    ) => post<ApiSprintStory>(`/sprints/${sprintId}/stories`, body),
    updateStory: (
      sprintId: string,
      storyId: string,
      body: { planned_points?: number; assigned_owner_id?: string; actor_user_id: string }
    ) => patch<ApiSprintStory>(`/sprints/${sprintId}/stories/${storyId}`, body),
    removeStory: (sprintId: string, storyId: string, actorUserId: string) =>
      del<ApiSprintStory>(`/sprints/${sprintId}/stories/${storyId}`, { actor_user_id: actorUserId }),
    start: (sprintId: string, actorUserId: string) => post<ApiSprint>(`/sprints/${sprintId}/start`, { actor_user_id: actorUserId }),
    complete: (sprintId: string, actorUserId: string) => post<ApiSprint>(`/sprints/${sprintId}/complete`, { actor_user_id: actorUserId }),
    board: (sprintId: string) => get<ApiSprintBoard>(`/sprints/${sprintId}/board`),
    generatePlan: (sprintId: string, triggeredByUserId: string) =>
      post<ApiArtifactVersion>(`/sprints/${sprintId}/generate-plan`, { triggered_by_user_id: triggeredByUserId }),
    generateReleasePlan: (sprintId: string, triggeredByUserId: string, reviewerId: string) =>
      post<ApiArtifactVersion>(`/sprints/${sprintId}/generate-release-plan`, {
        triggered_by_user_id: triggeredByUserId,
        reviewer_id: reviewerId,
      }),
  },

  // Release planning from story delivery lanes — see
  // app/api/routes/releases.py. A release only ever holds explicitly
  // added stories (never an implicit "add every ready story").
  releases: {
    list: (projectId: string) => get<ApiRelease[]>(`/projects/${projectId}/releases`),
    releaseReadyStories: (projectId: string) => get<ApiStory[]>(`/projects/${projectId}/release-ready-stories`),
    create: (body: { project_id: string; name: string; version: string; target_date?: string; created_by_id: string }) =>
      post<ApiRelease>("/releases", body),
    update: (
      releaseId: string,
      body: Partial<{ name: string; version: string; target_date: string; release_notes: string }> & { updated_by_id: string }
    ) => patch<ApiRelease>(`/releases/${releaseId}`, body),
    board: (releaseId: string) => get<ApiReleaseBoard>(`/releases/${releaseId}/board`),
    addStory: (releaseId: string, storyId: string, actorUserId: string) =>
      post<ApiReleaseStory>(`/releases/${releaseId}/stories`, { story_id: storyId, actor_user_id: actorUserId }),
    removeStory: (releaseId: string, storyId: string, actorUserId: string) =>
      del<void>(`/releases/${releaseId}/stories/${storyId}`, { actor_user_id: actorUserId }),
    generateNotes: (releaseId: string, triggeredByUserId: string) =>
      post<ApiRelease>(`/releases/${releaseId}/generate-notes`, { triggered_by_user_id: triggeredByUserId }),
    approvalChecklist: (releaseId: string) => get<string[]>(`/releases/${releaseId}/approval-checklist`),
    approve: (releaseId: string, actorUserId: string) => post<ApiRelease>(`/releases/${releaseId}/approve`, { actor_user_id: actorUserId }),
  },
};
