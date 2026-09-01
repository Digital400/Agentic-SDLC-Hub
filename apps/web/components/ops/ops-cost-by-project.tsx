import { formatCost } from "@/lib/format";
import type { OpsCostByProject } from "@/lib/types";

// Cost by project — sorted highest-spend first (see
// apps/api/app/services/ops_metrics.py), each row's bar scaled relative to
// the biggest spender so the relative split is visible at a glance, not
// just the raw numbers.
export function OpsCostByProjectList({ projects }: { projects: OpsCostByProject[] }) {
  if (projects.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No agent run costs recorded yet.</p>;
  }

  const maxCost = Math.max(...projects.map((p) => p.totalCost), 0.000001);

  return (
    <ul className="flex flex-col gap-2.5">
      {projects.map((p) => (
        <li key={p.projectId} className="flex items-center gap-3 text-sm">
          <span className="w-40 shrink-0 truncate" title={p.projectName}>
            {p.projectName}
          </span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary" style={{ width: `${(p.totalCost / maxCost) * 100}%` }} />
          </div>
          <span className="w-14 shrink-0 text-right text-xs text-muted-foreground">{p.runCount} runs</span>
          <span className="w-16 shrink-0 text-right text-xs font-medium">{formatCost(p.totalCost)}</span>
        </li>
      ))}
    </ul>
  );
}
