/**
 * Command and path guardrails — this phase's "Deny destructive commands,"
 * "Allow only approved repository paths," and "Deny external directories"
 * requirements, applied BEFORE anything reaches OpenCode's own
 * config.permission.bash/external_directory gate (defense in depth: this
 * runner never relies solely on OpenCode's own permission engine, since
 * that engine's `bash: "allow"` per-pattern map is a per-project author's
 * config, not something this runner should trust unexamined — see
 * permissions.ts's own docstring for how the two layers compose).
 */

import { minimatch } from "./minimatch.js";

/** A fixed, closed denylist of command shapes that are never acceptable
 * regardless of ProjectExecutionProfile.denied_command_patterns — mirrors
 * the "always wins on overlap" fail-closed posture ScopePolicy/ToolPolicy
 * already use on the Python side (Phase 01's policies.py). */
const ALWAYS_DENIED_COMMAND_PATTERNS: RegExp[] = [
  /\brm\s+-rf\s+\/(?:\s|$)/, // rm -rf / (and /-prefixed roots)
  /\bmkfs\b/,
  /\bdd\s+.*of=\/dev\//,
  /:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:/, // fork bomb
  /\bshutdown\b|\breboot\b|\bhalt\b/,
  /\bchmod\s+-R\s+777\s+\//,
  /\bcurl\b.*\|\s*(sh|bash)\b/, // pipe-to-shell
  /\bwget\b.*\|\s*(sh|bash)\b/,
  /\b(iptables|ufw)\b.*flush/,
  /\bsudo\b/, // this runner enforces non-root execution — sudo would defeat that
  /\bgit\s+push\b/, // "Never push code in this phase"
];

export class CommandDeniedError extends Error {}
export class PathDeniedError extends Error {}

export function assertCommandAllowed(command: string, deniedPatterns: string[]): void {
  for (const pattern of ALWAYS_DENIED_COMMAND_PATTERNS) {
    if (pattern.test(command)) {
      throw new CommandDeniedError(`Command denied by fixed platform denylist: ${describePattern(pattern)}`);
    }
  }
  for (const raw of deniedPatterns) {
    if (minimatch(command, raw) || command.includes(raw)) {
      throw new CommandDeniedError(`Command denied by ProjectExecutionProfile.denied_command_patterns: ${raw}`);
    }
  }
}

function describePattern(pattern: RegExp): string {
  return pattern.source.length > 60 ? pattern.source.slice(0, 60) + "…" : pattern.source;
}

/**
 * Fail-closed path check: a path is allowed only if it matches
 * `allowedPaths` (or `allowedPaths` is empty AND deny_by_default is false)
 * and does NOT match `deniedPaths`, and is never an absolute path or a
 * path that escapes the workspace root via `..` — "no host filesystem
 * access" and "deny external directories" both compile down to this.
 */
export function assertPathAllowed(
  relativePath: string,
  opts: { allowedPaths: string[]; deniedPaths: string[]; denyByDefault: boolean },
): void {
  if (relativePath.startsWith("/") || relativePath.startsWith("~") || /^[A-Za-z]:[\\/]/.test(relativePath)) {
    throw new PathDeniedError(`Absolute/external path denied: ${relativePath}`);
  }
  const normalized = relativePath.replace(/\\/g, "/");
  if (normalized.split("/").includes("..")) {
    throw new PathDeniedError(`Path escapes workspace via '..': ${relativePath}`);
  }
  for (const denied of opts.deniedPaths) {
    if (minimatch(normalized, denied)) {
      throw new PathDeniedError(`Path denied by denied_paths pattern '${denied}': ${relativePath}`);
    }
  }
  if (opts.allowedPaths.length > 0) {
    const matchesAllow = opts.allowedPaths.some((pattern) => minimatch(normalized, pattern));
    if (!matchesAllow) {
      throw new PathDeniedError(`Path does not match any allowed_paths pattern: ${relativePath}`);
    }
    return;
  }
  if (opts.denyByDefault) {
    throw new PathDeniedError(`No allowed_paths configured and deny_by_default is true: ${relativePath}`);
  }
}
