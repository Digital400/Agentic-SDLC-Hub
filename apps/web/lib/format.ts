export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/**
 * Short relative time for activity feeds (e.g. "2h ago", "3d ago").
 * Falls back to a plain date once it's more than a week old.
 */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();
  const diffMinutes = Math.round(diffMs / 60_000);

  if (diffMinutes < 1) return "just now";
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.round(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return formatDate(iso);
}

/** "solution_options_doc" -> "Solution Options Doc". Used for artifact types, actions, and node keys alike. */
export function formatSnakeCase(value: string): string {
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function formatStageLabel(nodeKey: string): string {
  return formatSnakeCase(nodeKey);
}

/** Seconds -> a short human duration, e.g. "0.4s", "12s", "3m 5s". */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remainingSeconds}s`;
}

export function formatCost(cost: number | null): string {
  if (cost === null) return "—";
  return `$${cost.toFixed(cost < 1 ? 4 : 2)}`;
}

export function formatPercent(rate: number | null): string {
  if (rate === null) return "—";
  return `${Math.round(rate * 100)}%`;
}

// Matches apps/api/app/models/enums.py's UserRole values exactly — a
// couple of them ("BA", "QA") are acronyms and stay as-is rather than
// title-casing into something wrong ("Ba", "Qa").
const ROLE_LABEL_OVERRIDES: Record<string, string> = { BA: "BA", QA: "QA", DEVOPS: "DevOps" };

export function formatRoleLabel(role: string): string {
  return ROLE_LABEL_OVERRIDES[role] ?? formatSnakeCase(role.toLowerCase());
}
