import type { ApiAgentRun } from "@/lib/api";

export interface ValidationVerdict {
  passed: boolean;
  score: number;
  threshold: number;
  criticalIssues: string[];
  missingDetails: string[];
  suggestions: string[];
}

/** Reads a VALIDATE run's verdict (see apps/api/app/services/validate_action.py). */
export function readVerdict(run: ApiAgentRun): ValidationVerdict | null {
  const r = run.loop_validation_result as Record<string, unknown> | null;
  if (run.action !== "validate" || !r || typeof run.loop_quality_score !== "number") return null;
  const list = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  return {
    passed: r["passed"] === true,
    score: run.loop_quality_score,
    threshold: run.loop_quality_threshold ?? 0.8,
    criticalIssues: list(r["critical_issues"]),
    missingDetails: list(r["missing_details"]),
    suggestions: list(r["suggestions"]),
  };
}
