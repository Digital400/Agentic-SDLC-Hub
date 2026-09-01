import { formatCost, formatDuration, formatPercent } from "@/lib/format";
import type { OpsStagePerformance } from "@/lib/types";

// Stage performance — per-stage run volume, success rate, cost, average
// quality score, and average loop iterations, aggregated across every
// project (see apps/api/app/services/ops_metrics.py). A real table, not a
// bar list, so a tech lead can scan every dimension for a stage at once.
// A stage with zero runs still appears, with its metrics rendered as "—"
// rather than 0%, so a new stage isn't misread as a low-performing one.
export function OpsStagePerformanceTable({ stages }: { stages: OpsStagePerformance[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-left text-xs text-muted-foreground">
          <th className="pb-2 pr-3 font-medium">Stage</th>
          <th className="pb-2 pr-3 font-medium">Runs</th>
          <th className="pb-2 pr-3 font-medium">Success</th>
          <th className="pb-2 pr-3 font-medium">Avg. quality</th>
          <th className="pb-2 pr-3 font-medium">Avg. iterations</th>
          <th className="pb-2 pr-3 font-medium">Avg. duration</th>
          <th className="pb-2 font-medium text-right">Cost</th>
        </tr>
      </thead>
      <tbody>
        {stages.map((stage) => (
          <tr key={stage.nodeKey} className="border-b border-border/60 last:border-0">
            <td className="max-w-[160px] truncate py-2 pr-3 font-medium">{stage.stageName}</td>
            <td className="py-2 pr-3 text-muted-foreground">{stage.totalRuns}</td>
            <td className="py-2 pr-3">
              <span className={stage.successRate !== null && stage.successRate < 0.5 ? "text-destructive" : ""}>
                {formatPercent(stage.successRate)}
              </span>
            </td>
            <td className="py-2 pr-3 text-muted-foreground">{formatPercent(stage.avgQualityScore)}</td>
            <td className="py-2 pr-3 text-muted-foreground">
              {stage.avgIterations === null ? "—" : stage.avgIterations.toFixed(1)}
            </td>
            <td className="py-2 pr-3 text-muted-foreground">{formatDuration(stage.avgDurationSeconds)}</td>
            <td className="py-2 text-right text-muted-foreground">{formatCost(stage.totalCost)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
