import { Clock } from "lucide-react";

// Real cycle-time metrics need per-node timestamp history the platform
// doesn't record yet (see docs/mvp-plan.md — ops dashboards are future
// scope). This is an honest placeholder, not invented numbers: it shows
// the shape of the future widget without claiming real data.
export function AverageStageTime() {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-8 text-center">
      <Clock className="h-6 w-6 text-muted-foreground/50" strokeWidth={1.5} />
      <p className="text-sm font-medium text-muted-foreground">Not tracked yet</p>
      <p className="max-w-[220px] text-xs text-muted-foreground">
        Average time per stage needs stage-history tracking, which isn&rsquo;t built yet.
      </p>
    </div>
  );
}
