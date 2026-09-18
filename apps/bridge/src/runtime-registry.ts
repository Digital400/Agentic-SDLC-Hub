/**
 * Admin-approved local runtime registry — same structural guarantee as
 * apps/runner's security/acp-registry.ts ("do not allow users to provide
 * arbitrary executable paths"), reimplemented here rather than imported
 * so this package stays genuinely "separately installable" (Phase 13's
 * own requirement) without depending on apps/runner's own non-Yarn-
 * integrated install.
 */

export interface ApprovedLocalRuntime {
  key: string;
  executablePath: string;
  args: string[];
  protocol: "acp" | "native";
  enabled: boolean;
}

export class LocalRuntimeNotApprovedError extends Error {}

export function parseApprovedLocalRuntimes(json: string | undefined): ApprovedLocalRuntime[] {
  if (!json) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const runtimes: ApprovedLocalRuntime[] = [];
  for (const entry of parsed) {
    if (
      typeof entry === "object" && entry !== null &&
      typeof (entry as Record<string, unknown>).key === "string" &&
      typeof (entry as Record<string, unknown>).executablePath === "string"
    ) {
      const e = entry as Record<string, unknown>;
      runtimes.push({
        key: e.key as string,
        executablePath: e.executablePath as string,
        args: Array.isArray(e.args) ? (e.args as string[]) : [],
        protocol: e.protocol === "native" ? "native" : "acp",
        enabled: e.enabled !== false,
      });
    }
  }
  return runtimes;
}

export class LocalRuntimeRegistry {
  private readonly runtimes = new Map<string, ApprovedLocalRuntime>();

  constructor(runtimes: ApprovedLocalRuntime[]) {
    for (const runtime of runtimes) this.runtimes.set(runtime.key, runtime);
  }

  list(): ApprovedLocalRuntime[] {
    return [...this.runtimes.values()];
  }

  resolve(key: string): ApprovedLocalRuntime {
    const runtime = this.runtimes.get(key);
    if (!runtime || !runtime.enabled) {
      throw new LocalRuntimeNotApprovedError(`"${key}" is not an approved, enabled local runtime.`);
    }
    return runtime;
  }
}
