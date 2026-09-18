/**
 * Bridge configuration — loaded once from the developer's own environment.
 * BRIDGE_ENABLED defaults to false: "Keep the bridge feature disabled by
 * default" (Phase 13's own literal requirement).
 */

export interface BridgeConfig {
  enabled: boolean;
  apiBaseUrl: string;
  deviceClientId: string;
  workspaceRoot: string;
  approvedRuntimesJson: string | undefined;
  jobStateDir: string;
  /** Hard ceiling on how long a fetched job credential remains usable, independent of anything the server claims. */
  jobCredentialTtlSeconds: number;
}

function envFlag(env: NodeJS.ProcessEnv, name: string, fallback: boolean): boolean {
  const raw = env[name];
  if (raw === undefined) return fallback;
  return raw === "1" || raw.toLowerCase() === "true";
}

export function loadBridgeConfig(env: NodeJS.ProcessEnv = process.env): BridgeConfig {
  return {
    enabled: envFlag(env, "BRIDGE_ENABLED", false),
    apiBaseUrl: env.BRIDGE_API_BASE_URL ?? "http://localhost:8000",
    deviceClientId: env.BRIDGE_DEVICE_CLIENT_ID ?? "agentic-sdlc-bridge-cli",
    workspaceRoot: env.BRIDGE_WORKSPACE_ROOT ?? process.cwd(),
    approvedRuntimesJson: env.BRIDGE_APPROVED_RUNTIMES_JSON,
    jobStateDir: env.BRIDGE_JOB_STATE_DIR ?? ".agentic-bridge/jobs",
    jobCredentialTtlSeconds: Number(env.BRIDGE_JOB_CREDENTIAL_TTL_SECONDS ?? "3600"),
  };
}
