import { memo } from "react";
import { Handle, Position } from "reactflow";
import { AlertTriangle, Gauge, ShieldCheck, User } from "lucide-react";
import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";

import { WORKFLOW_STATUS_ACCENT, WorkflowStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { formatCost, formatSnakeCase } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface StageNodeData {
  label: string;
  status: WorkflowStatus;
  assignedRole: string;
  outputArtifactType: string;
  requiresHumanApproval: boolean;
  /** Loop Engine's latest quality score (0-1) from this node's last DRAFT
   * run — null until one has gone through validation. */
  qualityScore: number | null;
  /** Cost of this node's last agent run, for a quick per-node cost read. */
  lastRunCost: number | null;
  /** Set only when status is BLOCKED — shown as a tooltip on the indicator. */
  blockedReason: string | null;
  selected: boolean;
}

// Custom React Flow node — a compact card showing everything the spec
// asks for at a glance: stage name, status (color-coded), quality score,
// approval badge, blocked indicator, token cost mini label, assigned role,
// and output artifact type. Clicking it opens the full detail panel (see
// NodeDetailsPanel) via onNodeClick in WorkflowCanvas.
function StageNodeComponent({ data }: { data: StageNodeData }) {
  const isBlocked = data.status === "BLOCKED";

  return (
    <div
      className={cn(
        "w-[210px] rounded-md border border-l-4 bg-card px-3 py-2 shadow-sm transition-shadow cursor-pointer hover:shadow-md",
        WORKFLOW_STATUS_ACCENT[data.status],
        data.selected ? "ring-2 ring-primary" : "border-border"
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground" />

      <div className="flex items-start justify-between gap-1">
        <span className="text-xs font-semibold leading-tight">{data.label}</span>
        {isBlocked ? (
          <AlertTriangle
            className="h-3.5 w-3.5 shrink-0 text-purple-600 dark:text-purple-400"
            aria-label={data.blockedReason ? `Blocked: ${data.blockedReason}` : "Blocked"}
          >
            <title>{data.blockedReason ? `Blocked: ${data.blockedReason}` : "Blocked"}</title>
          </AlertTriangle>
        ) : null}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        <WorkflowStatusBadge status={data.status} />
        {data.requiresHumanApproval ? (
          <Badge variant="outline" className="gap-1">
            <ShieldCheck className="h-3 w-3" />
            Approval
          </Badge>
        ) : null}
        {data.qualityScore !== null ? (
          <Badge variant={data.qualityScore >= 0.7 ? "success" : "warning"} className="gap-1">
            <Gauge className="h-3 w-3" />
            {Math.round(data.qualityScore * 100)}%
          </Badge>
        ) : null}
      </div>

      <div className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-1">
          <User className="h-3 w-3 shrink-0" />
          <span className="truncate">{data.assignedRole}</span>
        </div>
        <div className="flex items-center justify-between gap-1">
          <span className="truncate">Output: {formatSnakeCase(data.outputArtifactType)}</span>
          {data.lastRunCost !== null ? (
            <span className="shrink-0 rounded bg-muted px-1 py-0.5 font-medium">{formatCost(data.lastRunCost)}</span>
          ) : null}
        </div>
      </div>

      <Handle type="source" position={Position.Right} className="!bg-muted-foreground" />
    </div>
  );
}

export const StageNode = memo(StageNodeComponent);
