/**
 * Typed client for the FastAPI backend (apps/api). Functions here return
 * the backend's own response shapes (snake_case, matching apps/api/app/schemas/*)
 * — see lib/mappers.ts for converting those into this app's UI types
 * (lib/types.ts, camelCase).
 *
 * Works from both server components (page data fetching) and client
 * components (form submits, decisions) — see .env.example.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

export interface ApiUser {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
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

export interface ApiAgentDefinition {
  id: string;
  agent_key: string;
  name: string;
  description: string | null;
  model_name: string;
  is_active: boolean;
  total_runs: number;
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
    update: (id: string, body: Partial<{ name: string; business_owner: string; description: string; current_stage: string }>) =>
      patch<ApiProject>(`/projects/${id}`, body),
    archive: (id: string) => post<ApiProject>(`/projects/${id}/archive`),
    workflowNodes: (id: string) => get<ApiWorkflowNode[]>(`/projects/${id}/workflow-nodes`),
    workflowEdges: (id: string) => get<ApiWorkflowEdge[]>(`/projects/${id}/workflow-edges`),
    updateNodeStatus: (projectId: string, nodeId: string, status: string) =>
      patch<ApiWorkflowNode>(`/projects/${projectId}/workflow-nodes/${nodeId}`, { status }),
    artifacts: (id: string) => get<ApiArtifact[]>(`/projects/${id}/artifacts`),
    agentRuns: (id: string) => get<ApiAgentRun[]>(`/projects/${id}/agent-runs`),
  },

  artifacts: {
    get: (id: string) => get<ApiArtifact>(`/artifacts/${id}`),
    create: (body: { project_id: string; workflow_node_id: string; artifact_type: string; title: string; created_by_id: string }) =>
      post<ApiArtifact>("/artifacts", body),
    versions: (id: string) => get<ApiArtifactVersion[]>(`/artifacts/${id}/versions`),
    createVersion: (id: string, body: { content_markdown: string; change_summary?: string; created_by_id: string }) =>
      post<ApiArtifactVersion>(`/artifacts/${id}/versions`, body),
    updateContent: (id: string, body: { content_markdown: string; change_summary?: string }) =>
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

  agentDefinitions: {
    list: () => get<ApiAgentDefinition[]>("/agents"),
    get: (agentKey: string) => get<ApiAgentDefinition>(`/agents/${agentKey}`),
  },

  prompts: {
    list: (params?: { agent_key?: string }) => {
      const qs = params?.agent_key ? `?agent_key=${params.agent_key}` : "";
      return get<ApiAgentPrompt[]>(`/prompts${qs}`);
    },
    getByAgentKey: (agentKey: string, role: string = "draft") => get<ApiAgentPrompt>(`/prompts/${agentKey}?role=${role}`),
    update: (id: string, body: Partial<{ name: string; stage: string; system_prompt: string; output_format: string; validation_checklist: string[] }>) =>
      patch<ApiAgentPrompt>(`/prompts/${id}`, body),
    createVersion: (
      id: string,
      body: { name: string; stage: string; system_prompt: string; output_format: string; validation_checklist: string[] }
    ) => post<ApiAgentPrompt>(`/prompts/${id}/versions`, body),
    activate: (id: string) => post<ApiAgentPrompt>(`/prompts/${id}/activate`),
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
