import Link from "next/link";
import { AlertTriangle } from "lucide-react";

import { formatRelativeTime } from "@/lib/format";
import type { OpsBlockedWorkflowRow } from "@/lib/types";

// Blocked workflows — every WorkflowNode currently stuck BLOCKED (see
// apps/api/app/services/graph_engine.py's GraphEngineService.mark_blocked),
// with why and where, since this is the one metric on this dashboard that's
// actually actionable right now, not just informational.
export function OpsBlockedWorkflowsList({ workflows }: { workflows: OpsBlockedWorkflowRow[] }) {
  if (workflows.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Nothing is blocked — every workflow is either running, waiting, or done.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {workflows.map((w) => (
        <li key={`${w.projectId}-${w.nodeKey}`}>
          <Link
            href={`/projects/${w.projectId}/workflow`}
            className="flex items-start gap-2.5 rounded-md border border-border p-3 text-sm transition-colors hover:border-primary/50"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" strokeWidth={1.5} />
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">
                {w.projectName} <span className="font-normal text-muted-foreground">— {w.stageName}</span>
              </p>
              <p className="mt-0.5 truncate text-xs text-muted-foreground" title={w.blockedReason ?? undefined}>
                {w.blockedReason ?? "No reason recorded."}
              </p>
            </div>
            <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
              {formatRelativeTime(w.updatedAt)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
