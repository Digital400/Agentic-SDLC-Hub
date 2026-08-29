import { memo } from "react";
import { Handle, Position } from "reactflow";
import { ShieldCheck, User } from "lucide-react";
import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";

import { WORKFLOW_STATUS_ACCENT, WorkflowStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { formatSnakeCase } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface StageNodeData {
  label: string;
  status: WorkflowStatus;
  assignedRole: string;
  outputArtifactType: string;
  requiresHumanApproval: boolean;
  selected: boolean;
}

// Custom React Flow node — a compact card showing everything the spec
// asks for at a glance: stage name, status (color-coded), assigned role,
// output artifact type, and an approval-required badge. Clicking it opens
// the full detail panel (see NodeDetailsPanel) via onNodeClick in
// WorkflowCanvas.
function StageNodeComponent({ data }: { data: StageNodeData }) {
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
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        <WorkflowStatusBadge status={data.status} />
        {data.requiresHumanApproval ? (
          <Badge variant="outline" className="gap-1">
            <ShieldCheck className="h-3 w-3" />
            Approval required
          </Badge>
        ) : null}
      </div>

      <div className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-1">
          <User className="h-3 w-3 shrink-0" />
          <span className="truncate">{data.assignedRole}</span>
        </div>
        <div className="truncate">Output: {formatSnakeCase(data.outputArtifactType)}</div>
      </div>

      <Handle type="source" position={Position.Right} className="!bg-muted-foreground" />
    </div>
  );
}

export const StageNode = memo(StageNodeComponent);
