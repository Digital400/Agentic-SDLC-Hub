import type { OpsRagSourceUsage } from "@/lib/types";

// Most used RAG sources — which Knowledge Base sources agent runs
// actually cited (see AgentRun.retrieved_sources / apps/api/app/services
// /ops_metrics.py), so it's visible which sources are pulling their
// weight vs. sitting unused.
export function OpsRagSourcesList({ sources }: { sources: OpsRagSourceUsage[] }) {
  if (sources.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No Knowledge Base sources cited yet.</p>;
  }

  return (
    <ol className="flex flex-col gap-2">
      {sources.map((source, i) => (
        <li key={source.sourceTitle} className="flex items-start gap-2.5 text-sm">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
            {i + 1}
          </span>
          <span className="flex-1 truncate" title={source.sourceTitle}>
            {source.sourceTitle}
          </span>
          <span className="shrink-0 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-950 dark:text-blue-300">
            {source.count}×
          </span>
        </li>
      ))}
    </ol>
  );
}
