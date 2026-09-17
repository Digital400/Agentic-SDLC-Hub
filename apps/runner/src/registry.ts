/**
 * "Register OpenCode as an available but feature-flagged runtime." — a
 * small, explicit registry so a future caller (e.g. a Python-side HTTP
 * bridge from Phase 06's CeleryJobDispatcher, not built in this phase —
 * see docs/architecture/opencode-sandbox.md's "Remaining risks" section)
 * can discover which CodingRuntimeAdapters exist and whether each is
 * currently enabled, without importing OpenCodeRuntimeAdapter's concrete
 * module directly. Mirrors app/agent_runtime/capability.py's
 * RuntimeCapabilityManifest as the vendor-neutral "what can this runtime
 * do" declaration.
 */

import type { CodingRuntimeAdapter } from "./contracts/coding-runtime-adapter.js";
import { loadRunnerConfig, type RunnerConfig } from "./config.js";
import { OpenCodeRuntimeAdapter } from "./runtime/opencode-adapter.js";
import { AcpCodingRuntimeAdapter } from "./runtime/acp-adapter.js";
import { AcpAgentRegistry, parseApprovedAcpAgents } from "./security/acp-registry.js";

export interface RuntimeCapabilityManifest {
  schema_version: string;
  runtime_name: string;
  max_context_tokens: number;
  max_output_tokens: number;
  supported_tool_categories: string[];
  supports_structured_output: boolean;
  supports_streaming: boolean;
  response_formats: string[];
}

export interface RegisteredRuntime {
  enabled: boolean;
  capability: RuntimeCapabilityManifest;
  createAdapter: () => CodingRuntimeAdapter;
}

export function buildRuntimeRegistry(config: RunnerConfig = loadRunnerConfig()): Record<string, RegisteredRuntime> {
  const registry: Record<string, RegisteredRuntime> = {
    opencode: {
      // Registered unconditionally — availability is separate from
      // whether it may actually run (see `enabled`). A caller listing
      // registered runtimes must always see "opencode" even when disabled,
      // so operators can confirm the flag is what's gating it, not a
      // missing registration.
      enabled: config.openCodeRuntimeEnabled,
      capability: {
        schema_version: "1.0.0",
        runtime_name: "opencode",
        max_context_tokens: 200_000,
        max_output_tokens: 8_192,
        supported_tool_categories: ["file_read", "file_write", "shell_command"],
        supports_structured_output: false,
        supports_streaming: true,
        response_formats: ["opencode_session_transcript"],
      },
      createAdapter: () => new OpenCodeRuntimeAdapter({ runnerConfig: config }),
    },
  };

  // Phase 11: one registry entry per admin-approved ACP agent, named
  // "acp:<key>" — every one registered unconditionally (same reasoning as
  // opencode above), but `enabled` is false for ALL of them unless BOTH
  // config.acpRuntimeEnabled (the master ACP switch — "keep ACP disabled
  // by default until security review") AND that specific agent's own
  // `enabled` flag are true.
  const acpAgentRegistry = new AcpAgentRegistry(parseApprovedAcpAgents(config.acpApprovedAgentsJson));
  for (const agent of acpAgentRegistry.list()) {
    registry[`acp:${agent.key}`] = {
      enabled: config.acpRuntimeEnabled && agent.enabled,
      capability: {
        schema_version: "1.0.0",
        runtime_name: `acp:${agent.key}`,
        max_context_tokens: 128_000,
        max_output_tokens: 8_192,
        supported_tool_categories: ["file_read", "file_write", "shell_command"],
        supports_structured_output: false,
        supports_streaming: true,
        response_formats: ["acp_session_update_stream"],
      },
      createAdapter: () => new AcpCodingRuntimeAdapter({ runnerConfig: config, registry: acpAgentRegistry, agentKey: agent.key }),
    };
  }

  return registry;
}

export class RuntimeDisabledError extends Error {}
export class UnknownRuntimeError extends Error {}

export function getEnabledAdapter(name: string, config?: RunnerConfig): CodingRuntimeAdapter {
  const registry = buildRuntimeRegistry(config);
  const entry = registry[name];
  if (!entry) {
    throw new UnknownRuntimeError(`No runtime registered under the name '${name}'.`);
  }
  if (!entry.enabled) {
    throw new RuntimeDisabledError(
      `Runtime '${name}' is registered but disabled — set OPENCODE_RUNTIME_ENABLED=true (for 'opencode') or ACP_RUNTIME_ENABLED=true plus that agent's own "enabled" entry in ACP_APPROVED_AGENTS_JSON (for an 'acp:*' runtime) to enable it.`,
    );
  }
  return entry.createAdapter();
}
