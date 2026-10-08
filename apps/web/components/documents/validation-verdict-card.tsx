import { CheckCircle2, TriangleAlert } from "lucide-react";

import type { ValidationVerdict } from "@/lib/validation-verdict";

const pct = (n: number) => `${Math.round(n * 100)}%`;

export function ValidationVerdictCard({ verdict }: { verdict: ValidationVerdict }) {
  const Icon = verdict.passed ? CheckCircle2 : TriangleAlert;
  const groups: [string, string[]][] = [
    ["Critical issues", verdict.criticalIssues],
    ["Missing details", verdict.missingDetails],
    ["Suggestions", verdict.suggestions],
  ];
  return (
    <div
      className={`mt-2 rounded-md border p-2 text-xs ${
        verdict.passed
          ? "border-green-400/60 bg-green-50 dark:border-green-900 dark:bg-green-950/30"
          : "border-amber-400/60 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30"
      }`}
      role="status"
    >
      <p className="flex items-center gap-1.5 font-medium">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        {verdict.passed ? "Validation passed" : "Needs more work"} — quality {pct(verdict.score)} (needs {pct(verdict.threshold)})
      </p>
      <p className="mt-1 text-muted-foreground">
        {verdict.passed
          ? "The document is now ready for review. It was not changed."
          : "The document was not changed and is still a draft. Fix the points below (or use Improve), then validate again."}
      </p>
      {groups.map(([title, items]) =>
        items.length > 0 ? (
          <div key={title} className="mt-1.5">
            <p className="font-medium">{title}</p>
            <ul className="list-disc space-y-0.5 pl-4">
              {items.map((i, n) => (
                <li key={n}>{i}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </div>
  );
}
