/**
 * Shared workflow types.
 *
 * These mirror the shape of workflow template files such as
 * `workflows/sdlc-workflow.json` and are the contract both the API
 * (workflow engine, persistence) and the web app (React Flow canvas)
 * build against. Keep this file framework-agnostic — no React Flow or
 * FastAPI/Pydantic imports here.
 */

/**
 * Runtime status of a single workflow node instance for a given project.
 * This is project/instance state, not part of the static workflow
 * template — a template node has no status of its own until a project
 * is running through it.
 */
export type WorkflowStatus =
  | "NOT_STARTED"
  | "IN_PROGRESS"
  | "WAITING_FOR_REVIEW"
  | "APPROVED"
  | "NEEDS_CHANGES"
  | "REJECTED"
  | "BLOCKED"
  | "COMPLETED"
  | "FAILED";

export const WORKFLOW_STATUSES: readonly WorkflowStatus[] = [
  "NOT_STARTED",
  "IN_PROGRESS",
  "WAITING_FOR_REVIEW",
  "APPROVED",
  "NEEDS_CHANGES",
  "REJECTED",
  "BLOCKED",
  "COMPLETED",
  "FAILED",
] as const;

/**
 * Actions that can be taken against a node instance. Which of these are
 * valid for a given node is declared by that node's `allowedActions`.
 */
export type WorkflowAction =
  | "draft"
  | "edit"
  | "submit_for_review"
  | "approve"
  | "reject"
  | "request_changes"
  | "complete";

/**
 * A single stage in an SDLC workflow template.
 */
export interface WorkflowNode {
  /** Stable, unique identifier for this stage within the template (e.g. "hld"). */
  id: string;
  /** React Flow node type. Currently always "stage". */
  type: "stage";
  /** Human-readable stage name (e.g. "High-Level Design"). */
  name: string;
  /** What this stage is for and what it produces. */
  description: string;
  /**
   * Identifier of the agent configuration responsible for drafting/
   * assisting this stage (e.g. "hld-agent"). Resolved against agent
   * prompt templates in `packages/prompts` once the agent engine exists.
   */
  agentKey: string;
  /**
   * Artifact types (or raw inputs) this stage needs before it can start,
   * typically the `outputArtifactType` of one or more upstream stages.
   */
  requiredInputs: string[];
  /** Artifact type this stage produces. */
  outputArtifactType: string;
  /** Whether a human must explicitly approve this stage's artifact before the project can advance. */
  requiresHumanApproval: boolean;
  /** Actions permitted against this node's artifact/status. */
  allowedActions: WorkflowAction[];
  /**
   * IDs of stages that may follow this one. More than one entry means a
   * branch (e.g. forward progress vs. a rework loop back to an earlier
   * stage). An empty array marks a terminal stage.
   */
  nextNodes: string[];
  /** Canvas position, used by the React Flow rendering of the workflow graph. */
  position: { x: number; y: number };
}

/**
 * A transition between two stages in a workflow template, in the shape
 * React Flow expects for edges.
 */
export interface WorkflowEdge {
  /** Stable, unique identifier for this edge. */
  id: string;
  /** Source node id. */
  source: string;
  /** Target node id. */
  target: string;
  /** Optional label, e.g. "rework" for a backward/loop transition. */
  label?: string;
}

/**
 * A full workflow template, as found in `workflows/*.json`.
 */
export interface WorkflowTemplate {
  id: string;
  name: string;
  version: string;
  description: string;
  /** ID of the node a new project instance starts at. */
  startNode: string;
  /** All statuses a node instance can be in. Documented here for reference; see `WorkflowStatus`. */
  statuses: WorkflowStatus[];
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}
