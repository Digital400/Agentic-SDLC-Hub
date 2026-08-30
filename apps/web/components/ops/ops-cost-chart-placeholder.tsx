import { BarChart3 } from "lucide-react";

// 4. Cost chart placeholder — explicitly not a real chart yet. Trend data
// (cost per day/week) isn't computed by the backend today; this shows the
// one real total it does have and marks the rest as roadmap, the same
// convention used for other not-yet-built visuals in this app (e.g. the
// dashboard's "Average time per stage" card).
export function OpsCostChartPlaceholder({ totalCost }: { totalCost: number }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border py-10 text-center">
      <BarChart3 className="h-8 w-8 text-muted-foreground/50" strokeWidth={1.5} />
      <p className="text-sm font-medium">${totalCost.toFixed(4)} spent to date</p>
      <p className="max-w-xs text-xs text-muted-foreground">
        Cost-over-time charting isn&apos;t built yet — this needs per-day cost aggregation, which the backend doesn&apos;t
        compute today. This card shows the real running total in the meantime.
      </p>
    </div>
  );
}
