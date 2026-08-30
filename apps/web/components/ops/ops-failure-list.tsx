import Link from "next/link";
import { CheckCircle2, XCircle } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { formatRelativeTime } from "@/lib/format";
import type { OpsAgentRunRow } from "@/lib/types";

// 3. Failure list — recent FAILED runs with their error message, so a tech
// lead can see what's actually breaking without opening every run.
export function OpsFailureList({ failures }: { failures: OpsAgentRunRow[] }) {
  if (failures.length === 0) {
    return (
      <EmptyState
        icon={CheckCircle2}
        title="No recent failures"
        description="Every recent agent run completed successfully."
        className="border-none py-8"
      />
    );
  }

  return (
    <ul className="flex flex-col divide-y divide-border">
      {failures.map((run) => (
        <li key={run.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-2">
              <Link href={`/agent-runs/${run.id}`} className="text-sm font-medium hover:underline">
                {run.agentKey} · {run.action}
              </Link>
              <span className="text-xs text-muted-foreground">{formatRelativeTime(run.createdAt)}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              {run.projectName} · {run.workflowStageName}
            </p>
            {run.errorMessage ? <p className="mt-1 text-xs text-destructive">{run.errorMessage}</p> : null}
          </div>
        </li>
      ))}
    </ul>
  );
}
