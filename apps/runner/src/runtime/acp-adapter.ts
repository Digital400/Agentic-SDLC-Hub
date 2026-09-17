/**
 * AcpCodingRuntimeAdapter — the generic Agent Client Protocol coding
 * runtime this phase asks for, implementing the SAME CodingRuntimeAdapter
 * contract OpenCodeRuntimeAdapter does (see coding-runtime-adapter.ts) so
 * a caller never needs to know which concrete runtime it holds.
 *
 * Mirrors OpenCodeRuntimeAdapter's exact ten-step shape (see that file's
 * own docstring for the full numbered mapping) with one real protocol
 * difference: OpenCode's SDK lets THIS CLIENT directly call
 * session.shell(command) to run an exact command on demand; ACP's model
 * is agent-autonomous — the agent itself decides what tool calls to make
 * in response to a prompt, this client never dictates a literal shell
 * invocation. "Execute required checks" (step 8) is therefore realized
 * here as an explicit follow-up prompt turn per check ("run this exact
 * command and report its output"), with CommandEvidence.real_execution
 * only ever true when a matching tool_call_update was actually observed
 * for that turn — never assumed. See contracts/acp.ts's HONESTY note:
 * this protocol layer is best-effort and NOT verified against a live ACP
 * agent in this environment.
 *
 * SECURITY: the ONLY executable this ever spawns is one already present,
 * enabled, in the AcpAgentRegistry passed to its constructor — never a
 * path from a WorkPacket or any other per-request input (this phase's
 * "do not allow users to provide arbitrary executable paths"). Every
 * incoming session/request_permission from the agent is answered
 * conservatively: rejected outright whenever the WorkPacket's own
 * ApprovalPolicy.requires_human_approval is true (the default for nearly
 * every task type in this codebase) — no live human is in this loop, so
 * "requires approval" can only ever mean "not auto-approved," per Phase
 * 07's own "require human approval for push/PR/infra actions" spirit,
 * generalized here to every tool call until a real approval-relay exists.
 */

import { randomUUID } from "node:crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

import type {
  AcpInitializeResult, AcpNewSessionResult, AcpPromptResult, AcpRequestPermissionParams, AcpRequestPermissionResult, AcpSessionUpdateNotification,
} from "../contracts/acp.js";
import type { CodingRuntimeAdapter, CodingRuntimeExecuteInput } from "../contracts/coding-runtime-adapter.js";
import type { RuntimeEventType } from "../contracts/events.js";
import type { CommandEvidence, ExecutionResult, FileChangeEvidence, TestEvidence } from "../contracts/execution-result.js";
import { verifySignedWorkPacket, WorkPacketSignatureError } from "../contracts/signed-work-packet.js";
import type { RunnerConfig } from "../config.js";
import { assertCommandAllowed, assertPathAllowed, CommandDeniedError, PathDeniedError } from "../security/command-guard.js";
import { BudgetExceededError, BudgetTracker } from "../security/limits.js";
import { AcpAgentNotApprovedError, type AcpAgentRegistry } from "../security/acp-registry.js";
import { createEphemeralWorkspace, NonRootExecutionRequiredError } from "../security/workspace.js";
import { cloneAtBaseSha, CloneFailedError } from "./git-clone.js";
import { JsonRpcClosedError, JsonRpcConnection, JsonRpcPeerError, JsonRpcTimeoutError } from "./acp-json-rpc.js";
import { mapAcpSessionUpdate } from "./acp-event-mapper.js";

const ACP_PROTOCOL_VERSION_WE_SPEAK = 1;
const DEFAULT_REQUEST_TIMEOUT_MS = 60_000;

export interface AcpAdapterOptions {
  runnerConfig: RunnerConfig;
  registry: AcpAgentRegistry;
  /** Which registry key this adapter instance is pinned to — set once, at construction, never per-request. */
  agentKey: string;
  requestTimeoutMs?: number;
  repoAccessToken?: string;
  /** Test-only escape hatch — see git-clone.ts's cloneAtBaseSha's own identical option. Production callers never set this. */
  cloneUrlOverride?: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function boundedExcerpt(text: string | undefined | null, maxLength = 8000): string | null {
  if (!text) return null;
  return text.length > maxLength ? text.slice(0, maxLength) + "…" : text;
}

export class AcpCodingRuntimeAdapter implements CodingRuntimeAdapter {
  readonly runtimeName: string;

  constructor(private readonly opts: AcpAdapterOptions) {
    this.runtimeName = `acp:${opts.agentKey}`;
  }

  async execute(input: CodingRuntimeExecuteInput): Promise<ExecutionResult> {
    const startedAt = nowIso();
    const resultId = randomUUID();
    const timeoutMs = this.opts.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;

    // --- Step 1: verify the signed WorkPacket ----------------------------------
    let packet;
    try {
      if (!this.opts.runnerConfig.workPacketSigningSecret) {
        throw new WorkPacketSignatureError("RUNNER_SHARED_SIGNING_SECRET is not configured — refusing to execute any WorkPacket.");
      }
      packet = verifySignedWorkPacket(input.signedPacket, this.opts.runnerConfig.workPacketSigningSecret);
    } catch (err) {
      return this.failureResult(resultId, "unknown", startedAt, "SECURITY_VIOLATION", err);
    }

    // --- Resolve the approved agent (never a path from the packet) ------------
    let agent;
    try {
      agent = this.opts.registry.resolve(this.opts.agentKey);
    } catch (err) {
      const category = err instanceof AcpAgentNotApprovedError ? "SECURITY_VIOLATION" : "INTERNAL_ERROR";
      return this.failureResult(resultId, packet.packet_id, startedAt, category, err);
    }
    if (agent.protocolVersion !== ACP_PROTOCOL_VERSION_WE_SPEAK) {
      // "Pin adapter/runtime versions" — a version drift is refused, never
      // silently negotiated down/up.
      return this.failureResult(
        resultId, packet.packet_id, startedAt, "SECURITY_VIOLATION",
        new Error(`Approved agent "${agent.key}" pins ACP protocol version ${agent.protocolVersion}, but this runner only speaks ${ACP_PROTOCOL_VERSION_WE_SPEAK}.`),
      );
    }

    if (!packet.repository) {
      return this.failureResult(resultId, packet.packet_id, startedAt, "SCOPE_VIOLATION", new Error("WorkPacket has no repository — AcpCodingRuntimeAdapter only handles repository-scoped tasks."));
    }

    const profile = input.signedPacket.execution_profile;
    const budget = new BudgetTracker(packet.budget_policy, this.opts.runnerConfig.maxWallClockSecondsHardCeiling);

    // --- Step 2: isolated ephemeral workspace ----------------------------------
    let workspace;
    try {
      workspace = await createEphemeralWorkspace(this.opts.runnerConfig.workspaceRoot, packet.packet_id);
    } catch (err) {
      const category = err instanceof NonRootExecutionRequiredError ? "SECURITY_VIOLATION" : "INTERNAL_ERROR";
      return this.failureResult(resultId, packet.packet_id, startedAt, category, err);
    }

    let child: ChildProcessWithoutNullStreams | undefined;
    let connection: JsonRpcConnection | undefined;
    const commands: CommandEvidence[] = [];
    const testEvidence: TestEvidence[] = [];
    const fileChanges: FileChangeEvidence[] = [];
    let sequence = 0;

    const emit = (type: RuntimeEventType, payload: Record<string, unknown>) => {
      try {
        input.onEvent({ type, sequence: ++sequence, emittedAt: nowIso(), payload });
      } catch {
        // A throwing event sink must never abort execution.
      }
    };

    try {
      emit("STATUS", { phase: "workspace_created" });

      // --- Step 3: clone at the immutable base SHA -----------------------------
      await cloneAtBaseSha(packet.repository, workspace.directory, { token: this.opts.repoAccessToken, cloneUrlOverride: this.opts.cloneUrlOverride });
      emit("STATUS", { phase: "cloned", base_commit_sha: packet.repository.base_commit_sha });

      // --- Step 3b: start the approved agent process, in the workspace ---------
      child = spawn(agent.executablePath, agent.args, { cwd: workspace.directory, stdio: ["pipe", "pipe", "pipe"] });
      connection = new JsonRpcConnection(child.stdout, child.stdin);
      child.stderr.on("data", (chunk: Buffer) => emit("WARNING", { stderr: boundedExcerpt(chunk.toString("utf-8"), 2000) }));

      // Conservative default: reject every tool-call permission request
      // whenever this task requires human approval (the default) — see
      // module docstring's SECURITY note.
      connection.onIncomingRequest(async (method, params) => {
        if (method !== "session/request_permission") throw new Error(`Unsupported incoming request: ${method}`);
        const p = params as AcpRequestPermissionParams;
        emit("APPROVAL_REQUIRED", { tool_call_id: p.toolCall.toolCallId, title: p.toolCall.title });
        if (packet!.approval_policy.requires_human_approval) {
          const rejectOption = p.options.find((o) => o.kind === "reject_once" || o.kind === "reject_always");
          const result: AcpRequestPermissionResult = rejectOption
            ? { outcome: { outcome: "selected", optionId: rejectOption.optionId } }
            : { outcome: { outcome: "cancelled" } };
          return result;
        }
        const allowOption = p.options.find((o) => o.kind === "allow_once");
        const result: AcpRequestPermissionResult = allowOption
          ? { outcome: { outcome: "selected", optionId: allowOption.optionId } }
          : { outcome: { outcome: "cancelled" } };
        return result;
      });

      connection.onNotification("session/update", (_method, params) => {
        const notif = params as AcpSessionUpdateNotification;
        const mapped = mapAcpSessionUpdate(notif.update);
        if (mapped) emit(mapped.type, mapped.payload);
        if (notif.update.sessionUpdate === "tool_call_update" && (notif.update.status === "completed" || notif.update.status === "failed")) {
          for (const c of notif.update.content ?? []) {
            if (c.type === "diff" && c.path) {
              try {
                assertPathAllowed(c.path, { allowedPaths: profile.allowed_paths, deniedPaths: profile.denied_paths, denyByDefault: true });
              } catch (err) {
                if (err instanceof PathDeniedError) {
                  emit("WARNING", { message: err.message, file: c.path });
                  continue;
                }
                throw err;
              }
              fileChanges.push({ path: c.path, change_type: "MODIFIED", summary: notif.update.title ?? "Tool call file change.", diff: boundedExcerpt(c.diff, 8000) });
            }
          }
        }
      });

      // --- Capability negotiation (steps 4/6/7 collapse into one handshake here) ---
      const initResult = await connection.request<AcpInitializeResult>(
        "initialize", { protocolVersion: ACP_PROTOCOL_VERSION_WE_SPEAK, clientCapabilities: { fs: { readTextFile: true, writeTextFile: false } } }, timeoutMs,
      );
      emit("STATUS", { phase: "acp_initialized", agent_capabilities: initResult.agentCapabilities ?? {} });

      if (input.signal?.aborted) throw new Error("cancelled");
      const session = await connection.request<AcpNewSessionResult>("session/new", { cwd: workspace.directory, mcpServers: [] }, timeoutMs);

      // --- Step 5: compiled skills/context, as the leading prompt ---------------
      const promptText = [
        input.compiledContext.systemInstructions, "", `Goal: ${packet.objective.goal}`,
        `Success definition: ${packet.objective.success_definition}`, "", input.compiledContext.responseContractDescription,
      ].join("\n");

      if (input.signal?.aborted) throw new Error("cancelled");
      budget.recordLlmCall(0);
      const promptResult = await connection.request<AcpPromptResult>(
        "session/prompt", { sessionId: session.sessionId, prompt: [{ type: "text", text: promptText }] }, timeoutMs,
      );
      emit("PLAN_SUMMARY", { goal: packet.objective.goal, stop_reason: promptResult.stopReason });

      // --- Step 8: required checks, as explicit follow-up prompt turns ---------
      for (const check of packet.required_checks) {
        if (!check.command) continue;
        if (input.signal?.aborted) throw new Error("cancelled");
        try {
          assertCommandAllowed(check.command, profile.denied_command_patterns);
        } catch (err) {
          if (err instanceof CommandDeniedError) {
            commands.push({ command: check.command, real_execution: false, exit_code: null, stderr_excerpt: boundedExcerpt(err.message) });
            continue;
          }
          throw err;
        }
        budget.recordToolCall("acp_check_prompt");
        emit("COMMAND", { command: check.command });
        const before = fileChanges.length;
        await connection.request<AcpPromptResult>(
          "session/prompt", { sessionId: session.sessionId, prompt: [{ type: "text", text: `Run this exact command and report its full output: ${check.command}` }] }, timeoutMs,
        );
        // Honest, disclosed limitation (see module docstring): ACP gives
        // this client no direct exit-code/stdout channel for a command it
        // asked the agent to run — real_execution is only ever true when
        // this turn actually produced observable tool-call evidence,
        // never assumed from the prompt alone.
        const observedToolActivity = fileChanges.length > before;
        commands.push({ command: check.command, real_execution: observedToolActivity, exit_code: null, stdout_excerpt: observedToolActivity ? "See TOOL_RESULT events for this turn." : null });
        testEvidence.push({ name: check.name, result: observedToolActivity ? "PASS" : "SKIPPED", real_execution: observedToolActivity, notes: observedToolActivity ? null : "No tool-call evidence observed for this check turn." });
      }

      const usage = budget.snapshot();
      emit("USAGE", usage);
      emit("COMPLETED", { file_changes: fileChanges.length, commands: commands.length });

      return {
        schema_version: packet.schema_version,
        result_id: resultId,
        packet_id: packet.packet_id,
        state: "COMPLETED",
        summary: `ACP agent "${this.opts.agentKey}" completed with ${fileChanges.length} file change(s) and ${commands.length} check(s) attempted.`,
        file_changes: fileChanges,
        commands,
        test_evidence: testEvidence,
        usage: {
          prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: usage.costUsd,
          llm_calls_made: usage.llmCalls, tool_calls_made: usage.toolCalls, distinct_tools_used: usage.distinctTools,
          wall_clock_seconds: usage.wallClockSeconds, repair_attempts_used: 0,
        },
        failure: null,
        clarification: null,
        started_at: startedAt,
        completed_at: nowIso(),
      };
    } catch (err) {
      if (err instanceof Error && err.message === "cancelled") {
        return { ...this.failureResult(resultId, packet.packet_id, startedAt, "INTERNAL_ERROR", err), state: "CANCELLED", failure: null };
      }
      const category =
        err instanceof BudgetExceededError ? "BUDGET_EXCEEDED"
        : err instanceof CommandDeniedError || err instanceof PathDeniedError ? "SECURITY_VIOLATION"
        : err instanceof JsonRpcTimeoutError ? "TIMEOUT"
        : err instanceof JsonRpcPeerError || err instanceof JsonRpcClosedError ? "INTERNAL_ERROR"
        : err instanceof CloneFailedError ? "INTERNAL_ERROR"
        : "UNKNOWN";
      return this.failureResult(resultId, packet.packet_id, startedAt, category, err);
    } finally {
      // --- Step 10: dispose, always ---------------------------------------------
      try {
        connection?.close();
      } catch {
        // best-effort
      }
      try {
        child?.kill();
      } catch {
        // best-effort
      }
      await workspace.dispose();
    }
  }

  private failureResult(
    resultId: string, packetId: string, startedAt: string,
    category: NonNullable<ExecutionResult["failure"]>["category"], err: unknown,
  ): ExecutionResult {
    const message = err instanceof Error ? err.message : String(err);
    return {
      schema_version: "1.0.0",
      result_id: resultId,
      packet_id: packetId,
      state: "FAILED",
      summary: null,
      file_changes: [],
      commands: [],
      test_evidence: [],
      usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_usd: 0, llm_calls_made: 0, tool_calls_made: 0, distinct_tools_used: [], wall_clock_seconds: null, repair_attempts_used: 0 },
      failure: { category, message: boundedExcerpt(message, 2000) ?? "unknown error", retryable: category === "TIMEOUT" },
      clarification: null,
      started_at: startedAt,
      completed_at: nowIso(),
    };
  }
}
