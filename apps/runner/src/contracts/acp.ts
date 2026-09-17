/**
 * Agent Client Protocol (ACP) — a minimal, best-effort TypeScript mirror
 * of the publicly documented protocol (agentclientprotocol.com): a
 * JSON-RPC 2.0 exchange between a CLIENT (this runner) and an AGENT
 * (an external, admin-approved executable), used by editors like Zed to
 * talk to coding agents in a vendor-neutral way — exactly the role Phase
 * 11 wants a "generic ACP coding runtime" to play here.
 *
 * HONESTY (Common Instruction rule 9 — never report simulated execution
 * as real): this environment has no network access to the current spec
 * and no real ACP-compliant agent binary to validate against. The method/
 * notification names and payload shapes below are modeled on the
 * protocol's publicly known design as of this codebase's own knowledge
 * cutoff, not verified against a live reference implementation — see
 * docs/architecture/acp-runtime.md's own "Remaining risks" section. What
 * IS genuinely verified (real tests, no live agent needed): the JSON-RPC
 * wire framing itself (acp-json-rpc.ts) and this adapter's own security
 * boundaries (registry-only executables, path validation, timeouts).
 * AcpCodingRuntimeAdapter is registered but NEVER enabled by default
 * (Settings.ACP_RUNTIME_ENABLED) until a real security review against an
 * actual ACP agent happens — see Phase 11's own "keep ACP disabled by
 * default until security review" instruction.
 */

export interface AcpClientCapabilities {
  fs: { readTextFile: boolean; writeTextFile: boolean };
}

export interface AcpAgentCapabilities {
  loadSession?: boolean;
  promptCapabilities?: Record<string, boolean>;
}

export interface AcpInitializeParams {
  protocolVersion: number;
  clientCapabilities: AcpClientCapabilities;
}

export interface AcpInitializeResult {
  protocolVersion: number;
  agentCapabilities?: AcpAgentCapabilities;
}

export interface AcpNewSessionParams {
  cwd: string;
  mcpServers: unknown[];
}

export interface AcpNewSessionResult {
  sessionId: string;
}

export type AcpContentBlock = { type: "text"; text: string };

export interface AcpPromptParams {
  sessionId: string;
  prompt: AcpContentBlock[];
}

export type AcpStopReason = "end_turn" | "max_tokens" | "refusal" | "cancelled";

export interface AcpPromptResult {
  stopReason: AcpStopReason;
}

// --- session/update notification variants -----------------------------------------------

export interface AcpAgentMessageChunk {
  sessionUpdate: "agent_message_chunk";
  content: AcpContentBlock;
}

export interface AcpAgentThoughtChunk {
  sessionUpdate: "agent_thought_chunk";
  content: AcpContentBlock;
}

export interface AcpToolCallUpdate {
  sessionUpdate: "tool_call" | "tool_call_update";
  toolCallId: string;
  title?: string;
  status?: "pending" | "in_progress" | "completed" | "failed";
  content?: Array<{ type: string; path?: string; diff?: string }>;
  rawOutput?: unknown;
}

export interface AcpPlanUpdate {
  sessionUpdate: "plan";
  entries: Array<{ content: string; status: "pending" | "in_progress" | "completed" }>;
}

export type AcpSessionUpdate = AcpAgentMessageChunk | AcpAgentThoughtChunk | AcpToolCallUpdate | AcpPlanUpdate;

export interface AcpSessionUpdateNotification {
  sessionId: string;
  update: AcpSessionUpdate;
}

// --- session/request_permission (agent -> client request) ------------------------------

export interface AcpPermissionOption {
  optionId: string;
  name: string;
  kind: "allow_once" | "allow_always" | "reject_once" | "reject_always";
}

export interface AcpRequestPermissionParams {
  sessionId: string;
  toolCall: { toolCallId: string; title?: string };
  options: AcpPermissionOption[];
}

export interface AcpRequestPermissionResult {
  outcome: { outcome: "selected"; optionId: string } | { outcome: "cancelled" };
}
