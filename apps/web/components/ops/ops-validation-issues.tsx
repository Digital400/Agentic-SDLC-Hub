import type { OpsValidationIssueFrequency } from "@/lib/types";

// Most common validation issues — counted across every VALIDATE step of
// every loop iteration, not just each run's final verdict (see
// apps/api/app/services/ops_metrics.py), so a recurring-but-eventually-
// fixed issue still shows up as recurring. This is what tells a tech lead
// which prompts/checklists need work.
export function OpsValidationIssuesList({ issues }: { issues: OpsValidationIssueFrequency[] }) {
  if (issues.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No validation issues recorded yet.</p>;
  }

  return (
    <ol className="flex flex-col gap-2">
      {issues.map((issue, i) => (
        <li key={issue.message} className="flex items-start gap-2.5 text-sm">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
            {i + 1}
          </span>
          <span className="flex-1">{issue.message}</span>
          <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">
            {issue.count}×
          </span>
        </li>
      ))}
    </ol>
  );
}
