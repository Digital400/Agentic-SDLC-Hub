import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";
import type { AgentRunStatus, ArtifactStatus, ProjectStatus, ReviewStatus } from "@/lib/types";

// Central place mapping every status enum used across the app to a label +
// Badge color, so a status reads the same way everywhere it appears
// (workflow canvas, tables, workspace headers).

// Gray/Blue/Yellow/Green/Red/Purple — see the Workflow Graph spec. Also
// used for the node accent color in components/workflow/stage-node.tsx,
// which must stay in sync with this mapping.
const WORKFLOW_STATUS_VARIANT: Record<WorkflowStatus, BadgeProps["variant"]> = {
  NOT_STARTED: "gray",
  IN_PROGRESS: "info", // blue
  WAITING_FOR_REVIEW: "warning", // yellow
  APPROVED: "success", // green
  NEEDS_CHANGES: "warning", // yellow — not explicitly specced, grouped with "needs attention"
  REJECTED: "destructive", // red
  BLOCKED: "purple",
  COMPLETED: "success", // green
  FAILED: "destructive", // red
};

const WORKFLOW_STATUS_LABEL: Record<WorkflowStatus, string> = {
  NOT_STARTED: "Not started",
  IN_PROGRESS: "In progress",
  WAITING_FOR_REVIEW: "Waiting for review",
  APPROVED: "Approved",
  NEEDS_CHANGES: "Needs changes",
  REJECTED: "Rejected",
  BLOCKED: "Blocked",
  COMPLETED: "Completed",
  FAILED: "Failed",
};

// Tailwind border-color classes for the same six-color mapping, for
// non-Badge uses (e.g. a workflow node's colored left accent).
export const WORKFLOW_STATUS_ACCENT: Record<WorkflowStatus, string> = {
  NOT_STARTED: "border-l-gray-400",
  IN_PROGRESS: "border-l-blue-500",
  WAITING_FOR_REVIEW: "border-l-amber-500",
  APPROVED: "border-l-emerald-500",
  NEEDS_CHANGES: "border-l-amber-500",
  REJECTED: "border-l-red-500",
  BLOCKED: "border-l-purple-500",
  COMPLETED: "border-l-emerald-500",
  FAILED: "border-l-red-500",
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
