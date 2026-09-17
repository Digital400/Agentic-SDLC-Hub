/**
 * Builds the real @opencode-ai/sdk `Config` object OpenCode is started
 * with — this phase's "Run OpenCode with explicit permissions" step, and
 * the concrete mechanism for most of its security requirements:
 *
 *   - "Network denied by default"      -> permission.webfetch = "deny"
 *   - "Deny external directories"      -> permission.external_directory = "deny"
 *   - "Deny unapproved MCP tools"      -> mcp: {} (empty — no MCP servers configured at all in this phase)
 *   - "Deny destructive commands"      -> permission.bash is a per-pattern map, "ask"/"deny" for anything
 *                                          not an explicitly approved ProjectExecutionProfile command
 *   - "Route model access through the company model gateway" -> provider.<gateway>.options.baseURL/apiKey
 *   - "No platform secret mounts"      -> no `env` values are ever copied from this process's own
 *                                          environment into the Config; only ProjectExecutionProfile.
 *                                          environment_variable_names (NAMES ONLY, never values — same
 *                                          HARD RULE as the Python model column) are referenced.
 *
 * `permission.bash` is never set to a bare "allow" — every command is
 * either an explicit allow for one of ProjectExecutionProfile's own
 * approved commands, or "ask"/"deny" — this runner does not run
 * unattended with a blanket bash allow regardless of what a WorkPacket's
 * own ToolPolicy claims (defense in depth against a compromised/incorrect
 * upstream packet).
 */

import type { Config } from "@opencode-ai/sdk";

import type { ProjectExecutionProfile, ToolPolicy } from "../contracts/work-packet.js";
import type { RunnerConfig } from "../config.js";

const COMPANY_GATEWAY_PROVIDER_ID = "company-model-gateway";

export class ModelGatewayNotConfiguredError extends Error {}

export function buildOpenCodeConfig(opts: {
  runnerConfig: RunnerConfig;
  profile: ProjectExecutionProfile;
  toolPolicy: ToolPolicy;
  modelAlias: string;
}): Config {
  if (!opts.runnerConfig.modelGatewayBaseUrl) {
    // Fail-closed: never let OpenCode fall back to a directly-configured
    // public provider — every model call must route through the company
    // gateway (Phase 05), or this runner refuses to start OpenCode at all.
    throw new ModelGatewayNotConfiguredError(
      "MODEL_GATEWAY_BASE_URL is not configured — refusing to start OpenCode without routing model access through the company model gateway.",
    );
  }

  const approvedCommands = [
    opts.profile.install_command,
    opts.profile.lint_command,
    opts.profile.format_check_command,
    opts.profile.type_check_command,
    opts.profile.unit_test_command,
    opts.profile.integration_test_command,
    opts.profile.build_command,
    ...opts.profile.approved_security_scan_commands,
  ].filter((c): c is string => Boolean(c));

  const bashPermission: Record<string, "ask" | "allow" | "deny"> = { "*": "deny" };
  for (const command of approvedCommands) {
    bashPermission[command] = "allow";
  }
  for (const denied of opts.profile.denied_command_patterns) {
    bashPermission[denied] = "deny";
  }

  return {
    // Fixed set of MCP servers this phase approves: none. Any MCP tool a
    // project might want approved is a future phase's addition, driven by
    // an explicit, reviewed allowlist — never inferred from the packet.
    mcp: {},
    permission: {
      edit: opts.toolPolicy.allowed_tools.includes("file_write") ? "allow" : "ask",
      bash: bashPermission,
      webfetch: "deny",
      external_directory: "deny",
      doom_loop: "deny",
    },
    provider: {
      [COMPANY_GATEWAY_PROVIDER_ID]: {
        npm: "@ai-sdk/openai-compatible",
        options: {
          baseURL: opts.runnerConfig.modelGatewayBaseUrl,
          apiKey: opts.runnerConfig.modelGatewayApiKey,
        },
        models: {
          [opts.modelAlias]: {},
        },
      },
    },
    model: `${COMPANY_GATEWAY_PROVIDER_ID}/${opts.modelAlias}`,
    autoupdate: false,
    share: "disabled",
  };
}
