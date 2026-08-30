import { formatDuration, formatPercent } from "@/lib/format";
import type { OpsStagePerformance } from "@/lib/types";

// 5. Stage performance summary — per-stage run volume, success rate, and
// average duration, aggregated across every project (see
// apps/api/app/services/ops_metrics.py). A stage with zero runs still
// appears, with its metrics rendered as "—" rather than 0%, so a new stage
// isn't misread as a low-performing one.
export function OpsStagePerformanceTable({ stages }: { stages: OpsStagePerformance[] }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {stages.map((stage) => (
        <li key={stage.nodeKey} className="flex items-center gap-3 text-sm">
          <span className="w-40 shrink-0 truncate">{stage.stageName}</span>
          <span className="w-16 shrink-0 text-xs text-muted-foreground">{stage.totalRuns} runs</span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            {stage.successRate !== null ? (
              <div
                className={stage.successRate < 0.5 ? "h-full bg-destructive" : "h-full bg-emerald-500"}
                style={{ width: `${stage.successRate * 100}%` }}
              />
            ) : null}
          </div>
          <span className="w-10 shrink-0 text-right text-xs text-muted-foreground">
            {formatPercent(stage.successRate)}
          </span>
          <span className="w-16 shrink-0 text-right text-xs text-muted-foreground">
            {formatDuration(stage.avgDurationSeconds)}
          </span>
        </li>
      ))}
    </ul>
  );
}
