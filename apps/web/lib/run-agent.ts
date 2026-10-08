/**
 * Shared "run an agent and apply its output" flow — used by both entry
 * points that can trigger a real agent run: the Workflow graph's node
 * detail panel (for a stage with no artifact yet) and the document
 * editor's Agent Actions panel (for a stage that already has one).
 *
 * Deliberately does both API calls (start the run, then save its output
 * to an artifact) as one action from the UI's point of view — the backend
 * keeps them as two separate steps for safety (see
 * apps/api/app/api/routes/agent_runs.py's module docstring), but a user
 * clicking "Run Agent" expects one outcome, not a two-step wizard.
 */

import { api, type ApiAgentRun, type ApiProviderOverride } from "@/lib/api";

export interface RunAgentParams {
  projectId: string;
  workflowNodeId: string;
  action: "draft" | "improve" | "validate";
  triggeredByUserId: string;
  inputContext?: Record<string, unknown>;
  /** Force this one run to use a specific LLM backend — see ApiProviderOverride. Omit for the project default. */
  providerOverride?: ApiProviderOverride;
  /** Force a specific model within providerOverride's provider (e.g. "claude-opus-5"). Ignored unless providerOverride is also set. */
  modelOverride?: string;
}

export interface RunAgentResult {
  run: ApiAgentRun;
  /** Present only when the run COMPLETED and its output was saved. */
  saved: {
    artifactId: string;
    artifactVersionId: string;
    artifactStatus: string;
    workflowNodeStatus: string;
  } | null;
}

export async function runAgentAndApply(params: RunAgentParams): Promise<RunAgentResult> {
  const run = await api.agentRuns.start({
    project_id: params.projectId,
    workflow_node_id: params.workflowNodeId,
    action: params.action,
    triggered_by_user_id: params.triggeredByUserId,
    input_context: params.inputContext ?? {},
    provider_override: params.providerOverride,
    model_override: params.providerOverride ? params.modelOverride || undefined : undefined,
  });

  if (run.status !== "COMPLETED") {
    // A blocked/failed run is a real, expected outcome (e.g. a required
    // upstream artifact isn't approved yet) — see run.error_message.
    return { run, saved: null };
  }

  if (params.action === "validate") {
    // Validation judges the current draft; it produces no new content to save.
    // The server has already moved the artifact/node as appropriate.
    const artifactId = run.output_artifact_id;
    if (artifactId === null) return { run, saved: null };
    const artifact = await api.artifacts.get(artifactId);
    const nodeStatus =
      artifact.status === "READY_FOR_REVIEW" ? "WAITING_FOR_REVIEW" : artifact.status === "APPROVED" ? "COMPLETED" : "READY";
    return {
      run,
      saved: {
        artifactId,
        artifactVersionId: artifact.current_version_id ?? "",
        artifactStatus: artifact.status,
        workflowNodeStatus: nodeStatus,
      },
    };
  }

  const result = await api.agentRuns.saveToArtifact(run.id);
  return {
    run: result.agent_run,
    saved: {
      artifactId: result.artifact_id,
      artifactVersionId: result.artifact_version_id,
      artifactStatus: result.artifact_status,
      workflowNodeStatus: result.workflow_node_status,
    },
  };
}
