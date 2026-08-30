import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";
import type {
  AgentRunStatus,
  ArtifactStatus,
  IntegrationStatus,
  KnowledgeSourceStatus,
  ProjectStatus,
  ReviewStatus,
} from "@/lib/types";

// Central place mapping every status enum used across the app to a label +
// Badge color, so a status reads the same way everywhere it appears
// (workflow canvas, tables, workspace headers).

// Gray/Blue/Yellow/Green/Red/Purple — see the Workflow Graph spec. Also
// used for the node accent color in components/workflow/stage-node.tsx,
// which must stay in sync with this mapping.
//
// Matches apps/api/app/models/enums.py's WorkflowStatus — the graph-engine
// rework (see app/services/graph_engine.py) replaced the old
// NOT_STARTED/IN_PROGRESS/REJECTED trio with this larger, more specific
// set; colors below reuse the same six-color families rather than
// introducing new ones.
const WORKFLOW_STATUS_VARIANT: Record<WorkflowStatus, BadgeProps["variant"]> = {
  LOCKED: "gray", // prerequisites not satisfied yet — was NOT_STARTED
  READY: "info", // blue — unlocked, not yet run
  RUNNING: "info", // blue — an agent run is in flight
  WAITING_FOR_INPUT: "warning", // yellow — agent asked a clarifying question
  WAITING_FOR_REVIEW: "warning", // yellow
  APPROVED: "success", // green
  NEEDS_CHANGES: "warning", // yellow — not explicitly specced, grouped with "needs attention"
  BLOCKED: "purple", // was REJECTED's color; a rejected review now lands here
  FAILED: "destructive", // red
  SKIPPED: "gray",
  COMPLETED: "success", // green
};

const WORKFLOW_STATUS_LABEL: Record<WorkflowStatus, string> = {
  LOCKED: "Locked",
  READY: "Ready",
  RUNNING: "Running",
  WAITING_FOR_INPUT: "Waiting for input",
  WAITING_FOR_REVIEW: "Waiting for review",
  APPROVED: "Approved",
  NEEDS_CHANGES: "Needs changes",
  BLOCKED: "Blocked",
  FAILED: "Failed",
  SKIPPED: "Skipped",
  COMPLETED: "Completed",
};

// Tailwind border-color classes for the same color mapping, for non-Badge
// uses (e.g. a workflow node's colored left accent).
export const WORKFLOW_STATUS_ACCENT: Record<WorkflowStatus, string> = {
  LOCKED: "border-l-gray-400",
  READY: "border-l-blue-500",
  RUNNING: "border-l-blue-500",
  WAITING_FOR_INPUT: "border-l-amber-500",
  WAITING_FOR_REVIEW: "border-l-amber-500",
  APPROVED: "border-l-emerald-500",
  NEEDS_CHANGES: "border-l-amber-500",
  BLOCKED: "border-l-purple-500",
  FAILED: "border-l-red-500",
  SKIPPED: "border-l-gray-400",
  COMPLETED: "border-l-emerald-500",
};

export function WorkflowStatusBadge({ status, className }: { status: WorkflowStatus; className?: string }) {
  return (
    <Badge variant={WORKFLOW_STATUS_VARIANT[status]} className={className}>
      {WORKFLOW_STATUS_LABEL[status]}
    </Badge>
  );
}

const PROJECT_STATUS_VARIANT: Record<ProjectStatus, BadgeProps["variant"]> = {
  ACTIVE: "info",
  COMPLETED: "success",
  ARCHIVED: "outline",
};

export function ProjectStatusBadge({ status, className }: { status: ProjectStatus; className?: string }) {
  return (
    <Badge variant={PROJECT_STATUS_VARIANT[status]} className={className}>
      {status.charAt(0) + status.slice(1).toLowerCase()}
    </Badge>
  );
}

const REVIEW_STATUS_VARIANT: Record<ReviewStatus, BadgeProps["variant"]> = {
  PENDING: "warning",
  APPROVED: "success",
  NEEDS_CHANGES: "warning",
  REJECTED: "destructive",
};

const REVIEW_STATUS_LABEL: Record<ReviewStatus, string> = {
  PENDING: "Pending",
  APPROVED: "Approved",
  NEEDS_CHANGES: "Needs changes",
  REJECTED: "Rejected",
};

export function ReviewStatusBadge({ status, className }: { status: ReviewStatus; className?: string }) {
  return (
    <Badge variant={REVIEW_STATUS_VARIANT[status]} className={className}>
      {REVIEW_STATUS_LABEL[status]}
    </Badge>
  );
}

// Matches apps/api/app/models/enums.py's ArtifactStatus.
const ARTIFACT_STATUS_VARIANT: Record<ArtifactStatus, BadgeProps["variant"]> = {
  DRAFT: "gray",
  READY_FOR_REVIEW: "warning",
  APPROVED: "success",
  NEEDS_CHANGES: "warning",
  REJECTED: "destructive",
};

const ARTIFACT_STATUS_LABEL: Record<ArtifactStatus, string> = {
  DRAFT: "Draft",
  READY_FOR_REVIEW: "Ready for review",
  APPROVED: "Approved",
  NEEDS_CHANGES: "Needs changes",
  REJECTED: "Rejected",
};

export function ArtifactStatusBadge({ status, className }: { status: ArtifactStatus; className?: string }) {
  return (
    <Badge variant={ARTIFACT_STATUS_VARIANT[status]} className={className}>
      {ARTIFACT_STATUS_LABEL[status]}
    </Badge>
  );
}

const AGENT_RUN_STATUS_VARIANT: Record<AgentRunStatus, BadgeProps["variant"]> = {
  PENDING: "outline",
  RUNNING: "info",
  COMPLETED: "success",
  FAILED: "destructive",
};

export function AgentRunStatusBadge({ status, className }: { status: AgentRunStatus; className?: string }) {
  return (
    <Badge variant={AGENT_RUN_STATUS_VARIANT[status]} className={className}>
      {status.charAt(0) + status.slice(1).toLowerCase()}
    </Badge>
  );
}

// Matches apps/api/app/models/enums.py's KnowledgeSourceStatus. No
// ingestion pipeline exists yet — see that file's docstring — so this is
// just a lifecycle label, not evidence anything actually ran.
const KNOWLEDGE_SOURCE_STATUS_VARIANT: Record<KnowledgeSourceStatus, BadgeProps["variant"]> = {
  PENDING: "gray",
  PROCESSING: "info",
  INDEXED: "success",
  FAILED: "destructive",
};

const KNOWLEDGE_SOURCE_STATUS_LABEL: Record<KnowledgeSourceStatus, string> = {
  PENDING: "Pending",
  PROCESSING: "Processing",
  INDEXED: "Indexed",
  FAILED: "Failed",
};

export function KnowledgeSourceStatusBadge({ status, className }: { status: KnowledgeSourceStatus; className?: string }) {
  return (
    <Badge variant={KNOWLEDGE_SOURCE_STATUS_VARIANT[status]} className={className}>
      {KNOWLEDGE_SOURCE_STATUS_LABEL[status]}
    </Badge>
  );
}

// Matches apps/api/app/models/enums.py's IntegrationStatus. Every
// integration is realistically NOT_CONNECTED today — no MCP tool is wired
// up yet, see docs/architecture.md.
const INTEGRATION_STATUS_VARIANT: Record<IntegrationStatus, BadgeProps["variant"]> = {
  NOT_CONNECTED: "gray",
  CONNECTED: "success",
  ERROR: "destructive",
};

const INTEGRATION_STATUS_LABEL: Record<IntegrationStatus, string> = {
  NOT_CONNECTED: "Not connected",
  CONNECTED: "Connected",
  ERROR: "Error",
};

export function IntegrationStatusBadge({ status, className }: { status: IntegrationStatus; className?: string }) {
  return (
    <Badge variant={INTEGRATION_STATUS_VARIANT[status]} className={className}>
      {INTEGRATION_STATUS_LABEL[status]}
    </Badge>
  );
}
