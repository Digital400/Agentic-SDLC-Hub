/**
 * Environment configuration — mirrors this codebase's existing convention
 * (apps/api/app/core/config.py's Settings) of one typed, centrally-read
 * config object rather than scattered process.env reads.
 */

export interface RunnerConfig {
  /** Master feature flag — "Register OpenCode as an available but feature-flagged runtime." Defaults false: registered, not runnable, until explicitly enabled. Mirrors Settings.EXTERNAL_CODING_RUNTIMES_ENABLED's same fail-closed default (Phase 07). */
  openCodeRuntimeEnabled: boolean;
  /** Shared HMAC secret for SignedWorkPacket verification — required whenever openCodeRuntimeEnabled is true. */
  workPacketSigningSecret: string | undefined;
  /** OpenAI-compatible base URL for the company model gateway (Phase 05's ModelGateway/PolicyDrivenRouter) OpenCode's custom provider should route through. Required whenever openCodeRuntimeEnabled is true — never falls back to a public provider (fail-closed: no company data reaches an unrouted model endpoint). */
  modelGatewayBaseUrl: string | undefined;
  modelGatewayApiKey: string | undefined;
  /** Root directory ephemeral workspaces are created under — never the process's own cwd or any path outside this root. */
  workspaceRoot: string;
  /** Hard wall-clock ceiling (seconds) applied even if a WorkPacket's own BudgetPolicy.max_wall_clock_seconds is absent or larger. */
  maxWallClockSecondsHardCeiling: number;
}

function envFlag(env: NodeJS.ProcessEnv, name: string, fallback: boolean): boolean {
  const raw = env[name];
  if (raw === undefined) return fallback;
  return raw === "1" || raw.toLowerCase() === "true";
}

export function loadRunnerConfig(env: NodeJS.ProcessEnv = process.env): RunnerConfig {
  return {
    openCodeRuntimeEnabled: envFlag(env, "OPENCODE_RUNTIME_ENABLED", false),
    workPacketSigningSecret: env.RUNNER_SHARED_SIGNING_SECRET,
    modelGatewayBaseUrl: env.MODEL_GATEWAY_BASE_URL,
    modelGatewayApiKey: env.MODEL_GATEWAY_API_KEY,
    workspaceRoot: env.RUNNER_WORKSPACE_ROOT ?? "/tmp/opencode-runner-workspaces",
    maxWallClockSecondsHardCeiling: Number(env.RUNNER_MAX_WALL_CLOCK_SECONDS_HARD_CEILING ?? "1800"),
  };
}
