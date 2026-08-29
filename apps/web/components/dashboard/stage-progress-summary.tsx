import { STAGE_ORDER, getStageLabel } from "@/lib/mock-data";
import type { Project } from "@/lib/types";

// How many active projects currently sit at each SDLC stage, in workflow
// order — answers "where is our work concentrated right now?" at a glance.
export function StageProgressSummary({ projects }: { projects: Project[] }) {
  const activeProjects = projects.filter((p) => p.status === "ACTIVE");
  const maxCount = Math.max(1, ...STAGE_ORDER.map((key) => activeProjects.filter((p) => p.currentStage === key).length));

  return (
    <ul className="flex flex-col gap-2.5 p-4">
      {STAGE_ORDER.map((key) => {
        const count = activeProjects.filter((p) => p.currentStage === key).length;
        return (
          <li key={key} className="flex items-center gap-3 text-sm">
            <span className="w-36 shrink-0 truncate text-muted-foreground">{getStageLabel(key)}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
              {count > 0 ? (
                <div className="h-full rounded-full bg-primary" style={{ width: `${(count / maxCount) * 100}%` }} />
              ) : null}
            </div>
            <span className="w-4 shrink-0 text-right font-medium">{count}</span>
          </li>
        );
      })}
    </ul>
  );
}
