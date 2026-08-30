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
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
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

export interface ApiReviewComment {
  id: string;
  review_id: string;
  author_id: string;
  body: string;
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

export interface ApiStoryJiraPreview {
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
  stories: ApiStoryJiraPreview[];
  push_to_jira_enabled: boolean;
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
}

export interface ApiOpsSummary {
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  avg_duration_seconds: number | null;
  total_tokens: number;
  total_cost: number;
  approval_rate: number | null;
  rejection_rate: number | null;
  decided_review_count: number;
  human_change_rate: null;
  human_change_rate_note: string;
  blocked_workflow_count: number;
  recent_runs: ApiOpsAgentRunRow[];
  recent_failures: ApiOpsAgentRunRow[];
  stage_performance: ApiOpsStagePerformance[];
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
}

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
    workflowNodes: (id: string) => get<ApiWorkflowNode[]>(`/projects/${id}/workflow-nodes`),
    workflowEdges: (id: string) => get<ApiWorkflowEdge[]>(`/projects/${id}/workflow-edges`),
    updateNodeStatus: (projectId: string, nodeId: string, status: string) =>
      patch<ApiWorkflowNode>(`/projects/${projectId}/workflow-nodes/${nodeId}`, { status }),
    artifacts: (id: string) => get<ApiArtifact[]>(`/projects/${id}/artifacts`),
    agentRuns: (id: string) => get<ApiAgentRun[]>(`/projects/${id}/agent-runs`),
    previewJiraExport: (id: string) => post<ApiJiraExportPreview>(`/projects/${id}/stories/preview-jira-export`),
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
    requestChanges: (id: string, comment: string) => post<ApiReview>(`/reviews/${id}/request-changes`, { comment }),
    reject: (id: string, comment: string) => post<ApiReview>(`/reviews/${id}/reject`, { comment }),
    addComment: (id: string, body: { author_id: string; body: string }) =>
      post<ApiReviewComment>(`/reviews/${id}/comments`, body),
  },

  integrations: {
    list: () => get<ApiIntegration[]>("/integrations"),
    connect: (id: string) => post<ApiIntegration>(`/integrations/${id}/connect`),
    disconnect: (id: string) => post<ApiIntegration>(`/integrations/${id}/disconnect`),
  },

  ops: {
    summary: () => get<ApiOpsSummary>("/ops/summary"),
  },

  agentDefinitions: {
    list: () => get<ApiAgentDefinition[]>("/agents"),
    get: (agentKey: string) => get<ApiAgentDefinition>(`/agents/${agentKey}`),
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
};
